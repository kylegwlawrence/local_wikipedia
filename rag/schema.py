"""RAG database schema and connection helper.

All connections to the RAG database must go through ``connect_rag`` so that
the sqlite-vec extension is loaded before any queries run.
"""
import pathlib
import sqlite3

import sqlite_vec


def connect_rag(path: pathlib.Path) -> sqlite3.Connection:
    """Open the RAG SQLite database, load sqlite-vec, create schema if needed."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    create_rag_schema(conn)
    return conn


def create_rag_schema(conn: sqlite3.Connection) -> None:
    """Create all RAG tables if they don't exist. Idempotent."""
    conn.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS articles_meta (
            page_id      INTEGER PRIMARY KEY,
            title        TEXT    NOT NULL,
            revision_id  INTEGER NOT NULL,
            categories   TEXT    NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id      INTEGER NOT NULL REFERENCES articles_meta(page_id),
            section      TEXT,
            chunk_index  INTEGER NOT NULL DEFAULT 0,
            text         TEXT    NOT NULL,
            text_length  INTEGER NOT NULL
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
