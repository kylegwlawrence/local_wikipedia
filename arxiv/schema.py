"""Schema and connection helpers for the arXiv metadata + RAG databases.

Two physical databases live in ``dumps/``:

* ``arxiv.db`` — paper metadata harvested from OAI-PMH. Source of truth for
  what the search results render. ``papers.oai_datestamp`` drives the
  re-embed decision.
* ``arxiv_rag.db`` — one row per embedded paper in ``papers_meta`` plus the
  FTS5 / sqlite-vec virtual tables. Kept separate from the metadata DB so an
  ingest that rewrites ``papers`` never has to coordinate with an embed
  worker holding the RAG DB open.

Every connection must go through these helpers — ``connect_arxiv_rag`` in
particular has to load the sqlite-vec extension before any query against
``papers_vec`` will work.
"""

import pathlib
import sqlite3

import sqlite_vec


def connect_papers(path: pathlib.Path) -> sqlite3.Connection:
    """Open ``arxiv.db`` and ensure the metadata schema exists."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    create_papers_schema(conn)
    return conn


def connect_arxiv_rag(path: pathlib.Path) -> sqlite3.Connection:
    """Open ``arxiv_rag.db`` with sqlite-vec loaded and the schema ensured."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    create_arxiv_rag_schema(conn)
    return conn


def create_papers_schema(conn: sqlite3.Connection) -> None:
    """Create the metadata tables in ``arxiv.db``. Idempotent."""
    conn.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS papers (
            id               TEXT PRIMARY KEY,
            oai_datestamp    TEXT NOT NULL,
            title            TEXT NOT NULL,
            abstract         TEXT NOT NULL,
            authors          TEXT NOT NULL,
            categories       TEXT NOT NULL,
            primary_category TEXT NOT NULL,
            submitted_date   TEXT NOT NULL,
            updated_date     TEXT,
            doi              TEXT,
            journal_ref      TEXT,
            comments         TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_papers_submitted     ON papers(submitted_date);
        CREATE INDEX IF NOT EXISTS idx_papers_primary_cat   ON papers(primary_category);

        CREATE TABLE IF NOT EXISTS ingest_state (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    conn.commit()


def create_arxiv_rag_schema(conn: sqlite3.Connection) -> None:
    """Create the embedding tables in ``arxiv_rag.db``. Idempotent."""
    conn.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS _meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS papers_meta (
            rowid         INTEGER PRIMARY KEY AUTOINCREMENT,
            arxiv_id      TEXT    NOT NULL UNIQUE,
            oai_datestamp TEXT    NOT NULL,
            embed_text    TEXT    NOT NULL,
            embedded_at   TEXT    NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
            embed_text,
            content='papers_meta',
            content_rowid='rowid',
            tokenize='porter ascii'
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS papers_vec USING vec0(
            rowid INTEGER PRIMARY KEY,
            embedding FLOAT[768]
        );
    """)
    conn.commit()


def get_ingest_state(conn: sqlite3.Connection, key: str) -> str | None:
    """Return the value stored at ``ingest_state[key]``, or None if unset."""
    row = conn.execute("SELECT value FROM ingest_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_ingest_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Upsert ``ingest_state[key] = value``. Caller is responsible for commit."""
    conn.execute(
        "INSERT OR REPLACE INTO ingest_state (key, value) VALUES (?, ?)",
        (key, value),
    )


def get_embedding_dim(conn: sqlite3.Connection) -> int | None:
    """Return the stored embedding dimension on ``arxiv_rag.db``, or None."""
    row = conn.execute("SELECT value FROM _meta WHERE key = 'embedding_dim'").fetchone()
    return int(row["value"]) if row else None


def set_embedding_dim(conn: sqlite3.Connection, dim: int) -> None:
    """Record the embedding dimension. Caller is responsible for commit."""
    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('embedding_dim', ?)",
        (str(dim),),
    )
