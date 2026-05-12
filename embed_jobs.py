"""CRUD helpers for the embed_jobs / embed_job_items tables in dumps/jobs.db.

A "job" is a per-wiki batch of articles to embed, triggered when the user
clicks "Embed + links" on an article. Items are deduped by ``(job_id, title)``
so multiple clicks on different source articles append to the same active job
without redundant work.
"""
import pathlib
import sqlite3


def connect_embed_jobs(path: pathlib.Path) -> sqlite3.Connection:
    """Open (or create) jobs.db and ensure the embed-jobs schema exists."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_embed_schema(conn)
    return conn


def ensure_embed_schema(conn: sqlite3.Connection) -> None:
    """Create the embed_jobs and embed_job_items tables idempotently.

    Shares the database with ``refresh_jobs`` (see jobs.py) but uses separate
    tables — adding them here keeps the refresh-side schema untouched.
    """
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS embed_jobs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            wiki                TEXT    NOT NULL,
            status              TEXT    NOT NULL DEFAULT 'running',
            cancel_requested    INTEGER NOT NULL DEFAULT 0,
            started_at          TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at          TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at         TEXT,
            log_path            TEXT,
            error_message       TEXT,
            triggered_by_title  TEXT
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_embed_jobs_wiki_status "
        "ON embed_jobs(wiki, status)"
    )
    try:
        conn.execute("ALTER TABLE embed_jobs ADD COLUMN triggered_by_title TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists (existing database)
    try:
        conn.execute("ALTER TABLE embed_jobs ADD COLUMN include_links INTEGER NOT NULL DEFAULT 1")
    except sqlite3.OperationalError:
        pass  # column already exists (existing database)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS embed_job_items (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id         INTEGER NOT NULL REFERENCES embed_jobs(id),
            title          TEXT    NOT NULL,
            source_title   TEXT    NOT NULL,
            status         TEXT    NOT NULL DEFAULT 'queued',
            chunk_count    INTEGER NOT NULL DEFAULT 0,
            error_message  TEXT,
            enqueued_at    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at    TEXT,
            UNIQUE(job_id, title)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_embed_job_items_job_status "
        "ON embed_job_items(job_id, status)"
    )
    conn.commit()


# --- Jobs --------------------------------------------------------------------

def create_job(
    conn: sqlite3.Connection,
    wiki: str,
    log_path: str,
    triggered_by_title: str | None = None,
    include_links: int = 1,
) -> int:
    """Insert a new running job and return its id."""
    cur = conn.execute(
        "INSERT INTO embed_jobs (wiki, log_path, triggered_by_title, include_links) "
        "VALUES (?, ?, ?, ?)",
        (wiki, log_path, triggered_by_title, include_links),
    )
    conn.commit()
    return cur.lastrowid


def get_job(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM embed_jobs WHERE id = ?", (job_id,)
    ).fetchone()


def get_active_job(conn: sqlite3.Connection, wiki: str) -> sqlite3.Row | None:
    """Return the running, non-cancelled job for ``wiki`` if one exists."""
    return conn.execute(
        "SELECT * FROM embed_jobs WHERE wiki = ? "
        "AND status = 'running' AND cancel_requested = 0 "
        "ORDER BY id DESC LIMIT 1",
        (wiki,),
    ).fetchone()


def get_latest_jobs(
    conn: sqlite3.Connection, wiki: str, limit: int = 5
) -> list[sqlite3.Row]:
    """Return the most recent jobs for ``wiki`` (any status)."""
    return conn.execute(
        "SELECT * FROM embed_jobs WHERE wiki = ? ORDER BY id DESC LIMIT ?",
        (wiki, limit),
    ).fetchall()


def mark_job(
    conn: sqlite3.Connection,
    job_id: int,
    status: str,
    error_message: str | None = None,
) -> None:
    """Set a job's final status and finished_at timestamp."""
    conn.execute(
        "UPDATE embed_jobs SET status = ?, error_message = ?, "
        "finished_at = datetime('now'), updated_at = datetime('now') "
        "WHERE id = ?",
        (status, error_message, job_id),
    )
    conn.commit()


def request_cancel(conn: sqlite3.Connection, job_id: int) -> None:
    """Set the cancel_requested flag; the worker checks it between items."""
    conn.execute(
        "UPDATE embed_jobs SET cancel_requested = 1, "
        "updated_at = datetime('now') WHERE id = ?",
        (job_id,),
    )
    conn.commit()


def touch_job(conn: sqlite3.Connection, job_id: int) -> None:
    """Bump ``updated_at`` — used by the worker for liveness."""
    conn.execute(
        "UPDATE embed_jobs SET updated_at = datetime('now') WHERE id = ?",
        (job_id,),
    )
    conn.commit()


def clear_orphaned_jobs(conn: sqlite3.Connection) -> int:
    """Mark any in-flight embed jobs as failed (called on app startup).

    A worker subprocess that was killed mid-flight leaves its job row stuck
    in 'running' forever, which permanently blocks new embed requests. Items
    sitting in 'in_progress' under those jobs are also reset to 'failed' so
    the per-article view doesn't show ghost activity.
    Returns the number of job rows updated.
    """
    conn.execute(
        "UPDATE embed_job_items "
        "SET status = 'failed', "
        "    error_message = COALESCE(error_message, 'interrupted by server restart'), "
        "    finished_at = datetime('now') "
        "WHERE status = 'in_progress' "
        "AND job_id IN (SELECT id FROM embed_jobs WHERE status = 'running')"
    )
    cur = conn.execute(
        "UPDATE embed_jobs "
        "SET status = 'failed', "
        "    error_message = COALESCE(error_message, 'interrupted by server restart'), "
        "    finished_at = datetime('now'), "
        "    updated_at = datetime('now') "
        "WHERE status = 'running'"
    )
    conn.commit()
    return cur.rowcount


# --- Items -------------------------------------------------------------------

def append_items(
    conn: sqlite3.Connection,
    job_id: int,
    items: list[tuple[str, str]],
) -> int:
    """Insert ``(title, source_title)`` pairs, ignoring duplicates.

    Args:
        conn: jobs.db connection.
        job_id: Parent job id.
        items: List of ``(canonical_title, source_title)`` tuples.

    Returns:
        Number of rows actually inserted (excluding duplicates the unique
        index rejected).
    """
    if not items:
        return 0
    cur = conn.executemany(
        "INSERT OR IGNORE INTO embed_job_items (job_id, title, source_title) "
        "VALUES (?, ?, ?)",
        [(job_id, t, s) for t, s in items],
    )
    conn.commit()
    return cur.rowcount


def get_items(conn: sqlite3.Connection, job_id: int) -> list[sqlite3.Row]:
    """Return all items for ``job_id`` in insertion order."""
    return conn.execute(
        "SELECT * FROM embed_job_items WHERE job_id = ? ORDER BY id",
        (job_id,),
    ).fetchall()


def get_next_queued(
    conn: sqlite3.Connection, job_id: int
) -> sqlite3.Row | None:
    """Return the oldest queued item for ``job_id``, or None if the queue is empty."""
    return conn.execute(
        "SELECT * FROM embed_job_items WHERE job_id = ? AND status = 'queued' "
        "ORDER BY id LIMIT 1",
        (job_id,),
    ).fetchone()


def update_item(
    conn: sqlite3.Connection,
    item_id: int,
    status: str,
    chunk_count: int | None = None,
    error_message: str | None = None,
) -> None:
    """Update an item's status and optionally chunk_count / error_message.

    Sets ``finished_at`` when the new status is terminal (anything other than
    ``queued`` or ``in_progress``).
    """
    terminal = status not in ("queued", "in_progress")
    if terminal:
        conn.execute(
            "UPDATE embed_job_items SET status = ?, chunk_count = COALESCE(?, chunk_count), "
            "error_message = ?, finished_at = datetime('now') WHERE id = ?",
            (status, chunk_count, error_message, item_id),
        )
    else:
        conn.execute(
            "UPDATE embed_job_items SET status = ?, chunk_count = COALESCE(?, chunk_count), "
            "error_message = ? WHERE id = ?",
            (status, chunk_count, error_message, item_id),
        )
    conn.commit()


def count_items_by_status(
    conn: sqlite3.Connection, job_id: int
) -> dict[str, int]:
    """Return ``{status: count}`` for the items in ``job_id``."""
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM embed_job_items "
        "WHERE job_id = ? GROUP BY status",
        (job_id,),
    ).fetchall()
    return {r["status"]: r["n"] for r in rows}


def get_item_counts_for_jobs(
    conn: sqlite3.Connection, job_ids: list[int]
) -> dict[int, dict[str, int]]:
    """Return ``{job_id: {status: count}}`` for all jobs in ``job_ids``."""
    if not job_ids:
        return {}
    placeholders = ",".join("?" * len(job_ids))
    rows = conn.execute(
        f"SELECT job_id, status, COUNT(*) AS n FROM embed_job_items "
        f"WHERE job_id IN ({placeholders}) GROUP BY job_id, status",
        job_ids,
    ).fetchall()
    result: dict[int, dict[str, int]] = {}
    for r in rows:
        result.setdefault(r["job_id"], {})[r["status"]] = r["n"]
    return result


def get_jobs_page(
    conn: sqlite3.Connection,
    q: str = "",
    page: int = 1,
    per_page: int = 5,
) -> tuple[list[sqlite3.Row], int]:
    """Return ``(rows, total)`` across all wikis with pagination and optional search.

    If ``q`` is a digit string (optionally prefixed with ``#``) the search
    filters by job id.  Otherwise it filters by ``triggered_by_title LIKE``.
    """
    where_clauses: list[str] = []
    params: list = []
    q = q.strip()
    if q:
        stripped = q.lstrip("#")
        if stripped.isdigit():
            where_clauses.append("id = ?")
            params.append(int(stripped))
        else:
            where_clauses.append("triggered_by_title LIKE ?")
            params.append(f"%{q}%")
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    total: int = conn.execute(
        f"SELECT COUNT(*) FROM embed_jobs {where}", params
    ).fetchone()[0]
    offset = (page - 1) * per_page
    rows = conn.execute(
        f"SELECT * FROM embed_jobs {where} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [per_page, offset],
    ).fetchall()
    return rows, total


def record_sync_embed(
    conn: sqlite3.Connection,
    wiki: str,
    title: str,
    chunk_count: int = 0,
    error_message: str | None = None,
) -> int:
    """Record a completed synchronous single-article embed as a finished job.

    Creates both the ``embed_jobs`` row (already in a terminal status) and a
    single ``embed_job_items`` row so the job detail view shows what was done.
    Returns the new job id.
    """
    job_status = "failed" if error_message else "complete"
    item_status = "failed" if error_message else "completed"
    cur = conn.execute(
        "INSERT INTO embed_jobs "
        "(wiki, status, triggered_by_title, include_links, finished_at, updated_at) "
        "VALUES (?, ?, ?, 0, datetime('now'), datetime('now'))",
        (wiki, job_status, title),
    )
    job_id = cur.lastrowid
    conn.execute(
        "INSERT INTO embed_job_items "
        "(job_id, title, source_title, status, chunk_count, error_message, finished_at) "
        "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
        (job_id, title, title, item_status, chunk_count, error_message),
    )
    conn.commit()
    return job_id
