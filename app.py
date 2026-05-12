"""FastAPI web app for browsing the local Wikipedia SQLite database.

Serves three things:
- ``GET /`` — the single-page UI (search box + result/article containers).
- ``GET /search?q=...`` — an HTML fragment listing matching article titles,
  intended to be swapped into the page by HTMX.
- ``GET /article/{title}`` — an HTML fragment with the article rendered
  from wikitext to HTML.

The app reads from the SQLite database produced by ``parse.cli`` and uses
``render.convert_wikitext_to_html`` for conversion. The database path can be
overridden with the ``WIKI_DB`` environment variable, which is what the tests
use to point at a temporary fixture database.
"""
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
from contextlib import asynccontextmanager
from urllib.parse import quote
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import db as wiki_db
import embed_jobs
import jobs as refresh_jobs
from paths import BASE_DIR, DEFAULT_WIKI, JOBS_DB, KNOWN_WIKIS, db_path_for, rag_db_path_for
from rag.embed import embed_one as rag_embed_one
from rag.links import extract_article_links
from rag.schema import connect_rag
from render import convert_wikitext_to_html

DEFAULT_DB = db_path_for(DEFAULT_WIKI)
_WIKI_LABELS = {"enwiki": "EnWiki", "simplewiki": "SimpleWiki"}

# Cap search results so the dropdown stays manageable and the LIKE scan
# can stop early.
SEARCH_LIMIT = 20

# Cap redirect-chain following so a cycle can't hang the request. MediaWiki's
# own limit is 5 hops; matching that is conservative.
REDIRECT_MAX_HOPS = 5


def _recover_from_crash() -> None:
    """Clean up state left behind by a worker that died mid-flight.

    Runs once at app startup (via the lifespan hook). Two responsibilities:
      1. Mark any jobs stuck in non-terminal status as 'failed' so the UI
         stops showing perpetual progress and new requests aren't blocked
         by the zombie row in get_active_job().
      2. For any wiki whose FTS index was marked dirty by a crashed refresh,
         rebuild the FTS index synchronously before serving requests so
         search results don't silently return stale titles.
    """
    conn = refresh_jobs.connect_jobs(JOBS_DB)
    try:
        n_refresh = refresh_jobs.clear_orphaned_jobs(conn)
        if n_refresh:
            print(
                f"[startup] cleared {n_refresh} orphaned refresh job(s)",
                file=sys.stderr, flush=True,
            )
        dirty_wikis = refresh_jobs.get_fts_dirty_wikis(conn)
    finally:
        conn.close()

    embed_conn = embed_jobs.connect_embed_jobs(JOBS_DB)
    try:
        n_embed = embed_jobs.clear_orphaned_jobs(embed_conn)
        if n_embed:
            print(
                f"[startup] cleared {n_embed} orphaned embed job(s)",
                file=sys.stderr, flush=True,
            )
    finally:
        embed_conn.close()

    for wiki in dirty_wikis:
        db_path = db_path_for(wiki)
        if not db_path.exists():
            # The wiki DB was deleted while the flag was set. Clear the flag
            # so we don't try to rebuild on every startup.
            conn = refresh_jobs.connect_jobs(JOBS_DB)
            try:
                refresh_jobs.set_fts_dirty(conn, wiki, False)
            finally:
                conn.close()
            continue

        print(
            f"[startup] FTS index for {wiki} is dirty — rebuilding…",
            file=sys.stderr, flush=True,
        )
        wiki_conn = sqlite3.connect(db_path)
        try:
            wiki_conn.execute(
                "INSERT INTO articles_fts(articles_fts) VALUES('rebuild')"
            )
            wiki_conn.commit()
        finally:
            wiki_conn.close()

        conn = refresh_jobs.connect_jobs(JOBS_DB)
        try:
            refresh_jobs.set_fts_dirty(conn, wiki, False)
        finally:
            conn.close()
        print(f"[startup] FTS rebuild complete for {wiki}", file=sys.stderr, flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _recover_from_crash()
    yield


app = FastAPI(title="Local Wikipedia", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def _active_wiki(request: Request) -> str:
    """Return the wiki the user has selected, defaulting to ``DEFAULT_WIKI``."""
    return request.cookies.get("wiki_pref", DEFAULT_WIKI)


def _db_path(request: Request) -> pathlib.Path:
    """Return the SQLite database path.

    ``WIKI_DB`` env var wins (used by tests); otherwise the ``wiki_pref``
    cookie determines which wiki database to open.
    """
    if "WIKI_DB" in os.environ:
        return pathlib.Path(os.environ["WIKI_DB"])
    return db_path_for(_active_wiki(request))


def _connect(request: Request) -> sqlite3.Connection:
    """Open a per-request SQLite connection with row-dict access.

    Returns:
        A new ``sqlite3.Connection`` whose ``row_factory`` is set to
        ``sqlite3.Row`` so callers can index columns by name.

    Raises:
        HTTPException: 503 if the configured database file does not exist.
            This surfaces a clear error in the UI when a user starts the
            web app before running the parser.
    """
    path = _db_path(request)
    if not path.exists():
        raise HTTPException(status_code=503, detail=f"Database not found: {path}")
    return wiki_db.connect(path)



def _escape_fts5(q: str) -> str:
    """Wrap a raw query in FTS5 phrase quotes, escaping internal double-quotes."""
    return '"' + q.replace('"', '""') + '"'


def _search_titles(q: str, request: Request) -> list[str]:
    """Find article titles matching ``q`` using the FTS5 trigram index.

    The ``articles_fts`` virtual table uses the ``trigram`` tokenizer, which
    supports both prefix and substring matching in a single indexed query.
    Queries shorter than 3 characters fall back to a prefix LIKE on
    ``idx_articles_title`` because the trigram index requires at least 3 chars.

    Args:
        q: Raw search query from the user; whitespace is trimmed.

    Returns:
        Up to ``SEARCH_LIMIT`` titles ordered by BM25 relevance rank.
    """
    q = q.strip()
    if not q:
        return []

    conn = _connect(request)
    try:
        if len(q) < 3:
            needle = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            cur = conn.execute(
                "SELECT title FROM articles WHERE title LIKE ? ESCAPE '\\' "
                "ORDER BY title LIMIT ?",
                (needle + "%", SEARCH_LIMIT),
            )
            return [r["title"] for r in cur.fetchall()]

        cur = conn.execute(
            "SELECT title FROM articles_fts WHERE articles_fts MATCH ? "
            "ORDER BY rank LIMIT ?",
            (_escape_fts5(q), SEARCH_LIMIT),
        )
        return [r["title"] for r in cur.fetchall()]
    finally:
        conn.close()


def _fetch_article(title: str, request: Request) -> tuple[sqlite3.Row | None, str | None]:
    """Look up an article by title, following ``#REDIRECT`` chains.

    A third of the rows in a typical enwiki dump are redirect stubs whose
    ``text_content`` is just ``#REDIRECT [[Target]]``. Follow those up to
    ``REDIRECT_MAX_HOPS`` times so the user sees the real target article.

    Args:
        title: The article's exact title (case-sensitive, matching what the
            search endpoint returned).

    Returns:
        A tuple ``(row, redirected_from)`` where ``row`` is the resolved
        article row (or ``None`` if not found / redirect target missing),
        and ``redirected_from`` is the *original* title the caller asked
        for if at least one redirect was followed, otherwise ``None``.
    """
    conn = _connect(request)
    try:
        original_title = title
        seen: set[str] = set()
        for _ in range(REDIRECT_MAX_HOPS + 1):
            if title in seen:
                break  # cycle
            seen.add(title)
            cur = conn.execute(
                "SELECT title, text_content, text_bytes, timestamp "
                "FROM articles WHERE title = ?",
                (title,),
            )
            row = cur.fetchone()
            if row is None:
                return None, None
            target = wiki_db.redirect_target(row["text_content"])
            if target is None:
                redirected_from = original_title if title != original_title else None
                return row, redirected_from
            title = target
        # Hit the hop cap — bail out and return what we have.
        return None, None
    finally:
        conn.close()


@app.get("/", response_class=HTMLResponse)
def index(request: Request, article: str = "", wiki: str = "", not_found: str = "") -> HTMLResponse:
    """Render the single-page UI shell (search box + empty result containers).

    Args:
        request: FastAPI request, required by Jinja2's ``TemplateResponse``.
        article: Optional article title to pre-load into ``#article`` on page load.
        wiki: Optional wiki override (e.g. ``enwiki``). When present, takes
            precedence over the ``wiki_pref`` cookie and updates it so that
            subsequent HTMX article loads (which read the cookie) use the
            same database.

    Returns:
        The full ``index.html`` page. HTMX takes over from here and swaps
        fragments into ``#results`` and ``#article`` without reloading.
    """
    active_wiki = wiki if wiki in KNOWN_WIKIS else _active_wiki(request)
    other_wiki = next(w for w in KNOWN_WIKIS if w != active_wiki)
    response = templates.TemplateResponse(request, "index.html", {
        "wiki": active_wiki,
        "wiki_label": _WIKI_LABELS[active_wiki],
        "other_wiki": other_wiki,
        "other_wiki_label": _WIKI_LABELS[other_wiki],
        "preload_article": article,
        "not_found": not_found,
    })
    if wiki in KNOWN_WIKIS:
        response.set_cookie("wiki_pref", wiki, max_age=365 * 24 * 3600)
    return response


@app.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = "") -> HTMLResponse:
    """Return an HTML fragment listing titles that match the query.

    Designed as the target of an ``hx-get`` from the search input. The
    fragment is swapped into the ``#results`` container so the user sees
    matches update as they type.

    Args:
        request: FastAPI request used by Jinja2.
        q: The user's search string; defaults to empty so the endpoint
            behaves cleanly when called without a parameter.

    Returns:
        Rendered ``search_results.html`` partial. An empty ``q`` produces
        an empty list rather than an error.
    """
    titles = _search_titles(q, request)
    return templates.TemplateResponse(
        request, "search_results.html", {"titles": titles, "q": q}
    )


@app.get("/wikitext/{title:path}", response_class=HTMLResponse)
def wikitext(request: Request, title: str) -> HTMLResponse:
    """Return the raw wikitext for an article, bypassing the Markdown pipeline.

    Paired with ``/article/{title}`` — the two endpoints toggle between the
    rendered and raw views by swapping each other's fragment into ``#article``.

    Args:
        request: FastAPI request used by Jinja2.
        title: Exact article title taken from the URL path.

    Returns:
        Rendered ``wikitext.html`` partial containing the raw wikitext in a
        ``<pre>`` block.

    Raises:
        HTTPException: 404 when no article with that exact title exists.
    """
    row, _redirected_from = _fetch_article(title, request)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Article not found: {title}")

    return templates.TemplateResponse(
        request,
        "wikitext.html",
        {
            "title": row["title"],
            "wikitext": row["text_content"],
            "text_bytes": row["text_bytes"],
            "timestamp": row["timestamp"],
        },
    )


@app.get("/switch-wiki")
def switch_wiki(to: str, article: str = "") -> RedirectResponse:
    if to not in KNOWN_WIKIS:
        raise HTTPException(status_code=400, detail=f"Unknown wiki: {to}")
    if article:
        redirect_url = f"/?wiki={to}&article={quote(article)}"
    else:
        redirect_url = f"/?wiki={to}"
    response = RedirectResponse(redirect_url, status_code=302)
    response.set_cookie("wiki_pref", to, max_age=365 * 24 * 3600)
    return response


def _format_elapsed(started_at: str) -> str:
    try:
        start = datetime.fromisoformat(started_at)
        elapsed = int((datetime.now(UTC) - start.replace(tzinfo=UTC)).total_seconds())
        if elapsed < 60:
            return f"{elapsed}s"
        if elapsed < 3600:
            return f"{elapsed // 60}m {elapsed % 60}s"
        return f"{elapsed // 3600}h {(elapsed % 3600) // 60}m"
    except Exception:
        return ""


def _format_started_at(started_at: str) -> str:
    try:
        dt = datetime.fromisoformat(started_at)
        return dt.strftime("%-d %b %Y %H:%M UTC")
    except Exception:
        return started_at


def _render_status_panel(
    request: Request, wiki: str, job: sqlite3.Row | None
) -> HTMLResponse:
    if not job:
        return templates.TemplateResponse(
            request, "refresh_panel.html", {"wiki": wiki, "job": None, "elapsed": "", "started_at_display": ""}
        )
    active = job["status"] in ("pending", "downloading", "parsing", "rebuilding")
    elapsed = _format_elapsed(job["started_at"]) if active else ""
    started_at_display = "" if active else _format_started_at(job["started_at"])
    return templates.TemplateResponse(
        request,
        "refresh_panel.html",
        {
            "wiki": wiki,
            "job": dict(job),
            "elapsed": elapsed,
            "started_at_display": started_at_display,
        },
    )


@app.post("/refresh/{wiki}", response_class=HTMLResponse)
def refresh_wiki(request: Request, wiki: str) -> HTMLResponse:
    if wiki not in KNOWN_WIKIS:
        raise HTTPException(status_code=400, detail=f"Unknown wiki: {wiki}")

    conn = refresh_jobs.connect_jobs(JOBS_DB)
    try:
        conn.execute("BEGIN IMMEDIATE")
        active = refresh_jobs.get_active_job(conn, wiki)
        if active:
            conn.rollback()
            return _render_status_panel(request, wiki, active)

        log_path = str(BASE_DIR / "dumps" / f"{wiki}_refresh.log")
        job_id = refresh_jobs.create_job(conn, wiki, log_path)
        conn.commit()
        job = refresh_jobs.get_latest_job(conn, wiki)
    finally:
        conn.close()

    _refresh_log = pathlib.Path(log_path)
    _refresh_log.parent.mkdir(parents=True, exist_ok=True)
    with open(_refresh_log, "a") as _log:
        subprocess.Popen(
            [sys.executable, str(BASE_DIR / "worker.py"), "--wiki", wiki, "--job-id", str(job_id)],
            start_new_session=True,
            stdout=_log,
            stderr=_log,
        )

    return _render_status_panel(request, wiki, job)


@app.get("/refresh/status/{wiki}", response_class=HTMLResponse)
def refresh_status(request: Request, wiki: str) -> HTMLResponse:
    if wiki not in KNOWN_WIKIS:
        raise HTTPException(status_code=400, detail=f"Unknown wiki: {wiki}")

    conn = refresh_jobs.connect_jobs(JOBS_DB)
    try:
        job = refresh_jobs.get_latest_job(conn, wiki)
    finally:
        conn.close()

    return _render_status_panel(request, wiki, job)


def _rag_connect(wiki: str):
    """Open the RAG DB if it exists; return None if not yet created."""
    path = rag_db_path_for(wiki)
    if not path.exists():
        return None
    return connect_rag(path)


EMBED_PAGE_SIZE = 50


@app.get("/embed-manager", response_class=HTMLResponse)
def embed_manager(request: Request, page: int = 1) -> HTMLResponse:
    wiki = _active_wiki(request)
    rag_conn = _rag_connect(wiki)

    if rag_conn is None:
        return templates.TemplateResponse(request, "embed_manager.html", {
            "wiki": wiki,
            "articles": [],
            "page": 1,
            "total_pages": 0,
            "total_count": 0,
            "per_page": EMBED_PAGE_SIZE,
        })

    try:
        total_count = rag_conn.execute(
            "SELECT COUNT(*) FROM articles_meta"
        ).fetchone()[0]
        total_pages = max(1, (total_count + EMBED_PAGE_SIZE - 1) // EMBED_PAGE_SIZE)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * EMBED_PAGE_SIZE
        rows = rag_conn.execute(
            """SELECT m.page_id, m.title, m.categories, COUNT(c.chunk_id) AS chunk_count
               FROM articles_meta m
               LEFT JOIN chunks c ON c.page_id = m.page_id
               GROUP BY m.page_id
               ORDER BY m.title
               LIMIT ? OFFSET ?""",
            (EMBED_PAGE_SIZE, offset),
        ).fetchall()
        articles = [dict(r) for r in rows]
    finally:
        rag_conn.close()

    return templates.TemplateResponse(request, "embed_manager.html", {
        "wiki": wiki,
        "articles": articles,
        "page": page,
        "total_pages": total_pages,
        "total_count": total_count,
        "per_page": EMBED_PAGE_SIZE,
    })


@app.get("/embed-status/{title:path}", response_class=HTMLResponse)
def embed_status(request: Request, title: str) -> HTMLResponse:
    wiki = _active_wiki(request)
    rag_conn = _rag_connect(wiki)

    embedded = False
    if rag_conn is not None:
        try:
            row = rag_conn.execute(
                "SELECT 1 FROM articles_meta WHERE title = ?", (title,)
            ).fetchone()
            embedded = row is not None
        finally:
            rag_conn.close()

    jobs_conn = embed_jobs.connect_embed_jobs(JOBS_DB)
    try:
        links_row = jobs_conn.execute(
            "SELECT 1 FROM embed_jobs WHERE wiki = ? AND triggered_by_title = ? "
            "AND status = 'complete' LIMIT 1",
            (wiki, title),
        ).fetchone()
        links_embedded = links_row is not None
    finally:
        jobs_conn.close()

    return templates.TemplateResponse(request, "embed_status_widget.html", {
        "title": title,
        "embedded": embedded,
        "links_embedded": links_embedded,
        "error": False,
    })


@app.post("/embed-article/{title:path}", response_class=HTMLResponse)
def embed_article(request: Request, title: str) -> HTMLResponse:
    wiki = _active_wiki(request)

    wiki_conn = _connect(request)
    try:
        row = wiki_conn.execute(
            "SELECT page_id, title, revision_id, text_content FROM articles WHERE title = ?",
            (title,),
        ).fetchone()
    finally:
        wiki_conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Article not found: {title}")

    rag_path = rag_db_path_for(wiki)
    rag_conn = connect_rag(rag_path)
    error = False
    try:
        rag_embed_one(
            rag_conn,
            row["page_id"],
            row["title"],
            row["revision_id"],
            row["text_content"],
        )
    except Exception:
        error = True
    finally:
        rag_conn.close()

    jobs_conn = embed_jobs.connect_embed_jobs(JOBS_DB)
    try:
        links_row = jobs_conn.execute(
            "SELECT 1 FROM embed_jobs WHERE wiki = ? AND triggered_by_title = ? "
            "AND status = 'complete' LIMIT 1",
            (wiki, title),
        ).fetchone()
        links_embedded = links_row is not None
    finally:
        jobs_conn.close()

    return templates.TemplateResponse(request, "embed_status_widget.html", {
        "title": title,
        "embedded": not error,
        "links_embedded": links_embedded,
        "error": error,
    })


@app.get("/chunks/{title:path}", response_class=HTMLResponse)
def chunks(request: Request, title: str) -> HTMLResponse:
    wiki = _active_wiki(request)
    rag_conn = _rag_connect(wiki)

    if rag_conn is None:
        raise HTTPException(status_code=404, detail="No RAG database for this wiki")

    try:
        meta = rag_conn.execute(
            "SELECT page_id FROM articles_meta WHERE title = ?", (title,)
        ).fetchone()
        if meta is None:
            raise HTTPException(status_code=404, detail=f"Article not embedded: {title}")

        rows = rag_conn.execute(
            "SELECT section, chunk_index, text, text_length "
            "FROM chunks WHERE page_id = ? ORDER BY chunk_id",
            (meta["page_id"],),
        ).fetchall()
        chunk_list = [dict(r) for r in rows]
    finally:
        rag_conn.close()

    return templates.TemplateResponse(request, "chunks.html", {
        "title": title,
        "chunks": chunk_list,
        "wiki": wiki,
    })


# --- Active embedding (batch: source + linked articles) ---------------------

def _render_active_embedding_panel(
    request: Request,
    wiki: str,
    *,
    fragment_only: bool,
) -> HTMLResponse:
    """Render the active embedding panel (HTMX-polled inner content).

    If ``fragment_only`` is True returns just the panel fragment for HTMX
    polling; otherwise returns the full ``active_embedding.html`` page.
    """
    conn = embed_jobs.connect_embed_jobs(JOBS_DB)
    try:
        active = embed_jobs.get_active_job(conn, wiki)
        if active is None:
            latest = embed_jobs.get_latest_jobs(conn, wiki, limit=1)
            active = latest[0] if latest else None

        recent = embed_jobs.get_latest_jobs(conn, wiki, limit=6)
        items_by_job: dict[int, list[dict]] = {}
        counts_by_job: dict[int, dict[str, int]] = {}
        if active is not None:
            items_by_job[active["id"]] = [
                dict(r) for r in embed_jobs.get_items(conn, active["id"])
            ]
            counts_by_job[active["id"]] = embed_jobs.count_items_by_status(
                conn, active["id"]
            )
        recent_dicts = []
        for job in recent:
            if active is not None and job["id"] == active["id"]:
                continue
            counts = embed_jobs.count_items_by_status(conn, job["id"])
            recent_dicts.append({"job": dict(job), "counts": counts})
    finally:
        conn.close()

    active_dict: dict | None = None
    grouped_items: list[tuple[str, list[dict]]] = []
    counts: dict[str, int] = {}
    elapsed = ""
    started_at_display = ""
    if active is not None:
        active_dict = dict(active)
        items = items_by_job.get(active["id"], [])
        counts = counts_by_job.get(active["id"], {})
        groups: dict[str, list[dict]] = {}
        for item in items:
            groups.setdefault(item["source_title"], []).append(item)
        grouped_items = list(groups.items())
        is_running = (
            active_dict["status"] == "running"
            and not active_dict["cancel_requested"]
        )
        elapsed = _format_elapsed(active_dict["started_at"]) if is_running else ""
        started_at_display = (
            "" if is_running else _format_started_at(active_dict["started_at"])
        )

    template = "active_embedding_panel.html" if fragment_only else "active_embedding.html"
    return templates.TemplateResponse(request, template, {
        "wiki": wiki,
        "job": active_dict,
        "grouped_items": grouped_items,
        "counts": counts,
        "elapsed": elapsed,
        "started_at_display": started_at_display,
        "recent_jobs": recent_dicts,
    })


@app.post("/embed-links/{title:path}", response_class=HTMLResponse)
def embed_links(request: Request, title: str) -> HTMLResponse:
    """Extract wikilinks from ``title`` and enqueue them for batch embedding.

    The source article itself is included in the queue. If a job is already
    running for the active wiki, new items are appended to it; otherwise a new
    job is created and a worker subprocess is spawned.
    """
    wiki = _active_wiki(request)

    wiki_conn = _connect(request)
    try:
        row = wiki_conn.execute(
            "SELECT title, text_content FROM articles WHERE title = ?",
            (title,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Article not found: {title}")

        source_title = row["title"]
        raw_targets = extract_article_links(row["text_content"], source_title=source_title)

        # Resolve each link's redirect chain so the queue stores canonical
        # titles. Dedup happens here too (a redirect and its target collapse
        # to one entry). The source article goes in first so it's processed
        # before its neighbours.
        resolved: list[str] = []
        seen: set[str] = set()
        if source_title not in seen:
            resolved.append(source_title)
            seen.add(source_title)
        for target in raw_targets:
            canonical = wiki_db.resolve_redirect(wiki_conn, target, REDIRECT_MAX_HOPS)
            # Keep unresolved targets so the queue can surface them as not_found.
            picked = canonical or target
            if picked in seen:
                continue
            seen.add(picked)
            resolved.append(picked)
    finally:
        wiki_conn.close()

    items = [(t, source_title) for t in resolved]

    jobs_conn = embed_jobs.connect_embed_jobs(JOBS_DB)
    spawn_worker = False
    try:
        jobs_conn.execute("BEGIN IMMEDIATE")
        active = embed_jobs.get_active_job(jobs_conn, wiki)
        if active is None:
            log_path = str(BASE_DIR / "dumps" / f"{wiki}_embed.log")
            cur = jobs_conn.execute(
                "INSERT INTO embed_jobs (wiki, log_path, triggered_by_title) VALUES (?, ?, ?)",
                (wiki, log_path, source_title),
            )
            job_id = cur.lastrowid
            spawn_worker = True
        else:
            job_id = active["id"]

        if items:
            jobs_conn.executemany(
                "INSERT OR IGNORE INTO embed_job_items (job_id, title, source_title) "
                "VALUES (?, ?, ?)",
                [(job_id, t, s) for t, s in items],
            )
        jobs_conn.commit()
    finally:
        jobs_conn.close()

    if spawn_worker:
        _embed_log = BASE_DIR / "dumps" / f"{wiki}_embed.log"
        _embed_log.parent.mkdir(parents=True, exist_ok=True)
        with open(_embed_log, "a") as _log:
            subprocess.Popen(
                [sys.executable, str(BASE_DIR / "embed_worker.py"),
                 "--wiki", wiki, "--job-id", str(job_id)],
                start_new_session=True,
                stdout=_log,
                stderr=_log,
            )

    # HTMX requests: send the browser to /active-embedding. Non-HTMX callers
    # (curl, tests) get a 303 to the same place so behaviour is consistent.
    if request.headers.get("hx-request"):
        return Response(status_code=204, headers={"HX-Redirect": "/active-embedding"})
    return RedirectResponse("/active-embedding", status_code=303)


@app.get("/active-embedding", response_class=HTMLResponse)
def active_embedding(request: Request) -> HTMLResponse:
    wiki = _active_wiki(request)
    return _render_active_embedding_panel(request, wiki, fragment_only=False)


@app.get("/active-embedding/panel", response_class=HTMLResponse)
def active_embedding_panel(request: Request) -> HTMLResponse:
    wiki = _active_wiki(request)
    return _render_active_embedding_panel(request, wiki, fragment_only=True)


@app.post("/active-embedding/cancel/{job_id}", response_class=HTMLResponse)
def active_embedding_cancel(request: Request, job_id: int) -> HTMLResponse:
    wiki = _active_wiki(request)
    conn = embed_jobs.connect_embed_jobs(JOBS_DB)
    try:
        embed_jobs.request_cancel(conn, job_id)
    finally:
        conn.close()
    return _render_active_embedding_panel(request, wiki, fragment_only=True)


@app.get("/article/{title:path}", response_class=HTMLResponse)
def article(request: Request, title: str) -> HTMLResponse:
    """Render a single article as HTML.

    The conversion pipeline is:
        SQLite ``text_content`` (wikitext)
            → ``convert_wikitext_to_html`` (converts to HTML directly)

    The ``{title:path}`` converter is used so titles containing slashes —
    rare but legal in MediaWiki — round-trip correctly.

    Args:
        request: FastAPI request used by Jinja2.
        title: Exact article title taken from the URL path.

    Returns:
        Rendered ``article.html`` partial containing the article body and
        small metadata header (size and last-edit timestamp).

    Raises:
        HTTPException: 404 when no article with that exact title exists.
    """
    row, redirected_from = _fetch_article(title, request)
    if row is None:
        if request.headers.get("HX-Request") == "true":
            wiki = _active_wiki(request)
            wiki_label = _WIKI_LABELS.get(wiki, wiki)
            resp = Response(content="", status_code=200)
            resp.headers["HX-Reswap"] = "none"
            resp.headers["HX-Trigger"] = json.dumps(
                {"articleNotFound": {"title": title, "wiki": wiki_label}}
            )
            return resp
        raise HTTPException(status_code=404, detail=f"Article not found: {title}")

    html = convert_wikitext_to_html(row["text_content"])

    wiki = _active_wiki(request)
    other_wiki = next(w for w in KNOWN_WIKIS if w != wiki)
    other_wiki_for_template = None
    other_wiki_db = db_path_for(other_wiki)
    if other_wiki_db.exists():
        with wiki_db.connect(other_wiki_db) as ow_conn:
            hit = ow_conn.execute(
                "SELECT 1 FROM articles WHERE title = ? LIMIT 1", (row["title"],)
            ).fetchone()
            if hit:
                other_wiki_for_template = other_wiki
    response = templates.TemplateResponse(
        request,
        "article.html",
        {
            "title": row["title"],
            "html": html,
            "text_bytes": row["text_bytes"],
            "timestamp": row["timestamp"],
            "redirected_from": redirected_from,
            "wiki": wiki,
            "other_wiki": other_wiki_for_template,
        },
    )
    if request.headers.get("HX-Request") == "true":
        response.headers["HX-Push-Url"] = f"/?wiki={wiki}&article={quote(row['title'])}"
    return response
