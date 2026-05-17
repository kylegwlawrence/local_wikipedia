"""CRUD helpers for the arXiv full-paper embed jobs in ``dumps/jobs.db``.

A "job" is a batch of arxiv_ids to embed, triggered from the abstract page
("Embed full paper" button). Items are deduped by ``(job_id, arxiv_id)`` so
multiple clicks while a job is running append to the same job without
redundant work.

Lives in the same ``jobs.db`` as the wiki refresh / embed jobs but uses
its own tables (``arxiv_embed_jobs`` / ``arxiv_embed_job_items``). There
is no ``wiki`` dimension — arXiv is a single source.
"""

import pathlib
import sqlite3


def connect_arxiv_jobs(path: pathlib.Path) -> sqlite3.Connection:
    """Open (or create) jobs.db and ensure the arxiv-embed-jobs schema exists."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the arxiv_embed_jobs and arxiv_embed_job_items tables. Idempotent."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS arxiv_embed_jobs (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            status                   TEXT    NOT NULL DEFAULT 'running',
            cancel_requested         INTEGER NOT NULL DEFAULT 0,
            started_at               TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at               TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at              TEXT,
            log_path                 TEXT,
            error_message            TEXT,
            triggered_by_arxiv_id    TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_arxiv_embed_jobs_status ON arxiv_embed_jobs(status)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS arxiv_embed_job_items (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id         INTEGER NOT NULL REFERENCES arxiv_embed_jobs(id),
            arxiv_id       TEXT    NOT NULL,
            status         TEXT    NOT NULL DEFAULT 'queued',
            chunk_count    INTEGER NOT NULL DEFAULT 0,
            error_message  TEXT,
            enqueued_at    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at    TEXT,
            UNIQUE(job_id, arxiv_id)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_arxiv_embed_job_items_job_status ON arxiv_embed_job_items(job_id, status)"
    )
    conn.commit()


# --- Jobs --------------------------------------------------------------------


def create_job(
    conn: sqlite3.Connection,
    log_path: str,
    triggered_by_arxiv_id: str | None = None,
    status: str = "running",
) -> int:
    """Insert a new job and return its id."""
    cur = conn.execute(
        "INSERT INTO arxiv_embed_jobs (log_path, triggered_by_arxiv_id, status) VALUES (?, ?, ?)",
        (log_path, triggered_by_arxiv_id, status),
    )
    conn.commit()
    return cur.lastrowid


def get_job(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM arxiv_embed_jobs WHERE id = ?", (job_id,)).fetchone()


def get_active_job(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Return the running, non-cancelled job if one exists.

    "Active" here is the gate the route uses to decide whether to append to
    an existing job vs spawn a new worker — a cancelled job is excluded so
    the next click starts fresh.
    """
    return conn.execute(
        "SELECT * FROM arxiv_embed_jobs WHERE status = 'running' AND cancel_requested = 0 ORDER BY id DESC LIMIT 1"
    ).fetchone()


def get_latest_jobs(conn: sqlite3.Connection, limit: int = 5) -> list[sqlite3.Row]:
    """Return the most recent jobs (any status)."""
    return conn.execute("SELECT * FROM arxiv_embed_jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def mark_job(
    conn: sqlite3.Connection,
    job_id: int,
    status: str,
    error_message: str | None = None,
) -> None:
    """Set a job's final status and finished_at timestamp."""
    conn.execute(
        "UPDATE arxiv_embed_jobs SET status = ?, error_message = ?, "
        "finished_at = datetime('now'), updated_at = datetime('now') WHERE id = ?",
        (status, error_message, job_id),
    )
    conn.commit()


def request_cancel(conn: sqlite3.Connection, job_id: int) -> None:
    """Set the cancel_requested flag; the worker checks it between items."""
    conn.execute(
        "UPDATE arxiv_embed_jobs SET cancel_requested = 1, updated_at = datetime('now') WHERE id = ?",
        (job_id,),
    )
    conn.commit()


def touch_job(conn: sqlite3.Connection, job_id: int) -> None:
    """Bump ``updated_at`` — the worker calls this between items as a liveness signal."""
    conn.execute("UPDATE arxiv_embed_jobs SET updated_at = datetime('now') WHERE id = ?", (job_id,))
    conn.commit()


def clear_orphaned_jobs(conn: sqlite3.Connection) -> int:
    """Mark stuck-running jobs (and their in-progress items) as failed.

    Called on app startup. A worker subprocess killed mid-flight leaves its
    job row stuck in ``running`` forever, which would block new requests.
    Returns the number of job rows updated.
    """
    conn.execute(
        "UPDATE arxiv_embed_job_items SET status = 'failed', "
        "error_message = COALESCE(error_message, 'interrupted by server restart'), "
        "finished_at = datetime('now') "
        "WHERE status = 'in_progress' "
        "AND job_id IN (SELECT id FROM arxiv_embed_jobs WHERE status = 'running')"
    )
    cur = conn.execute(
        "UPDATE arxiv_embed_jobs SET status = 'failed', "
        "error_message = COALESCE(error_message, 'interrupted by server restart'), "
        "finished_at = datetime('now'), updated_at = datetime('now') WHERE status = 'running'"
    )
    conn.commit()
    return cur.rowcount


# --- Items -------------------------------------------------------------------


def append_items(conn: sqlite3.Connection, job_id: int, arxiv_ids: list[str]) -> int:
    """Insert ``arxiv_id`` rows for ``job_id``, deduping against the UNIQUE constraint.

    Re-adding an arxiv_id that's already in any status is a no-op. Returns
    the number of rows actually inserted.
    """
    if not arxiv_ids:
        return 0
    cur = conn.executemany(
        "INSERT OR IGNORE INTO arxiv_embed_job_items (job_id, arxiv_id) VALUES (?, ?)",
        [(job_id, aid) for aid in arxiv_ids],
    )
    conn.commit()
    return cur.rowcount


def get_items(conn: sqlite3.Connection, job_id: int) -> list[sqlite3.Row]:
    """Return all items for ``job_id`` in insertion order."""
    return conn.execute("SELECT * FROM arxiv_embed_job_items WHERE job_id = ? ORDER BY id", (job_id,)).fetchall()


def get_next_queued(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    """Return the oldest queued item for ``job_id``, or None if drained."""
    return conn.execute(
        "SELECT * FROM arxiv_embed_job_items WHERE job_id = ? AND status = 'queued' ORDER BY id LIMIT 1",
        (job_id,),
    ).fetchone()


def update_item(
    conn: sqlite3.Connection,
    item_id: int,
    status: str,
    chunk_count: int | None = None,
    error_message: str | None = None,
) -> None:
    """Update an item's status, optionally setting chunk_count / error_message.

    Sets ``finished_at`` when the status is terminal (not ``queued`` /
    ``in_progress``).
    """
    terminal = status not in ("queued", "in_progress")
    if terminal:
        conn.execute(
            "UPDATE arxiv_embed_job_items SET status = ?, "
            "chunk_count = COALESCE(?, chunk_count), error_message = ?, "
            "finished_at = datetime('now') WHERE id = ?",
            (status, chunk_count, error_message, item_id),
        )
    else:
        conn.execute(
            "UPDATE arxiv_embed_job_items SET status = ?, "
            "chunk_count = COALESCE(?, chunk_count), error_message = ? WHERE id = ?",
            (status, chunk_count, error_message, item_id),
        )
    conn.commit()


def count_items_by_status(conn: sqlite3.Connection, job_id: int) -> dict[str, int]:
    """Return ``{status: count}`` for the items in ``job_id``."""
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM arxiv_embed_job_items WHERE job_id = ? GROUP BY status",
        (job_id,),
    ).fetchall()
    return {r["status"]: r["n"] for r in rows}
