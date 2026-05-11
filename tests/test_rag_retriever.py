"""Tests for rag/retriever.py — hybrid retrieval logic (no Ollama needed)."""
import pytest

from rag.retriever import Chunk, RetrievalResult, _fetch_chunks, _rrf_merge, _sparse_search, retrieve
from rag.schema import connect_rag


@pytest.fixture
def rag_db(tmp_path):
    """Small RAG DB with known chunks for testing sparse search and RRF."""
    conn = connect_rag(tmp_path / "rag.db")
    conn.execute(
        "INSERT INTO articles_meta (page_id, title, revision_id) VALUES (1, 'April', 1)"
    )
    conn.execute(
        "INSERT INTO articles_meta (page_id, title, revision_id) VALUES (2, 'March', 2)"
    )
    conn.execute(
        "INSERT INTO chunks (chunk_id, page_id, section, chunk_index, text, text_length) "
        "VALUES (1, 1, NULL, 0, 'April is the fourth month of the year.', 38)"
    )
    conn.execute(
        "INSERT INTO chunks (chunk_id, page_id, section, chunk_index, text, text_length) "
        "VALUES (2, 2, NULL, 0, 'March is the third month of the year.', 37)"
    )
    conn.commit()
    # Rebuild FTS so sparse search works
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    conn.commit()
    return conn


class TestSparseSearch:
    def test_finds_matching_chunk(self, rag_db):
        results = _sparse_search("April fourth month", rag_db, k=10)
        chunk_ids = [r[0] for r in results]
        assert 1 in chunk_ids

    def test_prefers_more_relevant_chunk(self, rag_db):
        results = _sparse_search("April", rag_db, k=10)
        assert results, "Expected at least one result"
        assert results[0][0] == 1  # April chunk should rank first

    def test_empty_query_returns_empty(self, rag_db):
        results = _sparse_search("", rag_db, k=10)
        assert results == []

    def test_limit_respected(self, rag_db):
        results = _sparse_search("month", rag_db, k=1)
        assert len(results) <= 1


class TestRRFMerge:
    def test_both_lists_empty(self):
        assert _rrf_merge([], []) == []

    def test_dense_only(self):
        result = _rrf_merge([(1, 0.1), (2, 0.2)], [])
        ids = [r[0] for r in result]
        assert ids == [1, 2]  # rank 1 gets higher score

    def test_sparse_only(self):
        result = _rrf_merge([], [(3, -1.0), (4, -2.0)])
        ids = [r[0] for r in result]
        assert ids == [3, 4]

    def test_overlapping_results_score_higher(self):
        # chunk 1 appears in both lists at rank 1 — should outscore chunk 2 (sparse only)
        dense = [(1, 0.1), (2, 0.2)]
        sparse = [(1, -1.0), (3, -2.0)]
        result = _rrf_merge(dense, sparse)
        ids = [r[0] for r in result]
        assert ids[0] == 1  # overlap gives highest score

    def test_output_sorted_descending(self):
        dense = [(1, 0.1), (2, 0.2), (3, 0.3)]
        sparse = [(2, -1.0), (3, -2.0), (4, -3.0)]
        result = _rrf_merge(dense, sparse)
        scores = [s for _, s in result]
        assert scores == sorted(scores, reverse=True)


class TestFetchChunks:
    def test_hydrates_title_from_articles_meta(self, rag_db):
        chunks = _fetch_chunks([1], {}, rag_db)
        assert 1 in chunks
        assert chunks[1].title == "April"

    def test_returns_section_and_text(self, rag_db):
        chunks = _fetch_chunks([2], {}, rag_db)
        assert chunks[2].section is None
        assert "March" in chunks[2].text

    def test_missing_chunk_id_not_in_result(self, rag_db):
        chunks = _fetch_chunks([999], {}, rag_db)
        assert 999 not in chunks

    def test_empty_list_returns_empty_dict(self, rag_db):
        assert _fetch_chunks([], {}, rag_db) == {}

    def test_multiple_chunks(self, rag_db):
        chunks = _fetch_chunks([1, 2], {}, rag_db)
        assert set(chunks.keys()) == {1, 2}


class TestRetrieve:
    def test_empty_query_returns_empty(self, rag_db):
        result = retrieve("", rag_db)
        assert isinstance(result, RetrievalResult)
        assert result.hits == []

    def test_whitespace_query_returns_empty(self, rag_db):
        result = retrieve("   ", rag_db)
        assert result.hits == []

    def test_sparse_fallback_when_ollama_down(self, rag_db):
        # Ollama is not running in tests; retrieve falls back to sparse-only
        result = retrieve("April month", rag_db, ollama_url="http://127.0.0.1:1")
        assert isinstance(result, RetrievalResult)
        assert not result.used_dense
        assert all(isinstance(r, Chunk) for r in result.hits)

    def test_result_has_score(self, rag_db):
        result = retrieve("April", rag_db, ollama_url="http://127.0.0.1:1")
        for r in result.hits:
            assert r.score > 0.0
