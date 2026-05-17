"""Tests for arxiv/retriever.py — sparse search, RRF, hydration (no Ollama needed)."""

import json

import pytest

from arxiv.retriever import (
    Paper,
    RetrievalResult,
    _hydrate,
    _rrf_merge,
    _sparse_search,
    retrieve,
)
from arxiv.schema import connect_arxiv_rag, connect_papers


def _seed(papers_conn, rag_conn, rows):
    """Insert a batch of (id, title, abstract, categories) into both DBs.

    The arxiv_rag side gets papers_meta + papers_fts populated; papers_vec is
    left empty (sparse-only tests don't need vectors).
    """
    for row in rows:
        papers_conn.execute(
            "INSERT INTO papers (id, oai_datestamp, title, abstract, authors, "
            "categories, primary_category, submitted_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["id"],
                "2024-01-22",
                row["title"],
                row["abstract"],
                json.dumps(row.get("authors", ["A. N. Author"])),
                row["categories"],
                row["categories"].split()[0],
                "2024-01-22",
            ),
        )
        embed_text = f"{row['title']}\n\n{row['abstract']}\n\nCategories: {row['categories']}"
        rag_conn.execute(
            "INSERT INTO papers_meta (arxiv_id, oai_datestamp, embed_text, embedded_at) VALUES (?, ?, ?, ?)",
            (row["id"], "2024-01-22", embed_text, "2024-01-23T00:00:00Z"),
        )
    papers_conn.commit()
    rag_conn.commit()
    rag_conn.execute("INSERT INTO papers_fts(papers_fts) VALUES('rebuild')")
    rag_conn.commit()


@pytest.fixture
def db_pair(tmp_path, monkeypatch):
    """Two-DB fixture mirroring arxiv.db + arxiv_rag.db with three sample papers.

    Also zeros out the embedder retry backoff so the Ollama-down fallback tests
    don't waste 6 s waiting between retries.
    """
    import rag.embedder

    monkeypatch.setattr(rag.embedder, "_BACKOFF_BASE", 0)
    papers = connect_papers(tmp_path / "arxiv.db")
    rag = connect_arxiv_rag(tmp_path / "arxiv_rag.db")
    _seed(
        papers,
        rag,
        [
            {
                "id": "2401.0001",
                "title": "Attention is all you need",
                "abstract": "Transformers replace recurrence with self-attention.",
                "categories": "cs.CL cs.LG",
            },
            {
                "id": "2401.0002",
                "title": "BERT pretraining",
                "abstract": "Masked language modelling for transformer encoders.",
                "categories": "cs.CL",
            },
            {
                "id": "2401.0003",
                "title": "ResNet image classification",
                "abstract": "Deep residual learning for computer vision.",
                "categories": "cs.CV",
            },
        ],
    )
    yield papers, rag
    papers.close()
    rag.close()


class TestSparseSearch:
    def test_finds_matching_paper(self, db_pair):
        _, rag = db_pair
        results = _sparse_search("transformer attention", rag, k=10)
        rowids = [r[0] for r in results]
        # Either of the transformer-related papers should rank.
        assert rowids

    def test_empty_query_returns_empty(self, db_pair):
        _, rag = db_pair
        assert _sparse_search("", rag, k=10) == []

    def test_limit_respected(self, db_pair):
        _, rag = db_pair
        results = _sparse_search("transformer", rag, k=1)
        assert len(results) <= 1

    def test_porter_stemming_matches_inflections(self, db_pair):
        _, rag = db_pair
        # "transformers" in the abstract should match a "transformer" query under porter.
        results = _sparse_search("transformer", rag, k=10)
        assert results


class TestRRFMerge:
    def test_both_empty(self):
        assert _rrf_merge([], []) == []

    def test_dense_only_preserves_order(self):
        out = _rrf_merge([(1, 0.1), (2, 0.2)], [])
        assert [r for r, _ in out] == [1, 2]

    def test_overlap_outranks_singleton(self):
        dense = [(1, 0.1), (2, 0.2)]
        sparse = [(1, -1.0), (3, -2.0)]
        out = _rrf_merge(dense, sparse)
        assert out[0][0] == 1

    def test_output_sorted_desc(self):
        out = _rrf_merge([(1, 0.1), (2, 0.2), (3, 0.3)], [(2, -1.0), (4, -2.0)])
        scores = [s for _, s in out]
        assert scores == sorted(scores, reverse=True)


class TestHydrate:
    def test_returns_papers_in_input_order(self, db_pair):
        papers, rag = db_pair
        rowids = [r[0] for r in rag.execute("SELECT rowid FROM papers_meta ORDER BY rowid").fetchall()]
        hits = _hydrate(rowids, {r: 1.0 for r in rowids}, rag, papers)
        assert [h.arxiv_id for h in hits] == ["2401.0001", "2401.0002", "2401.0003"]

    def test_parses_authors_list(self, db_pair):
        papers, rag = db_pair
        rowids = [rag.execute("SELECT rowid FROM papers_meta LIMIT 1").fetchone()[0]]
        hits = _hydrate(rowids, {rowids[0]: 1.0}, rag, papers)
        assert isinstance(hits[0].authors, list)

    def test_carries_score(self, db_pair):
        papers, rag = db_pair
        rowids = [rag.execute("SELECT rowid FROM papers_meta LIMIT 1").fetchone()[0]]
        hits = _hydrate(rowids, {rowids[0]: 0.42}, rag, papers)
        assert hits[0].score == 0.42

    def test_missing_rowid_skipped(self, db_pair):
        papers, rag = db_pair
        hits = _hydrate([99999], {99999: 1.0}, rag, papers)
        assert hits == []

    def test_empty_input_returns_empty(self, db_pair):
        papers, rag = db_pair
        assert _hydrate([], {}, rag, papers) == []


class TestRetrieve:
    def test_empty_query_returns_empty(self, db_pair):
        papers, rag = db_pair
        result = retrieve("", rag, papers)
        assert isinstance(result, RetrievalResult)
        assert result.hits == []

    def test_whitespace_query_returns_empty(self, db_pair):
        papers, rag = db_pair
        assert retrieve("   ", rag, papers).hits == []

    def test_sparse_fallback_when_ollama_down(self, db_pair):
        papers, rag = db_pair
        result = retrieve(
            "transformer attention",
            rag,
            papers,
            ollama_url="http://127.0.0.1:1",
        )
        assert not result.used_dense
        assert all(isinstance(p, Paper) for p in result.hits)
        # We seeded transformer-related papers — at least one should hit.
        assert result.hits

    def test_paper_fields_populated(self, db_pair):
        papers, rag = db_pair
        result = retrieve("residual vision", rag, papers, ollama_url="http://127.0.0.1:1")
        assert result.hits
        hit = result.hits[0]
        assert hit.arxiv_id
        assert hit.title
        assert hit.categories
        assert hit.score > 0.0
