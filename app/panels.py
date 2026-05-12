"""Template-data builders for refresh + active-embedding panels.

These pack one or more queries against ``jobs.db`` into the shape each
template needs. Kept separate from the route handlers so the panels can be
re-rendered from multiple endpoints without duplicating the data wrangling.
"""

import math
import sqlite3

from fastapi import Request
from fastapi.responses import HTMLResponse

import paths
from app.config import templates
from app.helpers import format_elapsed, format_started_at
from jobs import embed as embed_jobs
from paths import KNOWN_WIKIS


def render_status_panel(request: Request, wiki: str, job: sqlite3.Row | None) -> HTMLResponse:
    if not job:
        return templates.TemplateResponse(
            request,
            "refresh_panel.html",
            {"wiki": wiki, "job": None, "elapsed": "", "started_at_display": ""},
        )
    active = job["status"] in ("pending", "downloading", "parsing", "rebuilding")
    elapsed = format_elapsed(job["started_at"]) if active else ""
    started_at_display = "" if active else format_started_at(job["started_at"])
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


def render_active_embedding_panel(
    request: Request,
    wiki: str,
    *,
    fragment_only: bool,
    job_id: int | None = None,
) -> HTMLResponse:
    """Render the active embedding panel (HTMX-polled inner content).

    If ``fragment_only`` is True returns just the panel fragment for HTMX
    polling; otherwise returns the full ``active_embedding.html`` page.
    If ``job_id`` is given, show that specific job instead of the most recent.
    """
    conn = embed_jobs.connect_embed_jobs(paths.JOBS_DB)
    try:
        if job_id is not None:
            active = embed_jobs.get_job(conn, job_id)
        else:
            active = embed_jobs.get_active_job(conn, wiki)
            if active is None:
                latest = embed_jobs.get_latest_jobs(conn, wiki, limit=1)
                active = latest[0] if latest else None

        items_by_job: dict[int, list[dict]] = {}
        counts_by_job: dict[int, dict[str, int]] = {}
        if active is not None:
            items_by_job[active["id"]] = [dict(r) for r in embed_jobs.get_items(conn, active["id"])]
            counts_by_job[active["id"]] = embed_jobs.count_items_by_status(conn, active["id"])

        list_jobs: list[sqlite3.Row] = []
        list_total = 0
        list_counts: dict[int, dict[str, int]] = {}
        if not fragment_only:
            list_jobs, list_total = embed_jobs.get_jobs_page(conn)
            list_counts = embed_jobs.get_item_counts_for_jobs(conn, [j["id"] for j in list_jobs])
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
        is_running = active_dict["status"] == "running" and not active_dict["cancel_requested"]
        elapsed = format_elapsed(active_dict["started_at"]) if is_running else ""
        started_at_display = "" if is_running else format_started_at(active_dict["started_at"])

    other_wiki = next(w for w in KNOWN_WIKIS if w != wiki)
    other_wiki_for_template = other_wiki if paths.db_path_for(other_wiki).exists() else None

    template = "active_embedding_panel.html" if fragment_only else "active_embedding.html"
    return templates.TemplateResponse(
        request,
        template,
        {
            "wiki": wiki,
            "other_wiki": other_wiki_for_template,
            "job": active_dict,
            "grouped_items": grouped_items,
            "counts": counts,
            "elapsed": elapsed,
            "started_at_display": started_at_display,
            "current_page": "processes",
            "list_jobs": [dict(j) for j in list_jobs],
            "list_total": list_total,
            "list_counts": list_counts,
            "list_page": 1,
            "list_total_pages": math.ceil(list_total / 5) if list_total else 0,
            "list_q": "",
        },
    )


def render_job_list_panel(
    request: Request,
    wiki: str,
    q: str = "",
    page: int = 1,
) -> HTMLResponse:
    """Render the paginated job list fragment for HTMX search/pagination."""
    conn = embed_jobs.connect_embed_jobs(paths.JOBS_DB)
    try:
        jobs, total = embed_jobs.get_jobs_page(conn, q=q, page=page)
        counts = embed_jobs.get_item_counts_for_jobs(conn, [j["id"] for j in jobs])
    finally:
        conn.close()
    return templates.TemplateResponse(
        request,
        "job_list_panel.html",
        {
            "list_jobs": [dict(j) for j in jobs],
            "list_total": total,
            "list_counts": counts,
            "list_page": page,
            "list_total_pages": math.ceil(total / 5) if total else 0,
            "list_q": q,
        },
    )
