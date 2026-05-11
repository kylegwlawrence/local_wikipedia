"""Tests for the FastAPI web app.

The tests build a tiny SQLite database that mirrors the schema produced by
``parse.pipeline`` and point the app at it via the ``WIKI_DB`` environment
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
    # Single-hop redirect — content is just the redirect stub.
    {
        "page_id": 4,
        "title": "Apples",
        "wikitext": "#REDIRECT [[Apple]]",
    },
    # Two-hop redirect: Pyton -> Python (programming language) -> nothing.
    # Tests the chain follows past one hop.
    {
        "page_id": 5,
        "title": "Pyton",
        "wikitext": "#REDIRECT [[Python (programming language)]]",
    },
    # Cyclic redirect to test the cycle guard.
    {
        "page_id": 6,
        "title": "LoopA",
        "wikitext": "#REDIRECT [[LoopB]]",
    },
    {
        "page_id": 7,
        "title": "LoopB",
        "wikitext": "#REDIRECT [[LoopA]]",
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
    conn.execute("""
        CREATE VIRTUAL TABLE articles_fts USING fts5(
            title,
            content=articles,
            content_rowid=page_id,
            tokenize='trigram'
        )
    """)
    conn.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
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

    def test_internal_links_point_at_local_endpoint(self, client):
        # The Python article links to [[programming language]]; the rendered
        # output should rewrite that to a local /article/ link with HTMX
        # attributes so the front-end loads it as a fragment swap.
        resp = client.get("/article/Python%20(programming%20language)")
        assert resp.status_code == 200
        # Title is capitalised per MediaWiki convention.
        assert 'href="/article/Programming%20language"' in resp.text
        assert 'hx-get="/article/Programming%20language"' in resp.text
        assert 'hx-target="#article"' in resp.text
        # And nothing should still be pointing at en.wikipedia.org.
        assert "en.wikipedia.org" not in resp.text


class TestRedirects:
    """`_fetch_article` follows ``#REDIRECT`` chains."""

    def test_single_hop_redirect_follows_to_target(self, client):
        # 'Apples' redirects to 'Apple'; we should see Apple's content.
        resp = client.get("/article/Apples")
        assert resp.status_code == 200
        assert "<h2>Apple</h2>" in resp.text
        assert "An <strong>apple</strong>" in resp.text

    def test_redirect_displays_redirected_from_note(self, client):
        resp = client.get("/article/Apples")
        # The note tells the user they were redirected, citing the title
        # they originally clicked.
        assert "Redirected from" in resp.text
        assert "Apples" in resp.text

    def test_non_redirect_has_no_redirected_from_note(self, client):
        resp = client.get("/article/Apple")
        assert "Redirected from" not in resp.text

    def test_multi_hop_redirect_chain(self, client):
        # Pyton -> Python (programming language).
        resp = client.get("/article/Pyton")
        assert resp.status_code == 200
        assert "<h2>Python (programming language)</h2>" in resp.text
        assert "Redirected from" in resp.text

    def test_redirect_cycle_returns_404(self, client):
        # LoopA -> LoopB -> LoopA. The cycle guard should bail out and the
        # endpoint should surface a 404 rather than hanging.
        resp = client.get("/article/LoopA")
        assert resp.status_code == 404


class TestWikitext:
    """`/wikitext/{title}` returns the raw wikitext before Markdown conversion."""

    def test_returns_raw_wikitext(self, client):
        resp = client.get("/wikitext/April")
        assert resp.status_code == 200
        # Wikitext markup is present. Single quotes are HTML-escaped by Jinja2
        # auto-escaping, so ''' becomes &#39;&#39;&#39;.
        assert "&#39;&#39;&#39;April&#39;&#39;&#39;" in resp.text
        assert "== Events ==" in resp.text

    def test_not_converted_to_html(self, client):
        resp = client.get("/wikitext/April")
        # Wikitext bold (''' ''') must NOT have been rendered to <strong>.
        assert "<strong>" not in resp.text

    def test_title_with_spaces_and_parens(self, client):
        resp = client.get("/wikitext/Python%20(programming%20language)")
        assert resp.status_code == 200
        assert "Python (programming language)" in resp.text

    def test_missing_article_returns_404(self, client):
        resp = client.get("/wikitext/NoSuchArticle")
        assert resp.status_code == 404

    def test_toggle_button_links_to_rendered_view(self, client):
        resp = client.get("/wikitext/April")
        assert 'hx-get="/article/' in resp.text


class TestDatabaseMissing:
    """When the configured DB file is absent the app should fail loudly."""

    def test_503_when_db_missing(self, tmp_path, monkeypatch):
        # Point WIKI_DB at a path that does not exist; do not pre-create it.
        monkeypatch.setenv("WIKI_DB", str(tmp_path / "missing.db"))
        with TestClient(web_app.app) as c:
            resp = c.get("/search", params={"q": "April"})
            assert resp.status_code == 503


@pytest.fixture
def embed_client(tmp_path, monkeypatch):
    """Yield a TestClient with the embed-links route plumbing isolated.

    The fixture DB is the same as the regular client; in addition we point
    ``app.JOBS_DB`` at a tmp file (so refresh- and embed-job rows don't
    leak between tests), and we stub ``subprocess.Popen`` to a no-op so the
    embed_worker.py subprocess is never actually spawned.
    """
    import subprocess
    import embed_jobs

    db_path = tmp_path / "test.db"
    _build_fixture_db(db_path)
    monkeypatch.setenv("WIKI_DB", str(db_path))

    jobs_db = tmp_path / "jobs.db"
    monkeypatch.setattr(web_app, "JOBS_DB", jobs_db)
    # The embed-links route uses BASE_DIR to spawn embed_worker.py; the spawn
    # itself is stubbed, but the log_path string is still derived from BASE_DIR.
    monkeypatch.setattr(web_app, "BASE_DIR", tmp_path)

    spawned: list[list[str]] = []

    def fake_popen(args, **kwargs):
        spawned.append(args)

        class _P:
            pass

        return _P()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    with TestClient(web_app.app) as c:
        c.spawned = spawned
        c.jobs_db = jobs_db
        c.embed_jobs = embed_jobs
        yield c


class TestEmbedLinks:
    """`POST /embed-links/{title}` enqueues source + linked articles."""

    def test_404_for_unknown_source(self, embed_client):
        resp = embed_client.post(
            "/embed-links/NoSuchArticle", follow_redirects=False,
        )
        assert resp.status_code == 404

    def test_enqueues_source_and_links(self, embed_client):
        # 'April' wikitext links to [[month]]. The route should enqueue
        # 'April' (the source) and 'Month' (the link, capitalised). Since
        # 'month' is not in the fixture DB, redirect resolution returns None
        # and the canonicalised target ('Month') is enqueued as not_found.
        resp = embed_client.post("/embed-links/April", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/active-embedding"

        conn = embed_client.embed_jobs.connect_embed_jobs(embed_client.jobs_db)
        try:
            job = embed_client.embed_jobs.get_active_job(conn, "enwiki")
            assert job is not None
            items = embed_client.embed_jobs.get_items(conn, job["id"])
            titles = [r["title"] for r in items]
            assert titles == ["April", "Month"]
            # All items should share the source title.
            assert {r["source_title"] for r in items} == {"April"}
        finally:
            conn.close()

        # A worker should have been spawned exactly once.
        assert len(embed_client.spawned) == 1
        assert embed_client.spawned[0][1].endswith("embed_worker.py")

    def test_redirect_targets_collapse_to_canonical(self, embed_client):
        # Inject an article whose body links to a redirect stub ('Apples'
        # which redirects to 'Apple'). The canonical title should appear in
        # the queue, not the redirect stub title.
        import os
        import sqlite3
        c = sqlite3.connect(os.environ["WIKI_DB"])
        c.execute(
            "INSERT INTO articles (page_id, title, namespace, revision_id, "
            "timestamp, text_bytes, text_content) "
            "VALUES (?, ?, 0, 1, '2026-01-01T00:00:00Z', ?, ?)",
            (100, "LinkerArticle", 40, "Body links to [[Apples]]."),
        )
        c.commit()
        c.close()

        resp = embed_client.post(
            "/embed-links/LinkerArticle", follow_redirects=False,
        )
        assert resp.status_code == 303

        conn = embed_client.embed_jobs.connect_embed_jobs(embed_client.jobs_db)
        try:
            job = embed_client.embed_jobs.get_active_job(conn, "enwiki")
            titles = [r["title"] for r in embed_client.embed_jobs.get_items(
                conn, job["id"],
            )]
            # 'Apples' is a redirect to 'Apple'; canonical 'Apple' should appear.
            assert "Apple" in titles
            assert "Apples" not in titles
        finally:
            conn.close()

    def test_second_click_appends_to_running_job(self, embed_client):
        # First click — creates job, spawns worker.
        r1 = embed_client.post("/embed-links/April", follow_redirects=False)
        assert r1.status_code == 303
        # Second click on a different source — should append to the same job,
        # NOT spawn a second worker.
        r2 = embed_client.post("/embed-links/Apple", follow_redirects=False)
        assert r2.status_code == 303

        assert len(embed_client.spawned) == 1

        conn = embed_client.embed_jobs.connect_embed_jobs(embed_client.jobs_db)
        try:
            jobs = embed_client.embed_jobs.get_latest_jobs(conn, "enwiki")
            assert len(jobs) == 1
            job_id = jobs[0]["id"]
            items = embed_client.embed_jobs.get_items(conn, job_id)
            titles = {r["title"] for r in items}
            # Both sources and their (resolved) link targets are present.
            assert {"April", "Apple"}.issubset(titles)
        finally:
            conn.close()

    def test_hx_request_returns_hx_redirect_header(self, embed_client):
        resp = embed_client.post(
            "/embed-links/April",
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        # HTMX wants HX-Redirect, not a 3xx.
        assert resp.status_code == 204
        assert resp.headers["hx-redirect"] == "/active-embedding"


class TestActiveEmbedding:
    """`/active-embedding` and its panel + cancel endpoints."""

    def test_empty_state(self, embed_client):
        resp = embed_client.get("/active-embedding")
        assert resp.status_code == 200
        assert "No batch embed has been run yet" in resp.text

    def test_shows_running_job(self, embed_client):
        embed_client.post("/embed-links/April", follow_redirects=False)
        resp = embed_client.get("/active-embedding")
        assert resp.status_code == 200
        # Status badge + the source-group heading.
        assert "running" in resp.text
        assert "From <a href" in resp.text
        assert "April" in resp.text

    def test_panel_polls_while_running(self, embed_client):
        embed_client.post("/embed-links/April", follow_redirects=False)
        resp = embed_client.get("/active-embedding/panel")
        assert resp.status_code == 200
        assert 'hx-trigger="every 3s"' in resp.text

    def test_cancel_sets_flag_and_stops_polling(self, embed_client):
        embed_client.post("/embed-links/April", follow_redirects=False)
        conn = embed_client.embed_jobs.connect_embed_jobs(embed_client.jobs_db)
        try:
            job_id = embed_client.embed_jobs.get_active_job(conn, "enwiki")["id"]
        finally:
            conn.close()

        resp = embed_client.post(f"/active-embedding/cancel/{job_id}")
        assert resp.status_code == 200
        # Polling attribute should be gone once cancel is requested.
        assert 'hx-trigger="every 3s"' not in resp.text
        assert "cancelling" in resp.text or "cancel" in resp.text.lower()
