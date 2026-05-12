"""Refresh-job routes: kick off a refresh and poll its status."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

import paths
from app.helpers import spawn_worker
from app.panels import render_status_panel
from jobs import refresh as refresh_jobs
from paths import KNOWN_WIKIS

router = APIRouter()


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
