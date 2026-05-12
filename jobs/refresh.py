"""CRUD helpers for the refresh_jobs table in dumps/jobs.db."""
import pathlib
import sqlite3


def connect_jobs(path: pathlib.Path) -> sqlite3.Connection:
    """Open (or create) jobs.db and ensure the schema exists."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_jobs_schema(conn)
    return conn


def ensure_jobs_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS refresh_jobs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            wiki                TEXT    NOT NULL,
            status              TEXT    NOT NULL DEFAULT 'pending',
            started_at          TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at          TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            articles_scanned    INTEGER NOT NULL DEFAULT 0,
            articles_skipped    INTEGER NOT NULL DEFAULT 0,
            articles_updated    INTEGER NOT NULL DEFAULT 0,
            articles_inserted   INTEGER NOT NULL DEFAULT 0,
            articles_archived   INTEGER NOT NULL DEFAULT 0,
            error_message       TEXT,
            log_path            TEXT
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_refresh_jobs_wiki "
        "ON refresh_jobs(wiki, status)"
    )
    # Per-wiki mutable state. Currently tracks whether the FTS index is known
    # stale (set true before refresh starts; cleared after FTS rebuild succeeds).
    # If a worker dies between those two points, the lifespan hook in app.py
    # detects it on next startup and rebuilds.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wiki_state (
            wiki        TEXT    PRIMARY KEY,
            fts_dirty   INTEGER NOT NULL DEFAULT 0,
            updated_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def create_job(conn: sqlite3.Connection, wiki: str, log_path: str) -> int:
    cur = conn.execute(
        "INSERT INTO refresh_jobs (wiki, log_path) VALUES (?, ?)",
        (wiki, log_path),
    )
    conn.commit()
    return cur.lastrowid


def update_job(conn: sqlite3.Connection, job_id: int, **kwargs) -> None:
    """Update supplied fields plus updated_at. Commits immediately."""
    valid = {
        "status", "articles_scanned", "articles_skipped",
        "articles_updated", "articles_inserted", "articles_archived",
        "error_message", "log_path",
    }
    fields = {k: v for k, v in kwargs.items() if k in valid and v is not None}
    if not fields:
        return
    set_parts = [f"{k} = ?" for k in fields] + ["updated_at = datetime('now')"]
    values = list(fields.values()) + [job_id]
    conn.execute(
        f"UPDATE refresh_jobs SET {', '.join(set_parts)} WHERE id = ?",
        values,
    )
    conn.commit()


def get_latest_job(conn: sqlite3.Connection, wiki: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM refresh_jobs WHERE wiki = ? ORDER BY id DESC LIMIT 1",
        (wiki,),
    ).fetchone()


def get_active_job(conn: sqlite3.Connection, wiki: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM refresh_jobs WHERE wiki = ? "
        "AND status IN ('pending','downloading','parsing','rebuilding') "
        "ORDER BY id DESC LIMIT 1",
        (wiki,),
    ).fetchone()


def clear_orphaned_jobs(conn: sqlite3.Connection) -> int:
    """Mark any non-terminal jobs as failed (called on app startup).

    A worker subprocess that was killed mid-flight (server crash, OS reboot,
    OOM) leaves its row stuck in pending/downloading/parsing/rebuilding
    forever, which permanently blocks new refresh requests for that wiki.
    Returns the number of rows updated.
    """
    cur = conn.execute(
        "UPDATE refresh_jobs "
        "SET status = 'failed', "
        "    error_message = COALESCE(error_message, 'interrupted by server restart'), "
        "    updated_at = datetime('now') "
        "WHERE status IN ('pending','downloading','parsing','rebuilding')"
    )
    conn.commit()
    return cur.rowcount


def set_fts_dirty(conn: sqlite3.Connection, wiki: str, dirty: bool) -> None:
    """Mark the wiki's FTS index as stale (True) or up-to-date (False).

    Called by the refresh worker before article updates begin and again
    after the FTS rebuild succeeds. The startup hook reads this to detect
    workers that died between those two points.
    """
    conn.execute(
        "INSERT INTO wiki_state (wiki, fts_dirty, updated_at) "
        "VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(wiki) DO UPDATE SET "
        "    fts_dirty = excluded.fts_dirty, "
        "    updated_at = excluded.updated_at",
        (wiki, 1 if dirty else 0),
    )
    conn.commit()


def get_fts_dirty_wikis(conn: sqlite3.Connection) -> list[str]:
    """Return wiki names whose FTS index needs rebuilding."""
    rows = conn.execute(
        "SELECT wiki FROM wiki_state WHERE fts_dirty = 1"
    ).fetchall()
    return [r["wiki"] for r in rows]
