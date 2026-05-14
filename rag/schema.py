"""RAG database schema and connection helper.

All connections to the RAG database must go through ``connect_rag`` so that
the sqlite-vec extension is loaded before any queries run.
"""

import pathlib
import sqlite3

import sqlite_vec


def connect_rag(path: pathlib.Path) -> sqlite3.Connection:
    """Open the RAG SQLite database and return a ready-to-use connection.

    Loads the sqlite-vec extension and creates the schema if it doesn't exist.

    Args:
        path: Filesystem path to the RAG SQLite database file.

    Returns:
        A sqlite3.Connection with Row factory and sqlite-vec loaded.
    """
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    create_rag_schema(conn)
    return conn


def create_rag_schema(conn: sqlite3.Connection) -> None:
    """Create all RAG tables if they don't exist. Idempotent.

    Creates articles_meta, chunks, chunks_fts (FTS5 with porter stemming),
    chunks_vec (sqlite-vec 768-dim float32 embeddings), and _meta (key-value
    store for schema metadata such as the recorded embedding dimension).

    Args:
        conn: An open RAG database connection with sqlite-vec already loaded.
    """
    conn.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS _meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS articles_meta (
            page_id            INTEGER PRIMARY KEY,
            title              TEXT    NOT NULL,
            revision_id        INTEGER NOT NULL,
            embedded_at        TEXT,
            article_size_bytes INTEGER,
            links_embedded     INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id      INTEGER NOT NULL REFERENCES articles_meta(page_id),
            section      TEXT,
            chunk_index  INTEGER NOT NULL DEFAULT 0,
            text         TEXT    NOT NULL,
            text_length  INTEGER NOT NULL,
            chunk_type   TEXT    NOT NULL DEFAULT 'prose'
        );

        CREATE INDEX IF NOT EXISTS idx_chunks_page_id ON chunks(page_id);

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            text,
            content=chunks,
            content_rowid=chunk_id,
            tokenize='porter ascii'
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
            chunk_id INTEGER PRIMARY KEY,
            embedding FLOAT[768]
        );
    """)
    conn.commit()

    # Migrate existing databases: add new columns if absent.
    _col_migrations = [
        ("embedded_at", "ALTER TABLE articles_meta ADD COLUMN embedded_at TEXT"),
        ("article_size_bytes", "ALTER TABLE articles_meta ADD COLUMN article_size_bytes INTEGER"),
        ("links_embedded", "ALTER TABLE articles_meta ADD COLUMN links_embedded INTEGER NOT NULL DEFAULT 0"),
        ("chunk_type", "ALTER TABLE chunks ADD COLUMN chunk_type TEXT NOT NULL DEFAULT 'prose'"),
    ]
    for col_name, sql in _col_migrations:
        try:
            conn.execute(sql)
            if col_name == "links_embedded":
                conn.execute("UPDATE articles_meta SET links_embedded = 0 WHERE links_embedded IS NULL")
        except sqlite3.OperationalError:
            pass  # column already exists

    # Drop removed columns from existing databases.
    try:
        conn.execute("ALTER TABLE articles_meta DROP COLUMN categories")
    except sqlite3.OperationalError:
        pass  # column already absent

    conn.commit()


def get_embedding_dim(conn: sqlite3.Connection) -> int | None:
    """Return the stored embedding dimension, or None if not yet recorded."""
    row = conn.execute("SELECT value FROM _meta WHERE key='embedding_dim'").fetchone()
    return int(row["value"]) if row else None


def set_embedding_dim(conn: sqlite3.Connection, dim: int) -> None:
    """Write the embedding dimension to _meta. Caller is responsible for commit."""
    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('embedding_dim', ?)",
        (str(dim),),
    )
