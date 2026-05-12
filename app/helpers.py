"""Pure helpers used across routes.

These don't manage application lifecycle and depend only on their arguments
(plus module-level constants from ``app.config`` and the ``paths`` module).
"""
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime

from fastapi import Request, Response
from fastapi.responses import RedirectResponse

import db as wiki_db
import paths
from app.config import REDIRECT_MAX_HOPS, SEARCH_LIMIT, WIKI_LABELS
from app.deps import connect


def wiki_label(wiki: str) -> str:
    """Return the display label for ``wiki``, falling back to ``wiki`` itself."""
    return WIKI_LABELS.get(wiki, wiki)


def escape_fts5(q: str) -> str:
    """Wrap a raw query in FTS5 phrase quotes, escaping internal double-quotes."""
    return '"' + q.replace('"', '""') + '"'


def search_titles(q: str, request: Request) -> list[str]:
    """Find article titles matching ``q`` using the FTS5 trigram index.

    The ``articles_fts`` virtual table uses the ``trigram`` tokenizer, which
    supports both prefix and substring matching in a single indexed query.
    Queries shorter than 3 characters fall back to a prefix LIKE on
    ``idx_articles_title`` because the trigram index requires at least 3 chars.

    Returns up to ``SEARCH_LIMIT`` titles ordered by BM25 relevance rank.
    """
    q = q.strip()
    if not q:
        return []

    conn = connect(request)
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
            (escape_fts5(q), SEARCH_LIMIT),
        )
        return [r["title"] for r in cur.fetchall()]
    finally:
        conn.close()


def fetch_article(
    title: str, request: Request
) -> tuple[sqlite3.Row | None, str | None]:
    """Look up an article by title, following ``#REDIRECT`` chains.

    A third of the rows in a typical enwiki dump are redirect stubs whose
    ``text_content`` is just ``#REDIRECT [[Target]]``. Follow those up to
    ``REDIRECT_MAX_HOPS`` times so the user sees the real target article.

    Returns ``(row, redirected_from)``; ``redirected_from`` is the *original*
    title if at least one redirect was followed, otherwise ``None``.
    """
    conn = connect(request)
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
        return None, None
    finally:
        conn.close()


def format_elapsed(started_at: str) -> str:
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


def format_started_at(started_at: str) -> str:
    try:
        dt = datetime.fromisoformat(started_at)
        return dt.strftime("%-d %b %Y %H:%M UTC")
    except Exception:
        return started_at


def format_embedded_at(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts


def spawn_worker(module: str, wiki: str, job_id: int, log_suffix: str) -> None:
    """Spawn a worker subprocess as a detached, log-redirected process.

    Args:
        module: Dotted module path, e.g. ``"workers.refresh"``.
        wiki: Wiki name used to derive the log file name.
        job_id: Job id passed to the worker's CLI.
        log_suffix: Tag for the log file name (``"refresh"`` or ``"embed"``).
    """
    log_path = paths.BASE_DIR / "dumps" / f"{wiki}_{log_suffix}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as log:
        subprocess.Popen(
            [sys.executable, "-m", module, "--wiki", wiki, "--job-id", str(job_id)],
            cwd=paths.BASE_DIR,
            start_new_session=True,
            stdout=log,
            stderr=log,
        )


def htmx_redirect(url: str, request: Request) -> Response:
    """Return a 204 with ``HX-Redirect`` for HTMX requests, else a 303 redirect.

    Lets the same code path serve both HTMX-driven UI and plain HTTP clients.
    """
    if request.headers.get("hx-request") or "HX-Request" in request.headers:
        return Response(status_code=204, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)
