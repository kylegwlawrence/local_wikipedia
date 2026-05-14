"""Refresh-job routes: kick off a refresh and poll its status."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

import paths
from app.config import templates
from app.deps import active_wiki
from app.helpers import spawn_worker
from app.panels import render_status_panel
from jobs import refresh as refresh_jobs
from paths import KNOWN_WIKIS

router = APIRouter()


@router.get("/refresh", response_class=HTMLResponse)
def refresh_page(request: Request) -> HTMLResponse:
    wiki = active_wiki(request)
    other_wiki = next(w for w in KNOWN_WIKIS if w != wiki)
    other_wiki_for_template = other_wiki if paths.db_path_for(other_wiki).exists() else None
    conn = refresh_jobs.connect_jobs(paths.JOBS_DB)
    try:
        jobs = {w: refresh_jobs.get_latest_job(conn, w) for w in KNOWN_WIKIS}
    finally:
        conn.close()
    return templates.TemplateResponse(
        request,
        "refresh.html",
        {
            "wiki": wiki,
            "other_wiki": other_wiki_for_template,
            "current_page": "refresh",
            "breadcrumb_current": "Refresh",
            "page_title": "Refresh",
            "jobs": jobs,
        },
    )


@router.post("/refresh/{wiki}", response_class=HTMLResponse)
def refresh_wiki(request: Request, wiki: str) -> HTMLResponse:
    if wiki not in KNOWN_WIKIS:
        raise HTTPException(status_code=400, detail=f"Unknown wiki: {wiki}")

    conn = refresh_jobs.connect_jobs(paths.JOBS_DB)
    try:
        conn.execute("BEGIN IMMEDIATE")
        active = refresh_jobs.get_active_job(conn, wiki)
        if active:
            conn.rollback()
            return render_status_panel(request, wiki, active)

        log_path = str(paths.BASE_DIR / "dumps" / f"{wiki}_refresh.log")
        job_id = refresh_jobs.create_job(conn, wiki, log_path)
        conn.commit()
        job = refresh_jobs.get_latest_job(conn, wiki)
    finally:
        conn.close()

    spawn_worker("workers.refresh", wiki, job_id, "refresh")

    return render_status_panel(request, wiki, job)


@router.get("/refresh/status/{wiki}", response_class=HTMLResponse)
def refresh_status(request: Request, wiki: str) -> HTMLResponse:
    if wiki not in KNOWN_WIKIS:
        raise HTTPException(status_code=400, detail=f"Unknown wiki: {wiki}")

    conn = refresh_jobs.connect_jobs(paths.JOBS_DB)
    try:
        job = refresh_jobs.get_latest_job(conn, wiki)
    finally:
        conn.close()

    return render_status_panel(request, wiki, job)
