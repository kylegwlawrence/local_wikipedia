"""Tests for app/routes/arxiv.py and the sidebar link integration."""

import json

import pytest
from fastapi.testclient import TestClient

import app as web_app
import paths
from arxiv.schema import connect_arxiv_rag, connect_papers
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
    wiki_db = tmp_path / "test.db"
    build_fixture_db(wiki_db)

    monkeypatch.setattr(paths, "DUMPS_DIR", tmp_path)
    monkeypatch.setattr(paths, "ARXIV_DB", arxiv_db)
    monkeypatch.setattr(paths, "ARXIV_RAG_DB", arxiv_rag_db)
    monkeypatch.setattr(paths, "ARXIV_OAI_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setenv("WIKI_DB", str(wiki_db))
    monkeypatch.setattr(rag.embedder, "_BACKOFF_BASE", 0)

    with TestClient(web_app.app) as c:
        c.arxiv_db = arxiv_db
        c.arxiv_rag_db = arxiv_rag_db
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
