"""Routes for the ``/active-embedding`` job-management UI.

Named after the URL prefix rather than ``jobs.py`` so the route file's name
doesn't collide with the top-level ``jobs/`` CRUD package.
"""

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse

import paths
from app.config import templates
from app.deps import active_wiki
from app.helpers import htmx_redirect
from app.panels import render_active_embedding_panel, render_job_list_panel
from jobs import embed as embed_jobs

router = APIRouter()


@router.get("/active-embedding", response_class=HTMLResponse)
def active_embedding(request: Request, panel_page: int = 1) -> HTMLResponse:
    wiki = active_wiki(request)
    return render_active_embedding_panel(request, wiki, fragment_only=False, panel_page=panel_page)


@router.get("/active-embedding/running-count", response_class=HTMLResponse)
def running_jobs_count(request: Request) -> HTMLResponse:
    wiki = active_wiki(request)
    conn = embed_jobs.connect_embed_jobs(paths.JOBS_DB)
    try:
        count = embed_jobs.count_active_jobs(conn, wiki)
    finally:
        conn.close()
    return templates.TemplateResponse(request, "_sidebar_jobs_count.html", {"count": count})


@router.get("/active-embedding/jobs", response_class=HTMLResponse)
def active_embedding_jobs(request: Request, q: str = "", page: int = 1) -> HTMLResponse:
    wiki = active_wiki(request)
    return render_job_list_panel(request, wiki, q=q, page=page)


@router.delete("/active-embedding/jobs")
def delete_all_jobs(request: Request) -> Response:
    """Delete all embed job records and their items."""
    conn = embed_jobs.connect_embed_jobs(paths.JOBS_DB)
    try:
        embed_jobs.delete_all_jobs(conn)
    finally:
        conn.close()
    return htmx_redirect("/active-embedding", request)


@router.get("/active-embedding/panel", response_class=HTMLResponse)
def active_embedding_panel(request: Request, panel_page: int = 1) -> HTMLResponse:
    wiki = active_wiki(request)
    return render_active_embedding_panel(request, wiki, fragment_only=True, panel_page=panel_page)


@router.get("/active-embedding/panel/{job_id}", response_class=HTMLResponse)
def active_embedding_panel_for_job(request: Request, job_id: int, panel_page: int = 1) -> HTMLResponse:
    wiki = active_wiki(request)
    return render_active_embedding_panel(request, wiki, fragment_only=True, job_id=job_id, panel_page=panel_page)


@router.post("/active-embedding/cancel/{job_id}", response_class=HTMLResponse)
def active_embedding_cancel(request: Request, job_id: int) -> HTMLResponse:
    wiki = active_wiki(request)
    conn = embed_jobs.connect_embed_jobs(paths.JOBS_DB)
    try:
        embed_jobs.request_cancel(conn, job_id)
    finally:
        conn.close()
    return render_active_embedding_panel(request, wiki, fragment_only=True)
