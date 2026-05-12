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

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import db as wiki_db
from app.config import EMBED_PAGE_SIZE, REDIRECT_MAX_HOPS, WIKI_LABELS, templates
from app.deps import active_wiki, connect, rag_connect
from app.helpers import (
    fetch_article,
    format_embedded_at,
    htmx_redirect,
    search_titles,
    spawn_worker,
    wiki_label,
)
from app.lifespan import lifespan
from app.panels import (
    render_active_embedding_panel,
    render_job_list_panel,
    render_status_panel,
)
from jobs import embed as embed_jobs, refresh as refresh_jobs
from paths import BASE_DIR, JOBS_DB, KNOWN_WIKIS, db_path_for, rag_db_path_for
from rag.embed import delete_all_articles as rag_delete_all_articles, delete_article as rag_delete_article, embed_one as rag_embed_one
from rag.links import extract_article_links
from rag.schema import connect_rag
from render import convert_wikitext_to_html


app = FastAPI(title="Local Wikipedia", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


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
    selected_wiki = wiki if wiki in KNOWN_WIKIS else active_wiki(request)
    other_wiki = next(w for w in KNOWN_WIKIS if w != selected_wiki)
    other_wiki_db = db_path_for(other_wiki)
    other_wiki_for_template = other_wiki if other_wiki_db.exists() else None
    wiki_db_path = pathlib.Path(os.environ["WIKI_DB"]) if "WIKI_DB" in os.environ else db_path_for(selected_wiki)
    with wiki_db.connect(wiki_db_path) as conn:
        try:
            row = conn.execute(
                "SELECT value FROM db_metadata WHERE key = 'article_count'"
            ).fetchone()
            article_count = int(row["value"]) if row else None
        except sqlite3.OperationalError:
            article_count = None
        if article_count is None:
            article_count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
            conn.execute(
                "CREATE TABLE IF NOT EXISTS db_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO db_metadata (key, value) VALUES ('article_count', ?)",
                (str(article_count),),
            )
    response = templates.TemplateResponse(request, "index.html", {
        "wiki": selected_wiki,
        "wiki_label": WIKI_LABELS[selected_wiki],
        "other_wiki": other_wiki_for_template,
        "preload_article": article,
        "not_found": not_found,
        "current_page": "home",
        "article_count": f"{article_count:,}",
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
    titles = search_titles(q, request)
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
    row, _redirected_from = fetch_article(title, request)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Article not found: {title}")

    wiki = active_wiki(request)
    other_wiki = next(w for w in KNOWN_WIKIS if w != wiki)
    other_wiki_for_template = None
    other_wiki_db = db_path_for(other_wiki)
    if other_wiki_db.exists():
        try:
            with wiki_db.connect(other_wiki_db) as ow_conn:
                hit = ow_conn.execute(
                    "SELECT 1 FROM articles WHERE title = ? LIMIT 1", (row["title"],)
                ).fetchone()
                if hit:
                    other_wiki_for_template = other_wiki
        except Exception:
            pass

    return templates.TemplateResponse(
        request,
        "wikitext.html",
        {
            "title": row["title"],
            "wikitext": row["text_content"],
            "text_bytes": row["text_bytes"],
            "timestamp": row["timestamp"],
            "wiki": wiki,
            "other_wiki": other_wiki_for_template,
        },
    )


@app.get("/switch-wiki")
def switch_wiki(to: str, article: str = "", return_to: str = "") -> RedirectResponse:
    if to not in KNOWN_WIKIS:
        raise HTTPException(status_code=400, detail=f"Unknown wiki: {to}")
    if article:
        redirect_url = f"/?wiki={to}&article={quote(article)}"
    elif return_to and return_to.startswith("/") and not return_to.startswith("//"):
        redirect_url = return_to
    else:
        redirect_url = f"/?wiki={to}"
    response = RedirectResponse(redirect_url, status_code=302)
    response.set_cookie("wiki_pref", to, max_age=365 * 24 * 3600)
    return response


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
            return render_status_panel(request, wiki, active)

        log_path = str(BASE_DIR / "dumps" / f"{wiki}_refresh.log")
        job_id = refresh_jobs.create_job(conn, wiki, log_path)
        conn.commit()
        job = refresh_jobs.get_latest_job(conn, wiki)
    finally:
        conn.close()

    spawn_worker("workers.refresh", wiki, job_id, "refresh")

    return render_status_panel(request, wiki, job)


@app.get("/refresh/status/{wiki}", response_class=HTMLResponse)
def refresh_status(request: Request, wiki: str) -> HTMLResponse:
    if wiki not in KNOWN_WIKIS:
        raise HTTPException(status_code=400, detail=f"Unknown wiki: {wiki}")

    conn = refresh_jobs.connect_jobs(JOBS_DB)
    try:
        job = refresh_jobs.get_latest_job(conn, wiki)
    finally:
        conn.close()

    return render_status_panel(request, wiki, job)


@app.get("/embed-manager", response_class=HTMLResponse)
def embed_manager(request: Request, page: int = 1) -> HTMLResponse:
    wiki = active_wiki(request)
    other_wiki = next(w for w in KNOWN_WIKIS if w != wiki)
    other_wiki_for_template = other_wiki if db_path_for(other_wiki).exists() else None
    rag_conn = rag_connect(wiki)

    if rag_conn is None:
        return templates.TemplateResponse(request, "embed_manager.html", {
            "wiki": wiki,
            "other_wiki": other_wiki_for_template,
            "articles": [],
            "page": 1,
            "total_pages": 0,
            "total_count": 0,
            "per_page": EMBED_PAGE_SIZE,
            "current_page": "embeddings",
        })

    try:
        total_count = rag_conn.execute(
            "SELECT COUNT(*) FROM articles_meta"
        ).fetchone()[0]
        total_pages = max(1, (total_count + EMBED_PAGE_SIZE - 1) // EMBED_PAGE_SIZE)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * EMBED_PAGE_SIZE
        rows = rag_conn.execute(
            """SELECT m.page_id, m.title, m.categories,
                      m.article_size_bytes, m.embedded_at, m.links_embedded,
                      COUNT(c.chunk_id) AS chunk_count
               FROM articles_meta m
               LEFT JOIN chunks c ON c.page_id = m.page_id
               GROUP BY m.page_id
               ORDER BY m.title
               LIMIT ? OFFSET ?""",
            (EMBED_PAGE_SIZE, offset),
        ).fetchall()
        articles = [dict(r) for r in rows]
        for a in articles:
            a["embedded_at_display"] = format_embedded_at(a.get("embedded_at"))
    finally:
        rag_conn.close()

    return templates.TemplateResponse(request, "embed_manager.html", {
        "wiki": wiki,
        "other_wiki": other_wiki_for_template,
        "articles": articles,
        "page": page,
        "total_pages": total_pages,
        "total_count": total_count,
        "per_page": EMBED_PAGE_SIZE,
        "current_page": "embeddings",
    })


@app.get("/embed-status/{title:path}", response_class=HTMLResponse)
def embed_status(request: Request, title: str) -> HTMLResponse:
    wiki = active_wiki(request)
    rag_conn = rag_connect(wiki)

    embedded = False
    links_embedded = False
    if rag_conn is not None:
        try:
            row = rag_conn.execute(
                "SELECT links_embedded FROM articles_meta WHERE title = ?", (title,)
            ).fetchone()
            embedded = row is not None
            links_embedded = bool(row and row["links_embedded"])
        finally:
            rag_conn.close()

    return templates.TemplateResponse(request, "embed_status_widget.html", {
        "title": title,
        "embedded": embedded,
        "links_embedded": links_embedded,
        "error": False,
    })


@app.post("/embed-article/{title:path}", response_class=HTMLResponse)
def embed_article(request: Request, title: str) -> HTMLResponse:
    wiki = active_wiki(request)

    wiki_conn = connect(request)
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
    chunk_count = 0
    error = False
    embed_error: str | None = None
    links_embedded = False
    try:
        chunk_count = rag_embed_one(
            rag_conn,
            row["page_id"],
            row["title"],
            row["revision_id"],
            row["text_content"],
        )
        links_row = rag_conn.execute(
            "SELECT links_embedded FROM articles_meta WHERE page_id = ?",
            (row["page_id"],),
        ).fetchone()
        links_embedded = bool(links_row and links_row["links_embedded"])
    except Exception as exc:
        error = True
        embed_error = str(exc)
    finally:
        rag_conn.close()

    jobs_conn = embed_jobs.connect_embed_jobs(JOBS_DB)
    try:
        embed_jobs.record_sync_embed(jobs_conn, wiki, title, chunk_count, embed_error)
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
    wiki = active_wiki(request)
    rag_conn = rag_connect(wiki)

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
        "current_page": "",
    })


@app.post("/embed-links/{title:path}", response_class=HTMLResponse)
def embed_links(request: Request, title: str) -> HTMLResponse:
    """Extract wikilinks from ``title`` and enqueue them for batch embedding.

    The source article itself is included in the queue. If a job is already
    running for the active wiki, new items are appended to it; otherwise a new
    job is created and a worker subprocess is spawned.
    """
    wiki = active_wiki(request)

    wiki_conn = connect(request)
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
    started_new_job = False
    try:
        jobs_conn.execute("BEGIN IMMEDIATE")
        active = embed_jobs.get_active_job(jobs_conn, wiki)
        if active is None:
            log_path = str(BASE_DIR / "dumps" / f"{wiki}_embed.log")
            job_id = embed_jobs.create_job(
                jobs_conn, wiki, log_path,
                triggered_by_title=source_title,
                include_links=1,
            )
            started_new_job = True
        else:
            job_id = active["id"]

        if items:
            embed_jobs.append_items(jobs_conn, job_id, items)
        else:
            jobs_conn.commit()
    finally:
        jobs_conn.close()

    if started_new_job:
        spawn_worker("workers.embed", wiki, job_id, "embed")

    return htmx_redirect("/active-embedding", request)


@app.delete("/embed/{wiki}/{title:path}")
def delete_embed(request: Request, wiki: str, title: str) -> Response:
    """Remove all chunks, vectors, and metadata for one embedded article."""
    if wiki not in KNOWN_WIKIS:
        raise HTTPException(status_code=400, detail=f"Unknown wiki: {wiki}")
    rag_conn = rag_connect(wiki)
    if rag_conn is None:
        raise HTTPException(status_code=404, detail="No RAG database for this wiki")
    try:
        meta = rag_conn.execute(
            "SELECT page_id FROM articles_meta WHERE title = ?", (title,)
        ).fetchone()
        if meta is None:
            raise HTTPException(status_code=404, detail=f"Article not embedded: {title}")
        rag_delete_article(rag_conn, meta["page_id"])
        rag_conn.commit()
    finally:
        rag_conn.close()
    return Response(content="", status_code=200)


@app.delete("/embed-all/{wiki}")
def delete_all_embeds(request: Request, wiki: str) -> Response:
    """Remove all chunks, vectors, and metadata for every embedded article in the wiki."""
    if wiki not in KNOWN_WIKIS:
        raise HTTPException(status_code=400, detail=f"Unknown wiki: {wiki}")
    rag_conn = rag_connect(wiki)
    if rag_conn is None:
        raise HTTPException(status_code=404, detail="No RAG database for this wiki")
    try:
        rag_delete_all_articles(rag_conn)
        rag_conn.commit()
    finally:
        rag_conn.close()
    return htmx_redirect("/embed-manager", request)


@app.post("/embed/reembed/{wiki}/{title:path}")
def reembed_article(request: Request, wiki: str, title: str) -> Response:
    """Enqueue a single article for re-embedding without following its links."""
    if wiki not in KNOWN_WIKIS:
        raise HTTPException(status_code=400, detail=f"Unknown wiki: {wiki}")

    wiki_conn = wiki_db.connect(db_path_for(wiki))
    try:
        row = wiki_conn.execute(
            "SELECT title FROM articles WHERE title = ?", (title,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Article not found: {title}")
        canonical_title = row["title"]
    finally:
        wiki_conn.close()

    jobs_conn = embed_jobs.connect_embed_jobs(JOBS_DB)
    started_new_job = False
    try:
        jobs_conn.execute("BEGIN IMMEDIATE")
        log_path = str(BASE_DIR / "dumps" / f"{wiki}_embed.log")
        job_id = embed_jobs.create_job(
            jobs_conn, wiki, log_path,
            triggered_by_title=canonical_title,
            include_links=0,
        )
        embed_jobs.append_items(jobs_conn, job_id, [(canonical_title, canonical_title)])
        started_new_job = True
    finally:
        jobs_conn.close()

    if started_new_job:
        spawn_worker("workers.embed", wiki, job_id, "embed")

    return htmx_redirect("/active-embedding", request)


@app.get("/active-embedding", response_class=HTMLResponse)
def active_embedding(request: Request) -> HTMLResponse:
    wiki = active_wiki(request)
    return render_active_embedding_panel(request, wiki, fragment_only=False)


@app.get("/active-embedding/jobs", response_class=HTMLResponse)
def active_embedding_jobs(request: Request, q: str = "", page: int = 1) -> HTMLResponse:
    wiki = active_wiki(request)
    return render_job_list_panel(request, wiki, q=q, page=page)


@app.delete("/active-embedding/jobs")
def delete_all_jobs(request: Request) -> Response:
    """Delete all embed job records and their items."""
    conn = embed_jobs.connect_embed_jobs(JOBS_DB)
    try:
        embed_jobs.delete_all_jobs(conn)
    finally:
        conn.close()
    return htmx_redirect("/active-embedding", request)


@app.get("/active-embedding/panel", response_class=HTMLResponse)
def active_embedding_panel(request: Request) -> HTMLResponse:
    wiki = active_wiki(request)
    return render_active_embedding_panel(request, wiki, fragment_only=True)


@app.get("/active-embedding/panel/{job_id}", response_class=HTMLResponse)
def active_embedding_panel_for_job(request: Request, job_id: int) -> HTMLResponse:
    wiki = active_wiki(request)
    return render_active_embedding_panel(request, wiki, fragment_only=True, job_id=job_id)


@app.post("/active-embedding/cancel/{job_id}", response_class=HTMLResponse)
def active_embedding_cancel(request: Request, job_id: int) -> HTMLResponse:
    wiki = active_wiki(request)
    conn = embed_jobs.connect_embed_jobs(JOBS_DB)
    try:
        embed_jobs.request_cancel(conn, job_id)
    finally:
        conn.close()
    return render_active_embedding_panel(request, wiki, fragment_only=True)


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
    row, redirected_from = fetch_article(title, request)
    if row is None:
        if request.headers.get("HX-Request") == "true":
            wiki = active_wiki(request)
            resp = Response(content="", status_code=200)
            resp.headers["HX-Reswap"] = "none"
            resp.headers["HX-Trigger"] = json.dumps(
                {"articleNotFound": {"title": title, "wiki": wiki_label(wiki)}}
            )
            return resp
        raise HTTPException(status_code=404, detail=f"Article not found: {title}")

    html = convert_wikitext_to_html(row["text_content"])

    wiki = active_wiki(request)
    other_wiki = next(w for w in KNOWN_WIKIS if w != wiki)
    other_wiki_for_template = None
    other_wiki_db = db_path_for(other_wiki)
    if other_wiki_db.exists():
        try:
            with wiki_db.connect(other_wiki_db) as ow_conn:
                hit = ow_conn.execute(
                    "SELECT 1 FROM articles WHERE title = ? LIMIT 1", (row["title"],)
                ).fetchone()
                if hit:
                    other_wiki_for_template = other_wiki
        except Exception:
            pass
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
