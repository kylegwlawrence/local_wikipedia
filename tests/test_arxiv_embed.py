"""Tests for arxiv/embed.py and arxiv/templates_meta.py.

Ollama is mocked via respx. Tests run against fixture arxiv.db +
arxiv_rag.db in tmp_path.
"""

import httpx
import pytest
import respx

import paths
from arxiv import embed as embed_mod
from arxiv.embed import delete_paper, embed_papers, load_embedded, main, reset_rag
from arxiv.schema import connect_arxiv_rag, connect_papers
from arxiv.templates_meta import format_embed_text
from rag.embedder import EMBED_DOC_PREFIX, EMBEDDING_DIM, OLLAMA_BASE_URL

_FAKE_VEC = [0.1] * EMBEDDING_DIM


def _seed_papers(conn, rows):
    for r in rows:
        conn.execute(
            "INSERT INTO papers (id, oai_datestamp, title, abstract, authors, "
            "categories, primary_category, submitted_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                r["id"],
                r["oai_datestamp"],
                r["title"],
                r["abstract"],
                "[]",
                r["categories"],
                r["categories"].split()[0],
                "2024-01-22",
            ),
        )
    conn.commit()


def _paper(arxiv_id="2401.0001", oai_datestamp="2024-01-22", title="T", categories="cs.CL"):
    return {
        "id": arxiv_id,
        "oai_datestamp": oai_datestamp,
        "title": title,
        "abstract": "Abstract body.",
        "categories": categories,
    }


class TestFormatEmbedText:
    def test_includes_title_abstract_categories(self):
        out = format_embed_text(_paper(title="Paper", categories="cs.CL cs.LG"))
        assert "Paper" in out
        assert "Abstract body." in out
        assert "Categories: cs.CL cs.LG" in out

    def test_no_search_document_prefix(self):
        """The plain version stored in papers_meta must NOT carry the embed prefix."""
        out = format_embed_text(_paper())
        assert not out.startswith("search_document:")

    def test_deterministic_shape(self):
        out = format_embed_text(_paper(title="X", categories="math.AP"))
        assert out == "X\n\nAbstract body.\n\nCategories: math.AP"


@pytest.fixture
def rag_conn(tmp_path):
    c = connect_arxiv_rag(tmp_path / "arxiv_rag.db")
    yield c
    c.close()


class TestLoadEmbedded:
    def test_empty_when_no_rows(self, rag_conn):
        assert load_embedded(rag_conn) == {}

    def test_returns_mapping(self, rag_conn):
        rag_conn.execute(
            "INSERT INTO papers_meta (arxiv_id, oai_datestamp, embed_text, embedded_at) VALUES (?, ?, ?, ?)",
            ("2401.0001", "2024-01-22", "txt", "2024-01-23T00:00:00Z"),
        )
        rag_conn.commit()
        assert load_embedded(rag_conn) == {"2401.0001": "2024-01-22"}


class TestDeletePaper:
    def test_removes_meta_and_vec(self, rag_conn):
        # Seed one paper.
        cur = rag_conn.execute(
            "INSERT INTO papers_meta (arxiv_id, oai_datestamp, embed_text, embedded_at) VALUES (?, ?, ?, ?)",
            ("2401.0001", "2024-01-22", "title body cats", "2024-01-23T00:00:00Z"),
        )
        rowid = cur.lastrowid
        import struct

        rag_conn.execute(
            "INSERT INTO papers_vec (rowid, embedding) VALUES (?, ?)",
            (rowid, struct.pack(f"{EMBEDDING_DIM}f", *_FAKE_VEC)),
        )
        rag_conn.commit()

        delete_paper(rag_conn, "2401.0001")
        rag_conn.commit()

        assert rag_conn.execute("SELECT COUNT(*) FROM papers_meta").fetchone()[0] == 0
        assert rag_conn.execute("SELECT COUNT(*) FROM papers_vec").fetchone()[0] == 0

    def test_missing_id_is_noop(self, rag_conn):
        delete_paper(rag_conn, "does-not-exist")  # must not raise


class TestEmbedPapers:
    @respx.mock
    def test_inserts_meta_and_vec(self, rag_conn):
        respx.post(f"{OLLAMA_BASE_URL}/api/embed").mock(
            return_value=httpx.Response(200, json={"embeddings": [_FAKE_VEC, _FAKE_VEC]})
        )
        papers = [_paper(arxiv_id="2401.0001"), _paper(arxiv_id="2401.0002")]
        n = embed_papers(rag_conn, papers, embedded_at="2024-01-23T00:00:00Z")
        rag_conn.commit()
        assert n == 2
        assert rag_conn.execute("SELECT COUNT(*) FROM papers_meta").fetchone()[0] == 2
        assert rag_conn.execute("SELECT COUNT(*) FROM papers_vec").fetchone()[0] == 2

    @respx.mock
    def test_sends_prefixed_text_to_ollama(self, rag_conn):
        route = respx.post(f"{OLLAMA_BASE_URL}/api/embed").mock(
            return_value=httpx.Response(200, json={"embeddings": [_FAKE_VEC]})
        )
        embed_papers(rag_conn, [_paper()], embedded_at="2024-01-23T00:00:00Z")
        import json

        payload = json.loads(route.calls[0].request.read())
        assert all(text.startswith(EMBED_DOC_PREFIX) for text in payload["input"])

    @respx.mock
    def test_stored_embed_text_has_no_prefix(self, rag_conn):
        respx.post(f"{OLLAMA_BASE_URL}/api/embed").mock(
            return_value=httpx.Response(200, json={"embeddings": [_FAKE_VEC]})
        )
        embed_papers(rag_conn, [_paper()], embedded_at="2024-01-23T00:00:00Z")
        rag_conn.commit()
        row = rag_conn.execute("SELECT embed_text FROM papers_meta").fetchone()
        assert not row["embed_text"].startswith("search_document:")

    @respx.mock
    def test_records_embedding_dim_on_first_write(self, rag_conn):
        from arxiv.schema import get_embedding_dim

        respx.post(f"{OLLAMA_BASE_URL}/api/embed").mock(
            return_value=httpx.Response(200, json={"embeddings": [_FAKE_VEC]})
        )
        embed_papers(rag_conn, [_paper()], embedded_at="2024-01-23T00:00:00Z")
        rag_conn.commit()
        assert get_embedding_dim(rag_conn) == EMBEDDING_DIM

    def test_empty_batch_returns_zero(self, rag_conn):
        # No HTTP mock — embed_papers must not call out for an empty batch.
        assert embed_papers(rag_conn, [], embedded_at="now") == 0


class TestResetRag:
    def test_clears_all_tables(self, rag_conn):
        rag_conn.execute(
            "INSERT INTO papers_meta (arxiv_id, oai_datestamp, embed_text, embedded_at) VALUES ('x', 'd', 't', 'a')"
        )
        rag_conn.commit()
        reset_rag(rag_conn)
        assert rag_conn.execute("SELECT COUNT(*) FROM papers_meta").fetchone()[0] == 0


@pytest.fixture
def isolated_arxiv(tmp_path, monkeypatch):
    """Redirect arxiv DBs to tmp_path; zero out the embedder retry backoff."""
    import rag.embedder

    monkeypatch.setattr(paths, "DUMPS_DIR", tmp_path)
    monkeypatch.setattr(paths, "ARXIV_DB", tmp_path / "arxiv.db")
    monkeypatch.setattr(paths, "ARXIV_RAG_DB", tmp_path / "arxiv_rag.db")
    monkeypatch.setattr(paths, "ARXIV_OAI_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(rag.embedder, "_BACKOFF_BASE", 0)
    return tmp_path


class TestMain:
    def test_errors_when_arxiv_db_missing(self, isolated_arxiv, capsys):
        rc = main([])
        assert rc == 1
        assert "Run `python -m arxiv.ingest` first" in capsys.readouterr().err

    @respx.mock
    def test_end_to_end_embeds_new_papers(self, isolated_arxiv):
        # Seed arxiv.db with 2 papers.
        papers_conn = connect_papers(isolated_arxiv / "arxiv.db")
        _seed_papers(
            papers_conn,
            [
                {
                    "id": "2401.0001",
                    "oai_datestamp": "2024-01-22",
                    "title": "A",
                    "abstract": "x",
                    "categories": "cs.CL",
                },
                {
                    "id": "2401.0002",
                    "oai_datestamp": "2024-01-22",
                    "title": "B",
                    "abstract": "y",
                    "categories": "cs.LG",
                },
            ],
        )
        papers_conn.close()

        respx.post(f"{OLLAMA_BASE_URL}/api/embed").mock(
            return_value=httpx.Response(200, json={"embeddings": [_FAKE_VEC, _FAKE_VEC]})
        )

        rc = main([])
        assert rc == 0

        rag = connect_arxiv_rag(isolated_arxiv / "arxiv_rag.db")
        try:
            assert rag.execute("SELECT COUNT(*) FROM papers_meta").fetchone()[0] == 2
            assert rag.execute("SELECT COUNT(*) FROM papers_vec").fetchone()[0] == 2
            # FTS rebuilt at end — search should hit something.
            rows = rag.execute("SELECT rowid FROM papers_fts WHERE papers_fts MATCH 'cs'").fetchall()
            assert rows
        finally:
            rag.close()

    @respx.mock
    def test_skips_unchanged_and_updates_changed(self, isolated_arxiv):
        papers_conn = connect_papers(isolated_arxiv / "arxiv.db")
        _seed_papers(
            papers_conn,
            [
                {"id": "1", "oai_datestamp": "2024-01-22", "title": "Old", "abstract": "x", "categories": "cs.CL"},
                {"id": "2", "oai_datestamp": "2024-01-22", "title": "Same", "abstract": "y", "categories": "cs.LG"},
            ],
        )
        papers_conn.close()

        # Pre-populate papers_meta: paper 1 with OLD datestamp (will be updated),
        # paper 2 with the SAME datestamp (will be skipped).
        rag = connect_arxiv_rag(isolated_arxiv / "arxiv_rag.db")
        try:
            rag.execute(
                "INSERT INTO papers_meta (arxiv_id, oai_datestamp, embed_text, embedded_at) VALUES (?, ?, ?, ?)",
                ("1", "2024-01-01", "stale", "old"),
            )
            import struct

            rag.execute(
                "INSERT INTO papers_vec (rowid, embedding) VALUES "
                "((SELECT rowid FROM papers_meta WHERE arxiv_id='1'), ?)",
                (struct.pack(f"{EMBEDDING_DIM}f", *_FAKE_VEC),),
            )
            rag.execute(
                "INSERT INTO papers_meta (arxiv_id, oai_datestamp, embed_text, embedded_at) VALUES (?, ?, ?, ?)",
                ("2", "2024-01-22", "fresh", "old"),
            )
            rag.commit()
        finally:
            rag.close()

        respx.post(f"{OLLAMA_BASE_URL}/api/embed").mock(
            return_value=httpx.Response(200, json={"embeddings": [_FAKE_VEC]})
        )

        rc = main([])
        assert rc == 0

        rag = connect_arxiv_rag(isolated_arxiv / "arxiv_rag.db")
        try:
            new_meta = {
                r["arxiv_id"]: r["oai_datestamp"]
                for r in rag.execute("SELECT arxiv_id, oai_datestamp FROM papers_meta").fetchall()
            }
            assert new_meta == {"1": "2024-01-22", "2": "2024-01-22"}
        finally:
            rag.close()

    @respx.mock
    def test_reset_flag_wipes_first(self, isolated_arxiv):
        papers_conn = connect_papers(isolated_arxiv / "arxiv.db")
        _seed_papers(
            papers_conn,
            [{"id": "new", "oai_datestamp": "2024-01-22", "title": "T", "abstract": "x", "categories": "cs.CL"}],
        )
        papers_conn.close()

        rag = connect_arxiv_rag(isolated_arxiv / "arxiv_rag.db")
        try:
            rag.execute(
                "INSERT INTO papers_meta (arxiv_id, oai_datestamp, embed_text, embedded_at) "
                "VALUES ('stale', 'd', 't', 'a')"
            )
            rag.commit()
        finally:
            rag.close()

        respx.post(f"{OLLAMA_BASE_URL}/api/embed").mock(
            return_value=httpx.Response(200, json={"embeddings": [_FAKE_VEC]})
        )

        rc = main(["--reset"])
        assert rc == 0

        rag = connect_arxiv_rag(isolated_arxiv / "arxiv_rag.db")
        try:
            ids = {r[0] for r in rag.execute("SELECT arxiv_id FROM papers_meta").fetchall()}
            assert ids == {"new"}
        finally:
            rag.close()

    @respx.mock
    def test_batch_failure_marks_failed_and_continues(self, isolated_arxiv):
        papers_conn = connect_papers(isolated_arxiv / "arxiv.db")
        _seed_papers(
            papers_conn,
            [
                {"id": str(i), "oai_datestamp": "2024-01-22", "title": "T", "abstract": "x", "categories": "cs.CL"}
                for i in range(3)
            ],
        )
        papers_conn.close()

        respx.post(f"{OLLAMA_BASE_URL}/api/embed").mock(return_value=httpx.Response(503))

        # batch=1 so every paper is its own batch and they all fail.
        rc = main(["--batch", "1"])
        assert rc == 0  # main itself doesn't error — failures are accounted for in stats

        rag = connect_arxiv_rag(isolated_arxiv / "arxiv_rag.db")
        try:
            # Nothing should have been written.
            assert rag.execute("SELECT COUNT(*) FROM papers_meta").fetchone()[0] == 0
        finally:
            rag.close()

    @respx.mock
    def test_limit_caps_papers_processed(self, isolated_arxiv):
        papers_conn = connect_papers(isolated_arxiv / "arxiv.db")
        _seed_papers(
            papers_conn,
            [
                {"id": str(i), "oai_datestamp": "2024-01-22", "title": "T", "abstract": "x", "categories": "cs.CL"}
                for i in range(5)
            ],
        )
        papers_conn.close()

        respx.post(f"{OLLAMA_BASE_URL}/api/embed").mock(
            return_value=httpx.Response(200, json={"embeddings": [_FAKE_VEC] * 2})
        )

        rc = main(["--limit", "2"])
        assert rc == 0

        rag = connect_arxiv_rag(isolated_arxiv / "arxiv_rag.db")
        try:
            assert rag.execute("SELECT COUNT(*) FROM papers_meta").fetchone()[0] == 2
        finally:
            rag.close()

    def test_imports_module(self):
        # Sanity check the module imports cleanly without side effects.
        assert hasattr(embed_mod, "main")
