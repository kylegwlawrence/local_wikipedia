"""SQLite schema for the parsed Wikipedia database."""

import sqlite3


def create_schema(conn: sqlite3.Connection) -> None:
    """Create the ``articles`` and ``parse_metadata`` tables plus indexes."""
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            page_id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            namespace INTEGER NOT NULL DEFAULT 0,
            revision_id INTEGER NOT NULL,
            parent_revision_id INTEGER,
            timestamp TEXT NOT NULL,
            contributor_username TEXT,
            contributor_id INTEGER,
            comment TEXT,
            text_bytes INTEGER,
            text_content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_articles_title ON articles(title)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_articles_namespace ON articles(namespace)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_articles_timestamp ON articles(timestamp)")

    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
            title,
            content=articles,
            content_rowid=page_id,
            tokenize='trigram'
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS db_metadata (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS parse_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wiki TEXT NOT NULL,
            source_file TEXT NOT NULL,
            total_pages INTEGER NOT NULL,
            articles_count INTEGER NOT NULL,
            parse_started_at TEXT NOT NULL,
            parse_completed_at TEXT NOT NULL,
            parse_duration_seconds REAL NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS articles_archive (
            archive_id           INTEGER PRIMARY KEY AUTOINCREMENT,
            archived_at          TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            page_id              INTEGER NOT NULL,
            title                TEXT    NOT NULL,
            namespace            INTEGER NOT NULL DEFAULT 0,
            revision_id          INTEGER NOT NULL,
            parent_revision_id   INTEGER,
            timestamp            TEXT    NOT NULL,
            contributor_username TEXT,
            contributor_id       INTEGER,
            comment              TEXT,
            text_bytes           INTEGER,
            text_content         TEXT    NOT NULL,
            created_at           TEXT    NOT NULL
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_archive_page_id ON articles_archive(page_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_archive_archived_at ON articles_archive(archived_at)")

    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA page_size=4096")
    cursor.execute("PRAGMA synchronous=NORMAL")

    conn.commit()
