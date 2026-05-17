"""Tests for app/routes/arxiv.py and the sidebar link integration."""

import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import app as web_app
import paths
from arxiv import jobs as arxiv_jobs
from arxiv.schema import connect_arxiv_rag, connect_papers
from rag.embedder import EMBEDDING_DIM, OLLAMA_BASE_URL
from tests.conftest import build_fixture_db


def _seed_arxiv(arxiv_db, arxiv_rag_db, papers):
    papers_conn = connect_papers(arxiv_db)
    rag_conn = connect_arxiv_rag(arxiv_rag_db)
    for p in papers:
        papers_conn.execute(
            "INSERT INTO papers (id, oai_datestamp, title, abstract, authors, "
            "categories, primary_category, submitted_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                p["id"],
                "2024-01-22",
                p["title"],
                p["abstract"],
                json.dumps(p.get("authors", ["A. Author"])),
                p["categories"],
                p["categories"].split()[0],
                "2024-01-22",
            ),
        )
        embed_text = f"{p['title']}\n\n{p['abstract']}\n\nCategories: {p['categories']}"
        rag_conn.execute(
            "INSERT INTO papers_meta (arxiv_id, oai_datestamp, embed_text, embedded_at) VALUES (?, ?, ?, ?)",
            (p["id"], "2024-01-22", embed_text, "2024-01-23T00:00:00Z"),
        )
    papers_conn.commit()
    rag_conn.commit()
    rag_conn.execute("INSERT INTO papers_fts(papers_fts) VALUES('rebuild')")
    rag_conn.commit()
    papers_conn.close()
    rag_conn.close()


@pytest.fixture
def arxiv_client(tmp_path, monkeypatch):
    """TestClient with arxiv DBs redirected to tmp_path, and a wiki fixture for the sidebar."""
    import rag.embedder

    arxiv_db = tmp_path / "arxiv.db"
    arxiv_rag_db = tmp_path / "arxiv_rag.db"
    jobs_db = tmp_path / "jobs.db"
    wiki_db = tmp_path / "test.db"
    build_fixture_db(wiki_db)

    monkeypatch.setattr(paths, "DUMPS_DIR", tmp_path)
    monkeypatch.setattr(paths, "ARXIV_DB", arxiv_db)
    monkeypatch.setattr(paths, "ARXIV_RAG_DB", arxiv_rag_db)
    monkeypatch.setattr(paths, "ARXIV_OAI_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(paths, "ARXIV_EMBED_LOG", tmp_path / "arxiv_embed.log")
    monkeypatch.setattr(paths, "JOBS_DB", jobs_db)
    monkeypatch.setenv("WIKI_DB", str(wiki_db))
    monkeypatch.setattr(rag.embedder, "_BACKOFF_BASE", 0)

    # Capture spawn_worker calls instead of actually forking a subprocess.
    spawned: list[tuple] = []
    import app.routes.arxiv as arxiv_routes

    monkeypatch.setattr(arxiv_routes, "spawn_worker", lambda *args, **kwargs: spawned.append((args, kwargs)))

    with TestClient(web_app.app) as c:
        c.arxiv_db = arxiv_db
        c.arxiv_rag_db = arxiv_rag_db
        c.jobs_db = jobs_db
        c.spawned = spawned
        yield c


class TestArxivShell:
    def test_returns_200(self, arxiv_client):
        resp = arxiv_client.get("/arxiv")
        assert resp.status_code == 200

    def test_contains_search_input(self, arxiv_client):
        resp = arxiv_client.get("/arxiv")
        assert 'name="q"' in resp.text
        assert 'hx-get="/arxiv/search"' in resp.text

    def test_sidebar_arxiv_link_marked_active(self, arxiv_client):
        resp = arxiv_client.get("/arxiv")
        # Sidebar link to /arxiv should carry the active class on the arxiv page.
        # Look for the link with both the active class and href.
        assert 'href="/arxiv"' in resp.text
        # The sidebar active class is rendered on the same element via Jinja conditional.
        import re

        match = re.search(r'<a class="sidebar-item ([^"]*)"\s+href="/arxiv"', resp.text)
        assert match is not None
        assert "sidebar-item--active" in match.group(1)


class TestArxivSearch:
    def test_empty_query_returns_no_results(self, arxiv_client):
        resp = arxiv_client.get("/arxiv/search", params={"q": ""})
        assert resp.status_code == 200
        # Empty q: no result list and no "not built" message.
        assert "arxiv-results-list" not in resp.text
        assert "no-results" not in resp.text

    def test_query_against_uninitialized_db_shows_not_built(self, arxiv_client):
        resp = arxiv_client.get("/arxiv/search", params={"q": "transformer"})
        assert resp.status_code == 200
        assert "not yet built" in resp.text.lower()

    def test_returns_result_cards_when_indexed(self, arxiv_client):
        _seed_arxiv(
            arxiv_client.arxiv_db,
            arxiv_client.arxiv_rag_db,
            [
                {
                    "id": "2401.0001",
                    "title": "Attention is all you need",
                    "abstract": "Transformers replace recurrence with self-attention.",
                    "categories": "cs.CL cs.LG",
                    "authors": ["A. Vaswani", "N. Shazeer"],
                },
            ],
        )
        resp = arxiv_client.get("/arxiv/search", params={"q": "transformer"})
        assert resp.status_code == 200
        assert "Attention is all you need" in resp.text
        assert "https://arxiv.org/abs/2401.0001" in resp.text
        assert 'target="_blank"' in resp.text

    def test_no_match_shows_no_papers_message(self, arxiv_client):
        _seed_arxiv(
            arxiv_client.arxiv_db,
            arxiv_client.arxiv_rag_db,
            [
                {
                    "id": "2401.0001",
                    "title": "Transformer paper",
                    "abstract": "attention",
                    "categories": "cs.CL",
                }
            ],
        )
        resp = arxiv_client.get("/arxiv/search", params={"q": "quantum cryptography"})
        assert resp.status_code == 200
        assert "no papers match" in resp.text.lower()

    def test_authors_truncated_with_et_al(self, arxiv_client):
        _seed_arxiv(
            arxiv_client.arxiv_db,
            arxiv_client.arxiv_rag_db,
            [
                {
                    "id": "2401.0001",
                    "title": "Many authors",
                    "abstract": "transformer attention",
                    "categories": "cs.CL",
                    "authors": ["A One", "B Two", "C Three", "D Four", "E Five"],
                }
            ],
        )
        resp = arxiv_client.get("/arxiv/search", params={"q": "transformer"})
        assert "A One" in resp.text
        assert "C Three" in resp.text
        assert "et al" in resp.text

    def test_categories_rendered_as_badges(self, arxiv_client):
        _seed_arxiv(
            arxiv_client.arxiv_db,
            arxiv_client.arxiv_rag_db,
            [
                {
                    "id": "2401.0001",
                    "title": "X",
                    "abstract": "transformer",
                    "categories": "cs.CL cs.LG math.OC",
                }
            ],
        )
        resp = arxiv_client.get("/arxiv/search", params={"q": "transformer"})
        for cat in ("cs.CL", "cs.LG", "math.OC"):
            assert cat in resp.text


class TestSidebarLink:
    def test_arxiv_link_present_on_home(self, client):
        resp = client.get("/")
        assert 'href="/arxiv"' in resp.text

    def test_arxiv_link_not_active_on_home(self, client):
        import re

        resp = client.get("/")
        match = re.search(r'<a class="sidebar-item ([^"]*)"\s+href="/arxiv"', resp.text)
        assert match is not None
        assert "sidebar-item--active" not in match.group(1)


class TestResultCardLinks:
    def test_card_title_links_to_local_paper_page(self, arxiv_client):
        _seed_arxiv(
            arxiv_client.arxiv_db,
            arxiv_client.arxiv_rag_db,
            [{"id": "2401.0001", "title": "X", "abstract": "transformer", "categories": "cs.CL"}],
        )
        resp = arxiv_client.get("/arxiv/search", params={"q": "transformer"})
        assert 'href="/arxiv/2401.0001"' in resp.text
        # External-link icon still goes to arxiv.org/abs
        assert "https://arxiv.org/abs/2401.0001" in resp.text


class TestArxivPaperPage:
    def test_returns_200_with_metadata(self, arxiv_client):
        _seed_arxiv(
            arxiv_client.arxiv_db,
            arxiv_client.arxiv_rag_db,
            [
                {
                    "id": "2401.0001",
                    "title": "Attention is all you need",
                    "abstract": "Transformers replace recurrence.",
                    "categories": "cs.CL cs.LG",
                    "authors": ["A. Vaswani", "N. Shazeer"],
                }
            ],
        )
        resp = arxiv_client.get("/arxiv/2401.0001")
        assert resp.status_code == 200
        assert "Attention is all you need" in resp.text
        assert "Transformers replace recurrence." in resp.text
        assert "A. Vaswani" in resp.text and "N. Shazeer" in resp.text
        assert "cs.CL" in resp.text
        assert "arXiv:2401.0001" in resp.text

    def test_404_when_paper_missing(self, arxiv_client):
        # Initialize empty DBs
        _seed_arxiv(arxiv_client.arxiv_db, arxiv_client.arxiv_rag_db, [])
        resp = arxiv_client.get("/arxiv/does-not-exist")
        assert resp.status_code == 404

    def test_shows_reembed_when_already_embedded(self, arxiv_client):
        _seed_arxiv(
            arxiv_client.arxiv_db,
            arxiv_client.arxiv_rag_db,
            [{"id": "2401.0001", "title": "T", "abstract": "a", "categories": "cs.CL"}],
        )
        resp = arxiv_client.get("/arxiv/2401.0001")
        # Abstract IS embedded by _seed_arxiv → button shows "Re-embed"
        assert "Re-embed abstract" in resp.text

    def test_shows_embed_when_not_embedded(self, arxiv_client):
        # Seed paper only, no rag row.
        papers_conn = connect_papers(arxiv_client.arxiv_db)
        papers_conn.execute(
            "INSERT INTO papers (id, oai_datestamp, title, abstract, authors, "
            "categories, primary_category, submitted_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("2401.0001", "2024-01-22", "T", "abs", "[]", "cs.CL", "cs.CL", "2024-01-22"),
        )
        papers_conn.commit()
        papers_conn.close()
        # Create empty rag DB
        connect_arxiv_rag(arxiv_client.arxiv_rag_db).close()

        resp = arxiv_client.get("/arxiv/2401.0001")
        assert "Embed abstract" in resp.text
        assert "Re-embed abstract" not in resp.text

    def test_no_html_status_disables_full_paper_button(self, arxiv_client):
        _seed_arxiv(
            arxiv_client.arxiv_db,
            arxiv_client.arxiv_rag_db,
            [{"id": "2401.0001", "title": "T", "abstract": "a", "categories": "cs.CL"}],
        )
        # Mark the paper as no_html
        rag = connect_arxiv_rag(arxiv_client.arxiv_rag_db)
        rag.execute(
            "INSERT INTO papers_full_meta (arxiv_id, oai_datestamp, status, embedded_at) VALUES (?, ?, ?, ?)",
            ("2401.0001", "2024-01-22", "no_html", "2024-01-23T00:00:00Z"),
        )
        rag.commit()
        rag.close()

        resp = arxiv_client.get("/arxiv/2401.0001")
        assert "No HTML available" in resp.text
        assert "disabled" in resp.text


class TestEmbedAbstractRoute:
    @respx.mock
    def test_returns_actions_partial(self, arxiv_client):
        papers_conn = connect_papers(arxiv_client.arxiv_db)
        papers_conn.execute(
            "INSERT INTO papers (id, oai_datestamp, title, abstract, authors, "
            "categories, primary_category, submitted_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("2401.0001", "2024-01-22", "T", "abs body", "[]", "cs.CL", "cs.CL", "2024-01-22"),
        )
        papers_conn.commit()
        papers_conn.close()
        connect_arxiv_rag(arxiv_client.arxiv_rag_db).close()

        respx.post(f"{OLLAMA_BASE_URL}/api/embed").mock(
            return_value=httpx.Response(200, json={"embeddings": [[0.1] * EMBEDDING_DIM]})
        )

        resp = arxiv_client.post("/arxiv/2401.0001/embed-abstract")
        assert resp.status_code == 200
        # Returned fragment shows "Re-embed abstract" — the action panel partial,
        # not the full page (no <html> wrapper).
        assert "Re-embed abstract" in resp.text
        assert "<html" not in resp.text.lower()

    def test_404_when_paper_missing(self, arxiv_client):
        _seed_arxiv(arxiv_client.arxiv_db, arxiv_client.arxiv_rag_db, [])
        resp = arxiv_client.post("/arxiv/does-not-exist/embed-abstract")
        assert resp.status_code == 404


class TestEmbedPaperRoute:
    def test_creates_job_and_redirects(self, arxiv_client):
        _seed_arxiv(
            arxiv_client.arxiv_db,
            arxiv_client.arxiv_rag_db,
            [{"id": "2401.0001", "title": "T", "abstract": "a", "categories": "cs.CL"}],
        )
        # Non-HTMX request → 303 redirect.
        resp = arxiv_client.post("/arxiv/2401.0001/embed-paper", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/arxiv/active-embedding"

        # Job was created with the paper enqueued
        jobs_conn = arxiv_jobs.connect_arxiv_jobs(arxiv_client.jobs_db)
        active = arxiv_jobs.get_active_job(jobs_conn)
        assert active is not None
        assert active["triggered_by_arxiv_id"] == "2401.0001"
        items = arxiv_jobs.get_items(jobs_conn, active["id"])
        assert [i["arxiv_id"] for i in items] == ["2401.0001"]
        jobs_conn.close()

        # Worker was "spawned" (captured by fixture).
        assert len(arxiv_client.spawned) == 1
        args, _ = arxiv_client.spawned[0]
        assert args[0] == "workers.arxiv_embed"

    def test_appends_to_active_job_no_new_spawn(self, arxiv_client):
        _seed_arxiv(
            arxiv_client.arxiv_db,
            arxiv_client.arxiv_rag_db,
            [
                {"id": "2401.0001", "title": "X", "abstract": "a", "categories": "cs.CL"},
                {"id": "2401.0002", "title": "Y", "abstract": "b", "categories": "cs.CL"},
            ],
        )
        arxiv_client.post("/arxiv/2401.0001/embed-paper", follow_redirects=False)
        arxiv_client.post("/arxiv/2401.0002/embed-paper", follow_redirects=False)

        # One job, two items. Worker spawned once.
        jobs_conn = arxiv_jobs.connect_arxiv_jobs(arxiv_client.jobs_db)
        jobs = arxiv_jobs.get_latest_jobs(jobs_conn, limit=5)
        assert len(jobs) == 1
        items = arxiv_jobs.get_items(jobs_conn, jobs[0]["id"])
        assert {i["arxiv_id"] for i in items} == {"2401.0001", "2401.0002"}
        jobs_conn.close()

        assert len(arxiv_client.spawned) == 1

    def test_htmx_request_returns_204(self, arxiv_client):
        _seed_arxiv(
            arxiv_client.arxiv_db,
            arxiv_client.arxiv_rag_db,
            [{"id": "2401.0001", "title": "T", "abstract": "a", "categories": "cs.CL"}],
        )
        resp = arxiv_client.post(
            "/arxiv/2401.0001/embed-paper",
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert resp.status_code == 204
        assert resp.headers["HX-Redirect"] == "/arxiv/active-embedding"

    def test_404_when_paper_missing(self, arxiv_client):
        _seed_arxiv(arxiv_client.arxiv_db, arxiv_client.arxiv_rag_db, [])
        resp = arxiv_client.post("/arxiv/does-not-exist/embed-paper")
        assert resp.status_code == 404


class TestActiveEmbeddingPage:
    def test_renders_empty_state_with_no_job(self, arxiv_client):
        resp = arxiv_client.get("/arxiv/active-embedding")
        assert resp.status_code == 200
        assert "No full-paper embed job is running" in resp.text

    def test_renders_job_when_present(self, arxiv_client):
        _seed_arxiv(
            arxiv_client.arxiv_db,
            arxiv_client.arxiv_rag_db,
            [{"id": "2401.0001", "title": "T", "abstract": "a", "categories": "cs.CL"}],
        )
        arxiv_client.post("/arxiv/2401.0001/embed-paper", follow_redirects=False)

        resp = arxiv_client.get("/arxiv/active-embedding")
        assert "2401.0001" in resp.text
        assert "arXiv embed job #" in resp.text

    def test_panel_endpoint_returns_fragment(self, arxiv_client):
        resp = arxiv_client.get("/arxiv/active-embedding/panel")
        assert resp.status_code == 200
        assert "<html" not in resp.text.lower()


class TestActiveEmbeddingCancel:
    def test_sets_cancel_flag(self, arxiv_client):
        _seed_arxiv(
            arxiv_client.arxiv_db,
            arxiv_client.arxiv_rag_db,
            [{"id": "2401.0001", "title": "T", "abstract": "a", "categories": "cs.CL"}],
        )
        arxiv_client.post("/arxiv/2401.0001/embed-paper", follow_redirects=False)
        jobs_conn = arxiv_jobs.connect_arxiv_jobs(arxiv_client.jobs_db)
        job_id = arxiv_jobs.get_active_job(jobs_conn)["id"]
        jobs_conn.close()

        resp = arxiv_client.post(f"/arxiv/active-embedding/cancel/{job_id}")
        assert resp.status_code == 200

        jobs_conn = arxiv_jobs.connect_arxiv_jobs(arxiv_client.jobs_db)
        job = arxiv_jobs.get_job(jobs_conn, job_id)
        assert job["cancel_requested"] == 1
        jobs_conn.close()

    def test_404_for_missing_job(self, arxiv_client):
        # Initialize empty jobs.db so the route can open it.
        arxiv_jobs.connect_arxiv_jobs(arxiv_client.jobs_db).close()
        resp = arxiv_client.post("/arxiv/active-embedding/cancel/999")
        assert resp.status_code == 404
