"""Tests for workers/arxiv_embed.py — drains a job queue end-to-end."""

import httpx
import pytest
import respx

import paths
from arxiv import jobs as arxiv_jobs
from arxiv.schema import connect_arxiv_rag, connect_papers
from rag.embedder import EMBEDDING_DIM, OLLAMA_BASE_URL
from workers import arxiv_embed as arxiv_embed_worker

_FAKE_VEC = [0.1] * EMBEDDING_DIM
_HTML_URL = "https://arxiv.org/html/{}"


_SIMPLE_PAPER_HTML = """
<!doctype html>
<html><body>
<article class="ltx_document">
  <section class="ltx_section">
    <h2 class="ltx_title">Intro</h2>
    <p class="ltx_p">Body.</p>
  </section>
</article>
</body></html>
"""


def _seed_paper(papers_conn, arxiv_id):
    papers_conn.execute(
        "INSERT INTO papers (id, oai_datestamp, title, abstract, authors, "
        "categories, primary_category, submitted_date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (arxiv_id, "2024-01-22", f"Paper {arxiv_id}", "abs", "[]", "cs.CL", "cs.CL", "2024-01-22"),
    )
    papers_conn.commit()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Redirect every dump path into tmp_path and zero out polite sleeps."""
    monkeypatch.setattr(paths, "DUMPS_DIR", tmp_path)
    monkeypatch.setattr(paths, "JOBS_DB", tmp_path / "jobs.db")
    monkeypatch.setattr(paths, "ARXIV_DB", tmp_path / "arxiv.db")
    monkeypatch.setattr(paths, "ARXIV_RAG_DB", tmp_path / "arxiv_rag.db")
    monkeypatch.setattr(paths, "ARXIV_PAPERS_DIR", tmp_path / "arxiv" / "papers")
    import arxiv.download
    import rag.embedder

    monkeypatch.setattr(rag.embedder, "_BACKOFF_BASE", 0)
    monkeypatch.setattr(arxiv.download, "MIN_REQUEST_INTERVAL", 0)
    monkeypatch.setattr(arxiv.download, "BACKOFF_BASE", 0)
    return tmp_path


class TestWorker:
    @respx.mock
    def test_drains_queued_items(self, isolated):
        papers = connect_papers(paths.ARXIV_DB)
        _seed_paper(papers, "2401.0001")
        _seed_paper(papers, "2401.0002")
        papers.close()

        jobs_conn = arxiv_jobs.connect_arxiv_jobs(paths.JOBS_DB)
        job_id = arxiv_jobs.create_job(jobs_conn, str(paths.ARXIV_EMBED_LOG))
        arxiv_jobs.append_items(jobs_conn, job_id, ["2401.0001", "2401.0002"])
        jobs_conn.close()

        respx.get(_HTML_URL.format("2401.0001")).mock(return_value=httpx.Response(200, text=_SIMPLE_PAPER_HTML))
        respx.get(_HTML_URL.format("2401.0002")).mock(return_value=httpx.Response(200, text=_SIMPLE_PAPER_HTML))
        respx.post(f"{OLLAMA_BASE_URL}/api/embed").mock(
            return_value=httpx.Response(200, json={"embeddings": [_FAKE_VEC]})
        )

        rc = arxiv_embed_worker.main(["--job-id", str(job_id)])

        assert rc == 0
        jobs_conn = arxiv_jobs.connect_arxiv_jobs(paths.JOBS_DB)
        job = arxiv_jobs.get_job(jobs_conn, job_id)
        assert job["status"] == "complete"
        statuses = {i["arxiv_id"]: i["status"] for i in arxiv_jobs.get_items(jobs_conn, job_id)}
        assert statuses == {"2401.0001": "embedded", "2401.0002": "embedded"}
        jobs_conn.close()

        rag = connect_arxiv_rag(paths.ARXIV_RAG_DB)
        assert rag.execute("SELECT COUNT(*) FROM paper_chunks").fetchone()[0] >= 2
        rag.close()

    @respx.mock
    def test_no_html_marked_per_item(self, isolated):
        papers = connect_papers(paths.ARXIV_DB)
        _seed_paper(papers, "2401.0001")
        papers.close()

        jobs_conn = arxiv_jobs.connect_arxiv_jobs(paths.JOBS_DB)
        job_id = arxiv_jobs.create_job(jobs_conn, str(paths.ARXIV_EMBED_LOG))
        arxiv_jobs.append_items(jobs_conn, job_id, ["2401.0001"])
        jobs_conn.close()

        respx.get(_HTML_URL.format("2401.0001")).mock(return_value=httpx.Response(404))

        rc = arxiv_embed_worker.main(["--job-id", str(job_id)])
        assert rc == 0

        jobs_conn = arxiv_jobs.connect_arxiv_jobs(paths.JOBS_DB)
        items = arxiv_jobs.get_items(jobs_conn, job_id)
        assert items[0]["status"] == "no_html"
        jobs_conn.close()

    def test_unknown_arxiv_id_marked_not_found(self, isolated):
        # papers table is empty — embed_one_paper raises KeyError.
        jobs_conn = arxiv_jobs.connect_arxiv_jobs(paths.JOBS_DB)
        # Make sure papers.db exists (empty)
        connect_papers(paths.ARXIV_DB).close()
        connect_arxiv_rag(paths.ARXIV_RAG_DB).close()
        job_id = arxiv_jobs.create_job(jobs_conn, str(paths.ARXIV_EMBED_LOG))
        arxiv_jobs.append_items(jobs_conn, job_id, ["does-not-exist"])
        jobs_conn.close()

        rc = arxiv_embed_worker.main(["--job-id", str(job_id)])
        assert rc == 0

        jobs_conn = arxiv_jobs.connect_arxiv_jobs(paths.JOBS_DB)
        items = arxiv_jobs.get_items(jobs_conn, job_id)
        assert items[0]["status"] == "not_found"
        jobs_conn.close()

    @respx.mock
    def test_cancel_stops_drain(self, isolated):
        papers = connect_papers(paths.ARXIV_DB)
        _seed_paper(papers, "2401.0001")
        papers.close()

        jobs_conn = arxiv_jobs.connect_arxiv_jobs(paths.JOBS_DB)
        job_id = arxiv_jobs.create_job(jobs_conn, str(paths.ARXIV_EMBED_LOG))
        arxiv_jobs.append_items(jobs_conn, job_id, ["2401.0001"])
        # Pre-cancel before worker even starts.
        arxiv_jobs.request_cancel(jobs_conn, job_id)
        jobs_conn.close()

        rc = arxiv_embed_worker.main(["--job-id", str(job_id)])
        assert rc == 0

        jobs_conn = arxiv_jobs.connect_arxiv_jobs(paths.JOBS_DB)
        job = arxiv_jobs.get_job(jobs_conn, job_id)
        assert job["status"] == "cancelled"
        # Item still queued — never picked up.
        items = arxiv_jobs.get_items(jobs_conn, job_id)
        assert items[0]["status"] == "queued"
        jobs_conn.close()

    def test_missing_job_returns_1(self, isolated):
        # Set up empty arxiv DBs so the body can open them.
        connect_papers(paths.ARXIV_DB).close()
        connect_arxiv_rag(paths.ARXIV_RAG_DB).close()
        rc = arxiv_embed_worker.main(["--job-id", "999"])
        assert rc == 1
