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
import os
import pathlib
import re
import sqlite3
import subprocess
import sys
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import db as wiki_db
import jobs as refresh_jobs
from paths import BASE_DIR, DEFAULT_WIKI, JOBS_DB, KNOWN_WIKIS, db_path_for, rag_db_path_for
from rag.embed import embed_one as rag_embed_one
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

# Match a wikitext redirect line, e.g. ``#REDIRECT [[Target]]``. MediaWiki
# accepts case-insensitive ``REDIRECT`` and ignores leading whitespace.
_REDIRECT_RE = re.compile(
    r"^\s*#\s*REDIRECT\s*\[\[\s*([^\]\|#]+?)\s*(?:#[^\]\|]*)?(?:\|[^\]]*)?\s*\]\]",
    re.IGNORECASE,
)

app = FastAPI(title="Local Wikipedia")
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
            target = _redirect_target(row["text_content"])
            if target is None:
                redirected_from = original_title if title != original_title else None
                return row, redirected_from
            title = target
        # Hit the hop cap — bail out and return what we have.
        return None, None
    finally:
        conn.close()


def _redirect_target(text_content: str | None) -> str | None:
    """Return the redirect target if ``text_content`` is a redirect stub.

    MediaWiki redirects look like ``#REDIRECT [[Target]]`` (case-insensitive)
    optionally followed by a section anchor or display label which we drop
    since neither affects the resolved article.
    """
    if not text_content:
        return None
    m = _REDIRECT_RE.match(text_content)
    if not m:
        return None
    target = m.group(1).strip()
    # MediaWiki capitalises the first letter of every page title.
    if target:
        target = target[:1].upper() + target[1:]
    return target or None


@app.get("/", response_class=HTMLResponse)
def index(request: Request, article: str = "") -> HTMLResponse:
    """Render the single-page UI shell (search box + empty result containers).

    Args:
        request: FastAPI request, required by Jinja2's ``TemplateResponse``.
        article: Optional article title to pre-load into ``#article`` on page load.

    Returns:
        The full ``index.html`` page. HTMX takes over from here and swaps
        fragments into ``#results`` and ``#article`` without reloading.
    """
    wiki = _active_wiki(request)
    other_wiki = next(w for w in KNOWN_WIKIS if w != wiki)
    return templates.TemplateResponse(request, "index.html", {
        "wiki": wiki,
        "wiki_label": _WIKI_LABELS[wiki],
        "other_wiki": other_wiki,
        "other_wiki_label": _WIKI_LABELS[other_wiki],
        "preload_article": article,
    })


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
def switch_wiki(to: str) -> RedirectResponse:
    if to not in KNOWN_WIKIS:
        raise HTTPException(status_code=400, detail=f"Unknown wiki: {to}")
    response = RedirectResponse("/", status_code=302)
    response.set_cookie("wiki_pref", to, max_age=365 * 24 * 3600)
    return response


def _format_elapsed(started_at: str) -> str:
    try:
        start = datetime.fromisoformat(started_at)
        elapsed = int((datetime.utcnow() - start).total_seconds())
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
        return dt.strftime("%-d %b %Y %-I:%M %p UTC")
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
    finally:
        conn.close()

    subprocess.Popen(
        [sys.executable, str(BASE_DIR / "worker.py"), "--wiki", wiki, "--job-id", str(job_id)],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    conn2 = refresh_jobs.connect_jobs(JOBS_DB)
    try:
        job = refresh_jobs.get_latest_job(conn2, wiki)
    finally:
        conn2.close()

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

    return templates.TemplateResponse(request, "embed_status_widget.html", {
        "title": title,
        "embedded": embedded,
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

    return templates.TemplateResponse(request, "embed_status_widget.html", {
        "title": title,
        "embedded": not error,
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
        raise HTTPException(status_code=404, detail=f"Article not found: {title}")

    html = convert_wikitext_to_html(row["text_content"])

    return templates.TemplateResponse(
        request,
        "article.html",
        {
            "title": row["title"],
            "html": html,
            "text_bytes": row["text_bytes"],
            "timestamp": row["timestamp"],
            "redirected_from": redirected_from,
        },
    )
