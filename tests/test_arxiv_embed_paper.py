"""Tests for arxiv/embed_paper.py — end-to-end per-paper embed pipeline."""

import httpx
import pytest
import respx

import paths
from arxiv.embed_paper import embed_one_paper
from arxiv.schema import connect_arxiv_rag, connect_papers
from rag.embedder import EMBEDDING_DIM, OLLAMA_BASE_URL

_FAKE_VEC = [0.1] * EMBEDDING_DIM
_HTML_URL = "https://arxiv.org/html/{}"


_SIMPLE_PAPER_HTML = """
<!doctype html>
<html><body>
<article class="ltx_document">
  <div class="ltx_abstract">
    <h6 class="ltx_title ltx_title_abstract">Abstract</h6>
    <p class="ltx_p">Abstract paragraph.</p>
  </div>
  <section class="ltx_section" id="S1">
    <h2 class="ltx_title">Introduction</h2>
    <p class="ltx_p">Intro body text.</p>
  </section>
  <section class="ltx_section" id="S2">
    <h2 class="ltx_title">Methods</h2>
    <p class="ltx_p">Methods body text.</p>
  </section>
</article>
</body></html>
"""


def _seed_paper(papers_conn, arxiv_id="2401.0001", oai_datestamp="2024-01-22", title="Test Paper"):
    papers_conn.execute(
        "INSERT INTO papers (id, oai_datestamp, title, abstract, authors, "
        "categories, primary_category, submitted_date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (arxiv_id, oai_datestamp, title, "abstract body", "[]", "cs.CL", "cs.CL", "2024-01-22"),
    )
    papers_conn.commit()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Redirect all arxiv paths into tmp_path and zero out retry/polite sleeps."""
    monkeypatch.setattr(paths, "DUMPS_DIR", tmp_path)
    monkeypatch.setattr(paths, "ARXIV_DB", tmp_path / "arxiv.db")
    monkeypatch.setattr(paths, "ARXIV_RAG_DB", tmp_path / "arxiv_rag.db")
    monkeypatch.setattr(paths, "ARXIV_PAPERS_DIR", tmp_path / "arxiv" / "papers")
    import arxiv.download
    import rag.embedder

    monkeypatch.setattr(rag.embedder, "_BACKOFF_BASE", 0)
    monkeypatch.setattr(arxiv.download, "MIN_REQUEST_INTERVAL", 0)
    monkeypatch.setattr(arxiv.download, "BACKOFF_BASE", 0)
    return tmp_path


class TestEmbedOnePaper:
    @respx.mock
    def test_embeds_paper_end_to_end(self, isolated):
        papers = connect_papers(isolated / "arxiv.db")
        _seed_paper(papers)
        rag = connect_arxiv_rag(isolated / "arxiv_rag.db")
        respx.get(_HTML_URL.format("2401.0001")).mock(return_value=httpx.Response(200, text=_SIMPLE_PAPER_HTML))
        respx.post(f"{OLLAMA_BASE_URL}/api/embed").mock(
            return_value=httpx.Response(200, json={"embeddings": [_FAKE_VEC, _FAKE_VEC, _FAKE_VEC]})
        )

        result = embed_one_paper("2401.0001", papers_conn=papers, rag_conn=rag)

        assert result.status == "embedded"
        assert result.chunk_count == 3
        # Chunks in tables
        chunks = rag.execute("SELECT section, text FROM paper_chunks ORDER BY chunk_id").fetchall()
        assert {c["section"] for c in chunks} == {"Abstract", "Introduction", "Methods"}
        # Vectors written
        assert rag.execute("SELECT COUNT(*) FROM paper_chunks_vec").fetchone()[0] == 3
        # papers_full_meta updated
        meta = rag.execute("SELECT * FROM papers_full_meta WHERE arxiv_id = ?", ("2401.0001",)).fetchone()
        assert meta["status"] == "embedded"
        assert meta["chunk_count"] == 3
        assert meta["html_path"].endswith("2401.0001.html")
        assert meta["markdown_path"].endswith("2401.0001.md")
        # Markdown written to disk
        md = (isolated / "arxiv" / "papers" / "2401.0001.md").read_text(encoding="utf-8")
        assert "## Introduction" in md
        papers.close()
        rag.close()

    @respx.mock
    def test_no_html_marked_correctly(self, isolated):
        papers = connect_papers(isolated / "arxiv.db")
        _seed_paper(papers)
        rag = connect_arxiv_rag(isolated / "arxiv_rag.db")
        respx.get(_HTML_URL.format("2401.0001")).mock(return_value=httpx.Response(404))

        result = embed_one_paper("2401.0001", papers_conn=papers, rag_conn=rag)

        assert result.status == "no_html"
        assert result.chunk_count == 0
        # No chunks written
        assert rag.execute("SELECT COUNT(*) FROM paper_chunks").fetchone()[0] == 0
        # papers_full_meta has no_html row
        meta = rag.execute("SELECT status, html_path FROM papers_full_meta").fetchone()
        assert meta["status"] == "no_html"
        assert meta["html_path"] is None
        papers.close()
        rag.close()

    @respx.mock
    def test_skipped_when_oai_datestamp_unchanged(self, isolated):
        papers = connect_papers(isolated / "arxiv.db")
        _seed_paper(papers)
        rag = connect_arxiv_rag(isolated / "arxiv_rag.db")
        respx.get(_HTML_URL.format("2401.0001")).mock(return_value=httpx.Response(200, text=_SIMPLE_PAPER_HTML))
        respx.post(f"{OLLAMA_BASE_URL}/api/embed").mock(
            return_value=httpx.Response(200, json={"embeddings": [_FAKE_VEC] * 3})
        )

        # First call: actually embeds.
        first = embed_one_paper("2401.0001", papers_conn=papers, rag_conn=rag)
        assert first.status == "embedded"
        # Second call: same datestamp → skip.
        second = embed_one_paper("2401.0001", papers_conn=papers, rag_conn=rag)
        assert second.status == "skipped"
        assert second.chunk_count == first.chunk_count
        papers.close()
        rag.close()

    @respx.mock
    def test_force_reembeds_even_when_unchanged(self, isolated):
        papers = connect_papers(isolated / "arxiv.db")
        _seed_paper(papers)
        rag = connect_arxiv_rag(isolated / "arxiv_rag.db")
        respx.get(_HTML_URL.format("2401.0001")).mock(return_value=httpx.Response(200, text=_SIMPLE_PAPER_HTML))
        respx.post(f"{OLLAMA_BASE_URL}/api/embed").mock(
            return_value=httpx.Response(200, json={"embeddings": [_FAKE_VEC] * 3})
        )

        embed_one_paper("2401.0001", papers_conn=papers, rag_conn=rag)
        result = embed_one_paper("2401.0001", papers_conn=papers, rag_conn=rag, force=True)
        assert result.status == "embedded"
        # Still exactly 3 chunks (old ones replaced)
        assert rag.execute("SELECT COUNT(*) FROM paper_chunks").fetchone()[0] == 3
        papers.close()
        rag.close()

    @respx.mock
    def test_re_embed_on_changed_oai_datestamp(self, isolated):
        papers = connect_papers(isolated / "arxiv.db")
        _seed_paper(papers, oai_datestamp="2024-01-22")
        rag = connect_arxiv_rag(isolated / "arxiv_rag.db")
        respx.get(_HTML_URL.format("2401.0001")).mock(return_value=httpx.Response(200, text=_SIMPLE_PAPER_HTML))
        respx.post(f"{OLLAMA_BASE_URL}/api/embed").mock(
            return_value=httpx.Response(200, json={"embeddings": [_FAKE_VEC] * 3})
        )

        embed_one_paper("2401.0001", papers_conn=papers, rag_conn=rag)
        # Simulate paper being re-ingested with a new datestamp.
        papers.execute("UPDATE papers SET oai_datestamp = ? WHERE id = ?", ("2024-02-01", "2401.0001"))
        papers.commit()

        result = embed_one_paper("2401.0001", papers_conn=papers, rag_conn=rag)
        assert result.status == "embedded"
        # Stored datestamp now matches the new one.
        meta = rag.execute("SELECT oai_datestamp FROM papers_full_meta").fetchone()
        assert meta["oai_datestamp"] == "2024-02-01"
        papers.close()
        rag.close()

    def test_raises_when_paper_missing(self, isolated):
        papers = connect_papers(isolated / "arxiv.db")
        rag = connect_arxiv_rag(isolated / "arxiv_rag.db")
        with pytest.raises(KeyError):
            embed_one_paper("does-not-exist", papers_conn=papers, rag_conn=rag)
        papers.close()
        rag.close()

    @respx.mock
    def test_embed_text_includes_title_and_section(self, isolated):
        papers = connect_papers(isolated / "arxiv.db")
        _seed_paper(papers, title="My Title")
        rag = connect_arxiv_rag(isolated / "arxiv_rag.db")
        respx.get(_HTML_URL.format("2401.0001")).mock(return_value=httpx.Response(200, text=_SIMPLE_PAPER_HTML))
        route = respx.post(f"{OLLAMA_BASE_URL}/api/embed").mock(
            return_value=httpx.Response(200, json={"embeddings": [_FAKE_VEC] * 3})
        )

        embed_one_paper("2401.0001", papers_conn=papers, rag_conn=rag)

        import json

        payload = json.loads(route.calls[0].request.read())
        for text in payload["input"]:
            assert text.startswith("search_document: ")
            assert "My Title" in text
        papers.close()
        rag.close()

    @respx.mock
    def test_fts_rebuilt_after_embed(self, isolated):
        papers = connect_papers(isolated / "arxiv.db")
        _seed_paper(papers)
        rag = connect_arxiv_rag(isolated / "arxiv_rag.db")
        respx.get(_HTML_URL.format("2401.0001")).mock(return_value=httpx.Response(200, text=_SIMPLE_PAPER_HTML))
        respx.post(f"{OLLAMA_BASE_URL}/api/embed").mock(
            return_value=httpx.Response(200, json={"embeddings": [_FAKE_VEC] * 3})
        )

        embed_one_paper("2401.0001", papers_conn=papers, rag_conn=rag)

        # FTS query should find chunks.
        hits = rag.execute("SELECT rowid FROM paper_chunks_fts WHERE paper_chunks_fts MATCH 'methods'").fetchall()
        assert hits
        papers.close()
        rag.close()
