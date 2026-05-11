"""Tests for rag/schema.py — schema creation and idempotence."""
import sqlite3

import pytest

from rag.schema import connect_rag, create_rag_schema, get_embedding_dim, set_embedding_dim


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow') "
        "UNION SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {r[0] for r in rows}


def test_connect_rag_creates_schema(tmp_path):
    conn = connect_rag(tmp_path / "test.db")
    names = _table_names(conn)
    assert "articles_meta" in names
    assert "chunks" in names
    conn.close()


def test_create_rag_schema_idempotent(tmp_path):
    conn = connect_rag(tmp_path / "test.db")
    create_rag_schema(conn)  # second call should not raise
    names = _table_names(conn)
    assert "chunks" in names
    conn.close()


def test_chunks_fts_exists(tmp_path):
    conn = connect_rag(tmp_path / "test.db")
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_fts'"
    ).fetchall()
    assert rows, "chunks_fts virtual table not found"
    conn.close()


def test_chunks_vec_exists(tmp_path):
    conn = connect_rag(tmp_path / "test.db")
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE name='chunks_vec'"
    ).fetchall()
    assert rows, "chunks_vec virtual table not found"
    conn.close()


def test_chunks_index_exists(tmp_path):
    conn = connect_rag(tmp_path / "test.db")
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_chunks_page_id'"
    ).fetchall()
    assert rows, "idx_chunks_page_id index not found"
    conn.close()


def test_meta_table_exists(tmp_path):
    conn = connect_rag(tmp_path / "test.db")
    names = _table_names(conn)
    assert "_meta" in names
    conn.close()


def test_get_embedding_dim_returns_none_when_not_set(tmp_path):
    conn = connect_rag(tmp_path / "test.db")
    assert get_embedding_dim(conn) is None
    conn.close()


def test_set_and_get_embedding_dim(tmp_path):
    conn = connect_rag(tmp_path / "test.db")
    set_embedding_dim(conn, 768)
    conn.commit()
    assert get_embedding_dim(conn) == 768
    conn.close()


def test_set_embedding_dim_overwrites(tmp_path):
    conn = connect_rag(tmp_path / "test.db")
    set_embedding_dim(conn, 768)
    set_embedding_dim(conn, 1024)
    conn.commit()
    assert get_embedding_dim(conn) == 1024
    conn.close()


def test_insert_and_retrieve_chunk(tmp_path):
    conn = connect_rag(tmp_path / "test.db")
    conn.execute(
        "INSERT INTO articles_meta (page_id, title, revision_id) VALUES (1, 'Test', 100)"
    )
    conn.execute(
        "INSERT INTO chunks (page_id, section, chunk_index, text, text_length) "
        "VALUES (1, NULL, 0, 'Hello world', 11)"
    )
    conn.commit()
    row = conn.execute("SELECT text FROM chunks WHERE page_id=1").fetchone()
    assert row["text"] == "Hello world"
    conn.close()
