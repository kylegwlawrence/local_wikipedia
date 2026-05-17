"""Tests for arxiv/schema.py — schema creation, idempotence, state helpers."""

import sqlite3

from arxiv.schema import (
    connect_arxiv_rag,
    connect_papers,
    create_arxiv_rag_schema,
    create_papers_schema,
    get_embedding_dim,
    get_ingest_state,
    set_embedding_dim,
    set_ingest_state,
)


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


class TestPapersSchema:
    def test_connect_creates_tables(self, tmp_path):
        conn = connect_papers(tmp_path / "arxiv.db")
        names = _table_names(conn)
        assert "papers" in names
        assert "ingest_state" in names
        conn.close()

    def test_idempotent(self, tmp_path):
        conn = connect_papers(tmp_path / "arxiv.db")
        create_papers_schema(conn)
        names = _table_names(conn)
        assert "papers" in names
        conn.close()

    def test_papers_columns(self, tmp_path):
        conn = connect_papers(tmp_path / "arxiv.db")
        cols = {row[1] for row in conn.execute("PRAGMA table_info(papers)").fetchall()}
        for required in (
            "id",
            "oai_datestamp",
            "title",
            "abstract",
            "authors",
            "categories",
            "primary_category",
            "submitted_date",
            "updated_date",
            "doi",
            "journal_ref",
            "comments",
        ):
            assert required in cols, f"missing column: {required}"
        conn.close()

    def test_papers_indexes_present(self, tmp_path):
        conn = connect_papers(tmp_path / "arxiv.db")
        idx = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        assert "idx_papers_submitted" in idx
        assert "idx_papers_primary_cat" in idx
        conn.close()

    def test_insert_and_retrieve_paper(self, tmp_path):
        conn = connect_papers(tmp_path / "arxiv.db")
        conn.execute(
            "INSERT INTO papers (id, oai_datestamp, title, abstract, authors, "
            "categories, primary_category, submitted_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "2401.12345",
                "2024-01-22",
                "Test paper",
                "Abstract body.",
                '["Alice Smith"]',
                "cs.CL cs.LG",
                "cs.CL",
                "2024-01-22",
            ),
        )
        conn.commit()
        row = conn.execute("SELECT title, primary_category FROM papers WHERE id = ?", ("2401.12345",)).fetchone()
        assert row["title"] == "Test paper"
        assert row["primary_category"] == "cs.CL"
        conn.close()


class TestIngestState:
    def test_get_returns_none_when_unset(self, tmp_path):
        conn = connect_papers(tmp_path / "arxiv.db")
        assert get_ingest_state(conn, "last_harvested_date") is None
        conn.close()

    def test_set_and_get_roundtrip(self, tmp_path):
        conn = connect_papers(tmp_path / "arxiv.db")
        set_ingest_state(conn, "last_harvested_date", "2024-01-22")
        conn.commit()
        assert get_ingest_state(conn, "last_harvested_date") == "2024-01-22"
        conn.close()

    def test_set_overwrites(self, tmp_path):
        conn = connect_papers(tmp_path / "arxiv.db")
        set_ingest_state(conn, "k", "first")
        set_ingest_state(conn, "k", "second")
        conn.commit()
        assert get_ingest_state(conn, "k") == "second"
        conn.close()


class TestArxivRagSchema:
    def test_connect_creates_tables(self, tmp_path):
        conn = connect_arxiv_rag(tmp_path / "arxiv_rag.db")
        names = _table_names(conn)
        assert "papers_meta" in names
        assert "_meta" in names
        conn.close()

    def test_papers_fts_exists(self, tmp_path):
        conn = connect_arxiv_rag(tmp_path / "arxiv_rag.db")
        rows = conn.execute("SELECT name FROM sqlite_master WHERE name='papers_fts'").fetchall()
        assert rows, "papers_fts virtual table not found"
        conn.close()

    def test_papers_vec_exists(self, tmp_path):
        conn = connect_arxiv_rag(tmp_path / "arxiv_rag.db")
        rows = conn.execute("SELECT name FROM sqlite_master WHERE name='papers_vec'").fetchall()
        assert rows, "papers_vec virtual table not found"
        conn.close()

    def test_idempotent(self, tmp_path):
        conn = connect_arxiv_rag(tmp_path / "arxiv_rag.db")
        create_arxiv_rag_schema(conn)
        conn.close()

    def test_insert_papers_meta_and_fts_visible(self, tmp_path):
        conn = connect_arxiv_rag(tmp_path / "arxiv_rag.db")
        conn.execute(
            "INSERT INTO papers_meta (arxiv_id, oai_datestamp, embed_text, embedded_at) VALUES (?, ?, ?, ?)",
            ("2401.12345", "2024-01-22", "title body categories cs.CL", "2024-01-23T00:00:00Z"),
        )
        conn.commit()
        conn.execute("INSERT INTO papers_fts(papers_fts) VALUES('rebuild')")
        conn.commit()
        rows = conn.execute("SELECT rowid FROM papers_fts WHERE papers_fts MATCH 'body'").fetchall()
        assert rows, "FTS5 did not index inserted text"
        conn.close()


class TestEmbeddingDim:
    def test_returns_none_when_unset(self, tmp_path):
        conn = connect_arxiv_rag(tmp_path / "arxiv_rag.db")
        assert get_embedding_dim(conn) is None
        conn.close()

    def test_set_and_get(self, tmp_path):
        conn = connect_arxiv_rag(tmp_path / "arxiv_rag.db")
        set_embedding_dim(conn, 768)
        conn.commit()
        assert get_embedding_dim(conn) == 768
        conn.close()


class TestFullPaperSchema:
    def test_papers_full_meta_table_created(self, tmp_path):
        conn = connect_arxiv_rag(tmp_path / "arxiv_rag.db")
        assert "papers_full_meta" in _table_names(conn)
        conn.close()

    def test_papers_full_meta_columns(self, tmp_path):
        conn = connect_arxiv_rag(tmp_path / "arxiv_rag.db")
        cols = {row[1] for row in conn.execute("PRAGMA table_info(papers_full_meta)").fetchall()}
        for required in (
            "arxiv_id",
            "oai_datestamp",
            "status",
            "chunk_count",
            "html_path",
            "markdown_path",
            "error_message",
            "embedded_at",
        ):
            assert required in cols, f"missing column: {required}"
        conn.close()

    def test_paper_chunks_table_created(self, tmp_path):
        conn = connect_arxiv_rag(tmp_path / "arxiv_rag.db")
        assert "paper_chunks" in _table_names(conn)
        conn.close()

    def test_paper_chunks_columns(self, tmp_path):
        conn = connect_arxiv_rag(tmp_path / "arxiv_rag.db")
        cols = {row[1] for row in conn.execute("PRAGMA table_info(paper_chunks)").fetchall()}
        for required in ("chunk_id", "arxiv_id", "section", "chunk_index", "text", "text_length"):
            assert required in cols, f"missing column: {required}"
        conn.close()

    def test_paper_chunks_index_present(self, tmp_path):
        conn = connect_arxiv_rag(tmp_path / "arxiv_rag.db")
        idx = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        assert "idx_paper_chunks_arxiv" in idx
        conn.close()

    def test_paper_chunks_fts_exists(self, tmp_path):
        conn = connect_arxiv_rag(tmp_path / "arxiv_rag.db")
        rows = conn.execute("SELECT name FROM sqlite_master WHERE name='paper_chunks_fts'").fetchall()
        assert rows
        conn.close()

    def test_paper_chunks_vec_exists(self, tmp_path):
        conn = connect_arxiv_rag(tmp_path / "arxiv_rag.db")
        rows = conn.execute("SELECT name FROM sqlite_master WHERE name='paper_chunks_vec'").fetchall()
        assert rows
        conn.close()

    def test_insert_full_meta_and_chunks_roundtrip(self, tmp_path):
        conn = connect_arxiv_rag(tmp_path / "arxiv_rag.db")
        conn.execute(
            "INSERT INTO papers_full_meta (arxiv_id, oai_datestamp, status, chunk_count, "
            "html_path, markdown_path, embedded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "2310.06825",
                "2024-01-22",
                "embedded",
                2,
                "dumps/arxiv/papers/2310.06825.html",
                "dumps/arxiv/papers/2310.06825.md",
                "2024-01-23T00:00:00Z",
            ),
        )
        for i, (section, text) in enumerate([("Introduction", "intro body"), ("Methods", "methods body")]):
            conn.execute(
                "INSERT INTO paper_chunks (arxiv_id, section, chunk_index, text, text_length) VALUES (?, ?, ?, ?, ?)",
                ("2310.06825", section, i, text, len(text)),
            )
        conn.commit()
        conn.execute("INSERT INTO paper_chunks_fts(paper_chunks_fts) VALUES('rebuild')")
        conn.commit()

        chunks = conn.execute(
            "SELECT section, text FROM paper_chunks WHERE arxiv_id = ? ORDER BY chunk_index",
            ("2310.06825",),
        ).fetchall()
        assert [(c["section"], c["text"]) for c in chunks] == [
            ("Introduction", "intro body"),
            ("Methods", "methods body"),
        ]
        # FTS5 indexed both rows
        fts_hits = conn.execute("SELECT rowid FROM paper_chunks_fts WHERE paper_chunks_fts MATCH 'body'").fetchall()
        assert len(fts_hits) == 2
        conn.close()

    def test_idempotent_re_create(self, tmp_path):
        """Calling connect twice on the same DB must not raise on the new tables."""
        connect_arxiv_rag(tmp_path / "arxiv_rag.db").close()
        connect_arxiv_rag(tmp_path / "arxiv_rag.db").close()
