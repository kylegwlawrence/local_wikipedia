"""arXiv routes — search, abstract page, embed actions, active-job status.

* Search (``GET /arxiv``, ``GET /arxiv/search``) is read-only over
  ``arxiv.db`` (metadata) and ``arxiv_rag.db`` (embeddings + FTS).
* Abstract page (``GET /arxiv/{id}``) renders a single paper with its
  embed status and action buttons.
* Embed-abstract (``POST /arxiv/{id}/embed-abstract``) is synchronous —
  one Ollama call, immediate HTMX swap of the action panel.
* Embed-paper (``POST /arxiv/{id}/embed-paper``) is asynchronous — appends
  to the active job (or starts a new one + spawns a worker subprocess).
* Status page (``GET /arxiv/active-embedding``) shows the current job.
* Cancel (``POST /arxiv/active-embedding/cancel/{job_id}``) sets the
  cancel flag; the worker observes it between items.
"""

import json

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

import paths
from app.config import templates
from app.helpers import htmx_redirect, spawn_worker
from arxiv import jobs as arxiv_jobs, retriever
from arxiv.embed import embed_one_abstract
from arxiv.render import prepare_local_view
from arxiv.schema import connect_arxiv_rag, connect_papers

router = APIRouter()


def _embed_status(arxiv_id: str) -> tuple[bool, str | None, int]:
    """Return ``(abstract_embedded, full_status, full_chunk_count)`` for ``arxiv_id``.

    Returns falsy defaults if ``arxiv_rag.db`` does not exist yet.
    """
    if not paths.ARXIV_RAG_DB.exists():
        return False, None, 0
    rag_conn = connect_arxiv_rag(paths.ARXIV_RAG_DB)
    try:
        abstract = rag_conn.execute("SELECT 1 FROM papers_meta WHERE arxiv_id = ?", (arxiv_id,)).fetchone()
        full = rag_conn.execute(
            "SELECT status, chunk_count FROM papers_full_meta WHERE arxiv_id = ?",
            (arxiv_id,),
        ).fetchone()
        return (
            abstract is not None,
            full["status"] if full else None,
            full["chunk_count"] if full else 0,
        )
    finally:
        rag_conn.close()


def _fetch_paper(arxiv_id: str) -> dict:
    """Return one paper as a dict with ``authors`` decoded from JSON.

    Raises ``HTTPException(404)`` if not present in ``papers``.
    """
    if not paths.ARXIV_DB.exists():
        raise HTTPException(status_code=404, detail=f"Paper not found: {arxiv_id}")
    papers_conn = connect_papers(paths.ARXIV_DB)
    try:
        row = papers_conn.execute("SELECT * FROM papers WHERE id = ?", (arxiv_id,)).fetchone()
    finally:
        papers_conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Paper not found: {arxiv_id}")
    paper = dict(row)
    try:
        paper["authors"] = json.loads(paper["authors"])
    except (TypeError, json.JSONDecodeError):
        paper["authors"] = []
    return paper


# --- Search ------------------------------------------------------------------


@router.get("/arxiv", response_class=HTMLResponse)
def arxiv_index(request: Request) -> HTMLResponse:
    """Render the arXiv search shell."""
    return templates.TemplateResponse(request, "arxiv.html", {"current_page": "arxiv"})


@router.get("/arxiv/search", response_class=HTMLResponse)
def arxiv_search(request: Request, q: str = "") -> HTMLResponse:
    """Return an HTMX fragment with the top results for ``q``."""
    query = q.strip()
    ready = paths.ARXIV_DB.exists() and paths.ARXIV_RAG_DB.exists()
    if not query or not ready:
        return templates.TemplateResponse(
            request,
            "arxiv_results.html",
            {"hits": [], "q": q, "ready": ready, "used_dense": False},
        )

    rag_conn = connect_arxiv_rag(paths.ARXIV_RAG_DB)
    papers_conn = connect_papers(paths.ARXIV_DB)
    try:
        result = retriever.retrieve(query, rag_conn, papers_conn, top_k=20)
    finally:
        rag_conn.close()
        papers_conn.close()

    return templates.TemplateResponse(
        request,
        "arxiv_results.html",
        {"hits": result.hits, "q": q, "ready": True, "used_dense": result.used_dense},
    )


# --- Abstract page + actions -------------------------------------------------


@router.get("/arxiv/active-embedding", response_class=HTMLResponse)
def arxiv_active_embedding(request: Request) -> HTMLResponse:
    """Render the full-paper embed job status page."""
    return templates.TemplateResponse(
        request,
        "arxiv_active_embedding.html",
        {"current_page": "arxiv", **_active_embedding_context()},
    )


@router.get("/arxiv/active-embedding/panel", response_class=HTMLResponse)
def arxiv_active_embedding_panel(request: Request) -> HTMLResponse:
    """HTMX-polled fragment used by the status page."""
    return templates.TemplateResponse(
        request,
        "arxiv_active_embedding_panel.html",
        _active_embedding_context(),
    )


@router.post("/arxiv/active-embedding/cancel/{job_id}", response_class=HTMLResponse)
def arxiv_active_embedding_cancel(request: Request, job_id: int) -> Response:
    """Set the cancel flag on ``job_id``. The worker observes it between items."""
    jobs_conn = arxiv_jobs.connect_arxiv_jobs(paths.JOBS_DB)
    try:
        if arxiv_jobs.get_job(jobs_conn, job_id) is None:
            raise HTTPException(status_code=404)
        arxiv_jobs.request_cancel(jobs_conn, job_id)
    finally:
        jobs_conn.close()
    return templates.TemplateResponse(
        request,
        "arxiv_active_embedding_panel.html",
        _active_embedding_context(),
    )


def _active_embedding_context() -> dict:
    """Build the template context for the active-embedding panel."""
    if not paths.JOBS_DB.exists():
        return {"job": None, "items": [], "counts": {}}
    jobs_conn = arxiv_jobs.connect_arxiv_jobs(paths.JOBS_DB)
    try:
        latest = arxiv_jobs.get_latest_jobs(jobs_conn, limit=1)
        job = latest[0] if latest else None
        if job is None:
            return {"job": None, "items": [], "counts": {}}
        items = arxiv_jobs.get_items(jobs_conn, job["id"])
        counts = arxiv_jobs.count_items_by_status(jobs_conn, job["id"])
        return {"job": job, "items": items, "counts": counts}
    finally:
        jobs_conn.close()


@router.get("/arxiv/{arxiv_id}", response_class=HTMLResponse)
def arxiv_paper(request: Request, arxiv_id: str) -> HTMLResponse:
    """Render the abstract page for a single paper."""
    paper = _fetch_paper(arxiv_id)
    abstract_embedded, full_status, full_chunk_count = _embed_status(arxiv_id)
    return templates.TemplateResponse(
        request,
        "arxiv_paper.html",
        {
            "current_page": "arxiv",
            "paper": paper,
            "abstract_embedded": abstract_embedded,
            "full_status": full_status,
            "full_chunk_count": full_chunk_count,
        },
    )


@router.post("/arxiv/{arxiv_id}/embed-abstract", response_class=HTMLResponse)
def arxiv_embed_abstract(request: Request, arxiv_id: str) -> HTMLResponse:
    """Re-embed a single paper's abstract synchronously."""
    paper = _fetch_paper(arxiv_id)
    papers_conn = connect_papers(paths.ARXIV_DB)
    rag_conn = connect_arxiv_rag(paths.ARXIV_RAG_DB)
    try:
        embed_one_abstract(arxiv_id, papers_conn=papers_conn, rag_conn=rag_conn)
    finally:
        rag_conn.close()
        papers_conn.close()
    abstract_embedded, full_status, full_chunk_count = _embed_status(arxiv_id)
    return templates.TemplateResponse(
        request,
        "arxiv_paper_actions.html",
        {
            "paper": paper,
            "abstract_embedded": abstract_embedded,
            "full_status": full_status,
            "full_chunk_count": full_chunk_count,
        },
    )


@router.get("/arxiv/{arxiv_id}/view", response_class=HTMLResponse)
def arxiv_paper_view(request: Request, arxiv_id: str) -> HTMLResponse:
    """Render the locally cached arXiv HTML inside our base template.

    Requires the paper to have been through the full-paper embed flow so the
    HTML cache exists at ``ARXIV_PAPERS_DIR / "{id}.html"``. Returns 404 if
    the cache is missing.
    """
    paper = _fetch_paper(arxiv_id)
    html_path = paths.ARXIV_PAPERS_DIR / f"{arxiv_id}.html"
    if not html_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No cached HTML for {arxiv_id}. Click 'Embed full paper' first.",
        )
    body_html = prepare_local_view(html_path.read_text(encoding="utf-8"), arxiv_id)
    return templates.TemplateResponse(
        request,
        "arxiv_paper_view.html",
        {"current_page": "arxiv", "paper": paper, "body_html": body_html},
    )


@router.post("/arxiv/{arxiv_id}/embed-paper", response_class=HTMLResponse)
def arxiv_embed_paper(request: Request, arxiv_id: str) -> Response:
    """Enqueue a full-paper embed; spawn a worker if no active job exists."""
    _fetch_paper(arxiv_id)  # 404 if missing
    jobs_conn = arxiv_jobs.connect_arxiv_jobs(paths.JOBS_DB)
    try:
        active = arxiv_jobs.get_active_job(jobs_conn)
        if active is not None:
            arxiv_jobs.append_items(jobs_conn, active["id"], [arxiv_id])
        else:
            job_id = arxiv_jobs.create_job(
                jobs_conn,
                str(paths.ARXIV_EMBED_LOG),
                triggered_by_arxiv_id=arxiv_id,
            )
            arxiv_jobs.append_items(jobs_conn, job_id, [arxiv_id])
            spawn_worker("workers.arxiv_embed", "arxiv", job_id, "embed")
    finally:
        jobs_conn.close()
    return htmx_redirect("/arxiv/active-embedding", request)
