"""Per-article embed routes + the embed-manager listing page."""

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

import db as wiki_db
import paths
from app.config import EMBED_PAGE_SIZE, templates
from app.deps import active_wiki, connect, rag_connect
from app.helpers import format_embedded_at, htmx_redirect, spawn_worker
from jobs import embed as embed_jobs
from paths import KNOWN_WIKIS, REDIRECT_MAX_HOPS
from rag.embed import (
    delete_all_articles as rag_delete_all_articles,
    delete_article as rag_delete_article,
    embed_one as rag_embed_one,
)
from rag.links import extract_article_links
from rag.schema import connect_rag

router = APIRouter()


@router.get("/embed-manager", response_class=HTMLResponse)
def embed_manager(request: Request, page: int = 1) -> HTMLResponse:
    wiki = active_wiki(request)
    other_wiki = next(w for w in KNOWN_WIKIS if w != wiki)
    other_wiki_for_template = other_wiki if paths.db_path_for(other_wiki).exists() else None
    rag_conn = rag_connect(wiki)

    if rag_conn is None:
        return templates.TemplateResponse(
            request,
            "embed_manager.html",
            {
                "wiki": wiki,
                "other_wiki": other_wiki_for_template,
                "articles": [],
                "page": 1,
                "total_pages": 0,
                "total_count": 0,
                "per_page": EMBED_PAGE_SIZE,
                "current_page": "embeddings",
            },
        )

    try:
        total_count = rag_conn.execute("SELECT COUNT(*) FROM articles_meta").fetchone()[0]
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

    return templates.TemplateResponse(
        request,
        "embed_manager.html",
        {
            "wiki": wiki,
            "other_wiki": other_wiki_for_template,
            "articles": articles,
            "page": page,
            "total_pages": total_pages,
            "total_count": total_count,
            "per_page": EMBED_PAGE_SIZE,
            "current_page": "embeddings",
        },
    )


@router.get("/embed-status/{title:path}", response_class=HTMLResponse)
def embed_status(request: Request, title: str) -> HTMLResponse:
    wiki = active_wiki(request)
    rag_conn = rag_connect(wiki)

    embedded = False
    links_embedded = False
    if rag_conn is not None:
        try:
            row = rag_conn.execute("SELECT links_embedded FROM articles_meta WHERE title = ?", (title,)).fetchone()
            embedded = row is not None
            links_embedded = bool(row and row["links_embedded"])
        finally:
            rag_conn.close()

    return templates.TemplateResponse(
        request,
        "embed_status_widget.html",
        {
            "title": title,
            "embedded": embedded,
            "links_embedded": links_embedded,
            "error": False,
        },
    )


@router.post("/embed-article/{title:path}", response_class=HTMLResponse)
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

    rag_path = paths.rag_db_path_for(wiki)
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

    jobs_conn = embed_jobs.connect_embed_jobs(paths.JOBS_DB)
    try:
        embed_jobs.record_sync_embed(jobs_conn, wiki, title, chunk_count, embed_error)
    finally:
        jobs_conn.close()

    return templates.TemplateResponse(
        request,
        "embed_status_widget.html",
        {
            "title": title,
            "embedded": not error,
            "links_embedded": links_embedded,
            "error": error,
        },
    )


def _enqueue_links(request: Request, title: str, link_depth: int) -> HTMLResponse:
    """Extract wikilinks from ``title`` and enqueue them for batch embedding.

    ``link_depth`` is the ``hops_remaining`` value assigned to each 1-hop link
    target — pass 0 for a single-hop trigger (worker won't expand further) or
    1 for a 2-hop trigger (worker expands each link once more).

    The source article itself is included with ``hops_remaining=0`` because
    the route has already enqueued its 1-hop neighbours. If a job is already
    running for the active wiki, new items are appended to it; otherwise a
    new job is created and a worker subprocess is spawned.
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

    items = [(t, source_title, 0 if t == source_title else link_depth) for t in resolved]

    jobs_conn = embed_jobs.connect_embed_jobs(paths.JOBS_DB)
    started_new_job = False
    try:
        jobs_conn.execute("BEGIN IMMEDIATE")
        active = embed_jobs.get_active_job(jobs_conn, wiki)
        if active is None:
            log_path = str(paths.BASE_DIR / "dumps" / f"{wiki}_embed.log")
            job_id = embed_jobs.create_job(
                jobs_conn,
                wiki,
                log_path,
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


@router.post("/embed-links/{title:path}", response_class=HTMLResponse)
def embed_links(request: Request, title: str) -> HTMLResponse:
    """1-hop: embed ``title`` plus every wikilink target it references."""
    return _enqueue_links(request, title, link_depth=0)


@router.post("/embed-links-2/{title:path}", response_class=HTMLResponse)
def embed_links_2(request: Request, title: str) -> HTMLResponse:
    """2-hop: embed ``title``, every wikilink target, and the link targets of those.

    The 1-hop link rows are enqueued with ``hops_remaining=1`` so the worker
    extracts each one's wikilinks during processing and appends them at
    ``hops_remaining=0`` (terminal).
    """
    return _enqueue_links(request, title, link_depth=1)


@router.delete("/embed/{wiki}/{title:path}")
def delete_embed(request: Request, wiki: str, title: str) -> Response:
    """Remove all chunks, vectors, and metadata for one embedded article."""
    if wiki not in KNOWN_WIKIS:
        raise HTTPException(status_code=400, detail=f"Unknown wiki: {wiki}")
    rag_conn = rag_connect(wiki)
    if rag_conn is None:
        raise HTTPException(status_code=404, detail="No RAG database for this wiki")
    try:
        meta = rag_conn.execute("SELECT page_id FROM articles_meta WHERE title = ?", (title,)).fetchone()
        if meta is None:
            raise HTTPException(status_code=404, detail=f"Article not embedded: {title}")
        rag_delete_article(rag_conn, meta["page_id"])
        rag_conn.commit()
    finally:
        rag_conn.close()
    return Response(content="", status_code=200)


@router.delete("/embed-all/{wiki}")
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


@router.post("/embed/reembed/{wiki}/{title:path}")
def reembed_article(request: Request, wiki: str, title: str) -> Response:
    """Enqueue a single article for re-embedding without following its links."""
    if wiki not in KNOWN_WIKIS:
        raise HTTPException(status_code=400, detail=f"Unknown wiki: {wiki}")

    wiki_conn = wiki_db.connect(paths.db_path_for(wiki))
    try:
        row = wiki_conn.execute("SELECT title FROM articles WHERE title = ?", (title,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Article not found: {title}")
        canonical_title = row["title"]
    finally:
        wiki_conn.close()

    jobs_conn = embed_jobs.connect_embed_jobs(paths.JOBS_DB)
    started_new_job = False
    try:
        jobs_conn.execute("BEGIN IMMEDIATE")
        log_path = str(paths.BASE_DIR / "dumps" / f"{wiki}_embed.log")
        job_id = embed_jobs.create_job(
            jobs_conn,
            wiki,
            log_path,
            triggered_by_title=canonical_title,
            include_links=0,
        )
        embed_jobs.append_items(jobs_conn, job_id, [(canonical_title, canonical_title, 0)])
        started_new_job = True
    finally:
        jobs_conn.close()

    if started_new_job:
        spawn_worker("workers.embed", wiki, job_id, "embed")

    return htmx_redirect("/active-embedding", request)
