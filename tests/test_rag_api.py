"""Tests for the /rag/info and /rag/retrieve external API.

Hermetic: ``paths.rag_db_path_for`` is monkeypatched to point at a tmp dir
with one seeded RAG DB (simplewiki). enwiki is in ``paths.KNOWN_WIKIS`` but
has no RAG DB in the fixture, so it exercises the "corpus not found" paths.
"""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import app as web_app
import paths
from rag.embedder import EMBEDDING_DIM, OLLAMA_BASE_URL
from rag.schema import connect_rag


def _seed_rag_db(path):
    """Create a small RAG DB at ``path`` with two articles and two chunks."""
    conn = connect_rag(path)
    try:
        conn.execute("INSERT INTO articles_meta (page_id, title, revision_id) VALUES (1, 'April', 1)")
        conn.execute("INSERT INTO articles_meta (page_id, title, revision_id) VALUES (2, 'March', 2)")
        conn.execute(
            "INSERT INTO chunks (chunk_id, page_id, section, chunk_index, text, text_length) "
            "VALUES (1, 1, 'History', 0, 'April is the fourth month of the year.', 38)"
        )
        conn.execute(
            "INSERT INTO chunks (chunk_id, page_id, section, chunk_index, text, text_length) "
            "VALUES (2, 2, NULL, 0, 'March is the third month of the year.', 37)"
        )
        conn.commit()
        conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def rag_client(tmp_path, wiki_db_path, monkeypatch):
    """TestClient pointed at a hermetic dumps dir containing one RAG DB.

    simplewiki has a seeded RAG DB; enwiki does not. JOBS_DB and WIKI_DB are
    redirected to tmp paths so the lifespan hook doesn't touch real dumps/.
    """
    _seed_rag_db(tmp_path / "simplewiki_rag.db")

    monkeypatch.setattr(paths, "rag_db_path_for", lambda w: tmp_path / f"{w}_rag.db")
    monkeypatch.setattr(paths, "JOBS_DB", tmp_path / "jobs.db")
    monkeypatch.setenv("WIKI_DB", str(wiki_db_path))

    with TestClient(web_app.app) as c:
        yield c


class TestRagInfo:
    def test_returns_server_identity(self, rag_client):
        resp = rag_client.get("/rag/info")
        assert resp.status_code == 200
        data = resp.json()
        assert data["server_name"] == "local-wikipedia"
        assert data["server_version"] == "0.1.0"
        assert data["embedding_model"] == "nomic-embed-text"
        assert data["embedding_dim"] == EMBEDDING_DIM
        assert data["default_top_k"] == 5
        assert data["max_top_k"] == 50
        assert data["article_url_template"] == "/article/{title}"

    def test_lists_only_existing_corpora(self, rag_client):
        data = rag_client.get("/rag/info").json()
        ids = {c["id"] for c in data["corpora"]}
        assert ids == {"simplewiki"}

    def test_corpus_descriptor_fields(self, rag_client):
        data = rag_client.get("/rag/info").json()
        corpus = next(c for c in data["corpora"] if c["id"] == "simplewiki")
        assert corpus["display_name"] == "Simple English Wikipedia"
        assert corpus["article_count"] == 2

    def test_empty_corpora_when_no_rag_db(self, tmp_path, wiki_db_path, monkeypatch):
        # No RAG DB files in tmp_path → every wiki is filtered out.
        monkeypatch.setattr(paths, "rag_db_path_for", lambda w: tmp_path / f"{w}_rag.db")
        monkeypatch.setattr(paths, "JOBS_DB", tmp_path / "jobs.db")
        monkeypatch.setenv("WIKI_DB", str(wiki_db_path))
        with TestClient(web_app.app) as c:
            data = c.get("/rag/info").json()
        assert data["corpora"] == []


class TestRagRetrieve:
    @respx.mock
    def test_happy_path_returns_hits_with_all_fields(self, rag_client):
        # Mock Ollama so dense search runs (uniform vector → all distances equal,
        # but FTS sparse provides ranking and RRF merges both).
        respx.post(f"{OLLAMA_BASE_URL}/api/embeddings").mock(
            return_value=httpx.Response(200, json={"embedding": [0.1] * EMBEDDING_DIM})
        )
        resp = rag_client.post(
            "/rag/retrieve",
            json={"query": "April fourth month", "corpus": "simplewiki", "top_k": 5},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["used_dense"] is True
        assert len(data["hits"]) > 0

        # Top hit should be the April chunk (sparse search ranks it highest).
        top = data["hits"][0]
        assert top["title"] == "April"
        assert top["corpus"] == "simplewiki"
        assert top["section"] == "History"
        assert top["chunk_index"] == 0
        assert top["text_length"] == 38
        assert top["chunk_type"] == "prose"
        assert top["score"] > 0
        # Spec'd hit fields all present.
        for field in (
            "corpus",
            "chunk_id",
            "page_id",
            "title",
            "section",
            "chunk_index",
            "text",
            "text_length",
            "chunk_type",
            "score",
        ):
            assert field in top

    @respx.mock
    def test_default_top_k_used_when_omitted(self, rag_client):
        respx.post(f"{OLLAMA_BASE_URL}/api/embeddings").mock(
            return_value=httpx.Response(200, json={"embedding": [0.1] * EMBEDDING_DIM})
        )
        resp = rag_client.post("/rag/retrieve", json={"query": "month", "corpus": "simplewiki"})
        assert resp.status_code == 200

    @respx.mock
    def test_used_dense_false_when_ollama_errors(self, rag_client):
        respx.post(f"{OLLAMA_BASE_URL}/api/embeddings").mock(return_value=httpx.Response(500))
        resp = rag_client.post(
            "/rag/retrieve",
            json={"query": "April", "corpus": "simplewiki"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["used_dense"] is False
        # Sparse-only still returns the April chunk.
        assert any(h["title"] == "April" for h in body["hits"])

    def test_404_unknown_corpus_not_in_known_wikis(self, rag_client):
        resp = rag_client.post(
            "/rag/retrieve",
            json={"query": "anything", "corpus": "doesnotexist"},
        )
        assert resp.status_code == 404
        assert "doesnotexist" in resp.json()["detail"]

    def test_404_known_wiki_without_rag_db(self, rag_client):
        # enwiki is in KNOWN_WIKIS but the fixture has no enwiki_rag.db.
        resp = rag_client.post(
            "/rag/retrieve",
            json={"query": "anything", "corpus": "enwiki"},
        )
        assert resp.status_code == 404

    def test_422_empty_query(self, rag_client):
        resp = rag_client.post("/rag/retrieve", json={"query": "", "corpus": "simplewiki"})
        assert resp.status_code == 422

    def test_422_whitespace_only_query(self, rag_client):
        resp = rag_client.post("/rag/retrieve", json={"query": "   ", "corpus": "simplewiki"})
        assert resp.status_code == 422

    def test_422_top_k_too_high(self, rag_client):
        resp = rag_client.post(
            "/rag/retrieve",
            json={"query": "x", "corpus": "simplewiki", "top_k": 9999},
        )
        assert resp.status_code == 422

    def test_422_top_k_zero(self, rag_client):
        resp = rag_client.post(
            "/rag/retrieve",
            json={"query": "x", "corpus": "simplewiki", "top_k": 0},
        )
        assert resp.status_code == 422

    def test_422_missing_corpus(self, rag_client):
        resp = rag_client.post("/rag/retrieve", json={"query": "x"})
        assert resp.status_code == 422
