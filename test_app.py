"""Tests for the FastAPI web app.

The tests build a tiny SQLite database that mirrors the schema produced by
``parse/parse.py`` and point the app at it via the ``WIKI_DB`` environment
variable. This keeps the suite hermetic — it does not require a real
Wikipedia dump to be parsed first.
"""
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Importing the module (not just `app`) so we can also exercise its helpers
# directly if a future test wants to.
import app as web_app

# A pair of fixture articles. The wikitext is intentionally tiny but uses
# real wikitext markup so we can verify the full conversion pipeline.
FIXTURE_ARTICLES = [
    {
        "page_id": 1,
        "title": "April",
        "wikitext": (
            "'''April''' is the fourth [[month]] of the year.\n"
            "== Events ==\n"
            "* Spring begins.\n"
        ),
    },
    {
        "page_id": 2,
        "title": "Apple",
        "wikitext": "An '''apple''' is a [[fruit]].",
    },
    {
        "page_id": 3,
        "title": "Python (programming language)",
        "wikitext": "'''Python''' is a [[programming language]].",
    },
]


def _build_fixture_db(path: Path) -> None:
    """Create a minimal SQLite database matching the parser's schema.

    Only the columns the web app actually reads are populated; the rest
    are given placeholder values so the NOT NULL constraints are satisfied.
    """
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE articles (
            page_id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            namespace INTEGER NOT NULL DEFAULT 0,
            revision_id INTEGER NOT NULL,
            parent_revision_id INTEGER,
            timestamp TEXT NOT NULL,
            contributor_username TEXT,
            contributor_id INTEGER,
            comment TEXT,
            text_bytes INTEGER,
            text_content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("CREATE INDEX idx_articles_title ON articles(title)")
    conn.executemany(
        """
        INSERT INTO articles (
            page_id, title, namespace, revision_id, timestamp,
            text_bytes, text_content
        ) VALUES (?, ?, 0, 1, '2026-01-01T00:00:00Z', ?, ?)
        """,
        [
            (a["page_id"], a["title"], len(a["wikitext"]), a["wikitext"])
            for a in FIXTURE_ARTICLES
        ],
    )
    conn.commit()
    conn.close()


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Yield a ``TestClient`` wired to a fresh fixture database.

    Each test gets its own DB file in ``tmp_path`` so tests cannot leak
    state between each other.
    """
    db_path = tmp_path / "test.db"
    _build_fixture_db(db_path)
    monkeypatch.setenv("WIKI_DB", str(db_path))
    with TestClient(web_app.app) as c:
        yield c


class TestIndex:
    """The root page should render the search-box shell."""

    def test_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_contains_search_input(self, client):
        resp = client.get("/")
        # The HTMX-wired search input is the only thing the user sees on
        # first load; if it disappears the UI is broken.
        assert 'name="q"' in resp.text
        assert 'hx-get="/search"' in resp.text


class TestSearch:
    """`/search` returns an HTML fragment of matching titles."""

    def test_empty_query_returns_empty_fragment(self, client):
        resp = client.get("/search", params={"q": ""})
        assert resp.status_code == 200
        # No <ul>, no "no results" message — just empty whitespace.
        assert "<ul" not in resp.text
        assert "No articles match" not in resp.text

    def test_prefix_match(self, client):
        resp = client.get("/search", params={"q": "Apr"})
        assert resp.status_code == 200
        assert "April" in resp.text
        # Non-matching titles must not appear in the result list.
        assert "Apple" not in resp.text

    def test_substring_fallback(self, client):
        # "ril" is in "April" but not as a prefix; the substring fallback
        # should still surface it.
        resp = client.get("/search", params={"q": "ril"})
        assert resp.status_code == 200
        assert "April" in resp.text

    def test_no_match_shows_message(self, client):
        resp = client.get("/search", params={"q": "ZzzzNoSuch"})
        assert resp.status_code == 200
        assert "No articles match" in resp.text

    def test_result_links_use_htmx(self, client):
        resp = client.get("/search", params={"q": "Apr"})
        # Each result is a link that swaps the article panel via HTMX.
        assert 'hx-get="/article/' in resp.text
        assert 'hx-target="#article"' in resp.text


class TestArticle:
    """`/article/{title}` renders wikitext through the markdown pipeline."""

    def test_returns_rendered_html(self, client):
        resp = client.get("/article/April")
        assert resp.status_code == 200
        assert "<h2>April</h2>" in resp.text
        # Bold wikitext -> markdown ** -> <strong>.
        assert "<strong>April</strong>" in resp.text
        # Heading wikitext (== Events ==) -> ## Events -> <h2>Events</h2>.
        assert "Events" in resp.text

    def test_title_with_spaces_and_parens(self, client):
        # Round-trips a tricky title through URL encoding.
        resp = client.get("/article/Python%20(programming%20language)")
        assert resp.status_code == 200
        assert "Python (programming language)" in resp.text

    def test_missing_article_returns_404(self, client):
        resp = client.get("/article/NoSuchArticle")
        assert resp.status_code == 404

    def test_metadata_is_displayed(self, client):
        resp = client.get("/article/Apple")
        # The header shows byte size and timestamp from the DB row.
        assert "bytes" in resp.text
        assert "2026-01-01" in resp.text


class TestDatabaseMissing:
    """When the configured DB file is absent the app should fail loudly."""

    def test_503_when_db_missing(self, tmp_path, monkeypatch):
        # Point WIKI_DB at a path that does not exist; do not pre-create it.
        monkeypatch.setenv("WIKI_DB", str(tmp_path / "missing.db"))
        with TestClient(web_app.app) as c:
            resp = c.get("/search", params={"q": "April"})
            assert resp.status_code == 503
