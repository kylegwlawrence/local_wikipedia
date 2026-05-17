"""arXiv search routes — shell page + HTMX search fragment.

Both routes are read-only over ``dumps/arxiv.db`` (metadata) and
``dumps/arxiv_rag.db`` (embeddings + FTS). The shell page is a single
search input that fires ``GET /arxiv/search`` and swaps the response
fragment into ``#arxiv-results``. Search-result cards link out to
``arxiv.org/abs/{id}`` — there is no local reading view in v1.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

import paths
from app.config import templates
from arxiv import retriever
from arxiv.schema import connect_arxiv_rag, connect_papers

router = APIRouter()


@router.get("/arxiv", response_class=HTMLResponse)
def arxiv_index(request: Request) -> HTMLResponse:
    """Render the arXiv search shell."""
    return templates.TemplateResponse(
        request,
        "arxiv.html",
        {"current_page": "arxiv"},
    )


@router.get("/arxiv/search", response_class=HTMLResponse)
def arxiv_search(request: Request, q: str = "") -> HTMLResponse:
    """Return an HTMX fragment with the top results for ``q``."""
    query = q.strip()
    ready = paths.ARXIV_DB.exists() and paths.ARXIV_RAG_DB.exists()
    if not query:
        return templates.TemplateResponse(
            request,
            "arxiv_results.html",
            {"hits": [], "q": q, "ready": ready, "used_dense": False},
        )
    if not ready:
        return templates.TemplateResponse(
            request,
            "arxiv_results.html",
            {"hits": [], "q": q, "ready": False, "used_dense": False},
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
        {
            "hits": result.hits,
            "q": q,
            "ready": True,
            "used_dense": result.used_dense,
        },
    )
