"""Tests for the FastAPI web app.

The tests build a tiny SQLite database that mirrors the schema produced by
``parse.pipeline`` and point the app at it via the ``WIKI_DB`` environment
variable. This keeps the suite hermetic — it does not require a real
Wikipedia dump to be parsed first.

Shared fixtures (``client``, ``embed_client``, ``crash_recovery_env``,
``wiki_db_path``) live in ``tests/conftest.py``.
"""

from fastapi.testclient import TestClient

import app as web_app


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


class TestDailyArticles:
    """Two random article cards rendered below the search bar."""

    REDIRECT_TITLES = {"Apples", "Pyton", "LoopA", "LoopB"}

    def _card_titles(self, html: str) -> list[str]:
        import re

        return re.findall(r'<h2 class="daily-card__title">([^<]+)</h2>', html)

    def test_two_cards_appear(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.text.count('class="daily-card"') == 2
        assert resp.text.count('class="daily-card__title"') == 2
        assert resp.text.count('class="daily-card__snippet"') == 2

    def test_cards_link_to_articles(self, client):
        resp = client.get("/")
        titles = self._card_titles(resp.text)
        assert len(titles) == 2
        for title in titles:
            assert f'href="/article/{title.replace(" ", "%20").replace("(", "%28").replace(")", "%29")}"' in resp.text \
                or f'href="/article/{title}"' in resp.text

    def test_cards_persist_within_same_day(self, client):
        first = self._card_titles(client.get("/").text)
        second = self._card_titles(client.get("/").text)
        assert first == second
        assert len(first) == 2

    def test_cards_rotate_on_new_day(self, client, monkeypatch, wiki_db_path):
        import sqlite3

        from app import helpers

        client.get("/")  # locks in today's picks
        monkeypatch.setattr(helpers, "_today_iso", lambda: "2099-12-31")
        client.get("/")
        conn = sqlite3.connect(wiki_db_path)
        row = conn.execute(
            "SELECT value FROM db_metadata WHERE key = 'daily_articles_date'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "2099-12-31"

    def test_redirects_never_appear_as_cards(self, client, monkeypatch):
        # Force fresh picks 20 times by rolling the date; redirects in the
        # fixture (Apples, Pyton, LoopA, LoopB) must never leak through.
        from app import helpers

        for i in range(20):
            monkeypatch.setattr(helpers, "_today_iso", lambda i=i: f"2099-01-{i + 1:02d}")
            titles = set(self._card_titles(client.get("/").text))
            assert not (titles & self.REDIRECT_TITLES)

    def test_small_articles_never_appear_as_cards(self, client, monkeypatch):
        # April / Apple / Python wikitexts are all <100 bytes — well below
        # the 3 KB minimum — so the picker must skip them.
        from app import helpers

        small_titles = {"April", "Apple", "Python (programming language)"}
        for i in range(20):
            monkeypatch.setattr(helpers, "_today_iso", lambda i=i: f"2099-02-{i + 1:02d}")
            titles = set(self._card_titles(client.get("/").text))
            assert not (titles & small_titles)

    def test_extract_first_sentence_strips_markup(self):
        from app.helpers import _extract_first_sentence

        assert _extract_first_sentence("'''Foo''' is a [[bar]]. Another one.") == "Foo is a bar."

    def test_extract_first_sentence_slices_at_first_heading(self):
        from app.helpers import _extract_first_sentence

        wikitext = "'''April''' is the fourth month.\n== History ==\nLong history follows."
        result = _extract_first_sentence(wikitext)
        assert "April" in result
        assert "history" not in result.lower()

    def test_extract_first_sentence_returns_empty_for_empty_input(self):
        from app.helpers import _extract_first_sentence

        assert _extract_first_sentence("") == ""


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

    def test_result_links_navigate_to_article(self, client):
        resp = client.get("/search", params={"q": "Apr"})
        # Results are plain navigation links to the article page.
        assert 'href="/article/' in resp.text


class TestArticle:
    """`/article/{title}` renders wikitext through the markdown pipeline."""

    def test_returns_rendered_html(self, client):
        resp = client.get("/article/April")
        assert resp.status_code == 200
        # Article title appears in the page header.
        assert 'class="article-title"' in resp.text
        assert "April" in resp.text
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
        # Internal wikilinks rewrite to plain /article/ hrefs for full-page
        # navigation. No htmx attributes — the full-page article template
        # has no #article target.
        resp = client.get("/article/Python%20(programming%20language)")
        assert resp.status_code == 200
        # Title is capitalised per MediaWiki convention.
        assert 'href="/article/Programming%20language"' in resp.text
        assert "hx-get=" not in resp.text or 'hx-get="/article/' not in resp.text
        # And nothing should still be pointing at en.wikipedia.org.
        assert "en.wikipedia.org" not in resp.text


class TestRedirects:
    """`_fetch_article` follows ``#REDIRECT`` chains."""

    def test_single_hop_redirect_follows_to_target(self, client):
        # 'Apples' redirects to 'Apple'; we should see Apple's content.
        resp = client.get("/article/Apples")
        assert resp.status_code == 200
        assert "Apple" in resp.text
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
        assert "Python (programming language)" in resp.text
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
        assert 'href="/article/' in resp.text


class TestDatabaseMissing:
    """When the configured DB file is absent the app should fail loudly."""

    def test_503_when_db_missing(self, tmp_path, monkeypatch):
        # Point WIKI_DB at a path that does not exist; do not pre-create it.
        monkeypatch.setenv("WIKI_DB", str(tmp_path / "missing.db"))
        with TestClient(web_app.app) as c:
            resp = c.get("/search", params={"q": "April"})
            assert resp.status_code == 503


class TestEmbedLinks:
    """`POST /embed-links/{title}` enqueues source + linked articles."""

    def test_404_for_unknown_source(self, embed_client):
        resp = embed_client.post(
            "/embed-links/NoSuchArticle",
            follow_redirects=False,
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
            # Job should record which article triggered the embed.
            assert job["triggered_by_title"] == "April"
        finally:
            conn.close()

        # A worker should have been spawned exactly once via `python -m workers.embed`.
        assert len(embed_client.spawned) == 1
        assert embed_client.spawned[0][1:3] == ["-m", "workers.embed"]

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
            "/embed-links/LinkerArticle",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        conn = embed_client.embed_jobs.connect_embed_jobs(embed_client.jobs_db)
        try:
            job = embed_client.embed_jobs.get_active_job(conn, "enwiki")
            titles = [
                r["title"]
                for r in embed_client.embed_jobs.get_items(
                    conn,
                    job["id"],
                )
            ]
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

    def test_enqueues_at_zero_hops_remaining(self, embed_client):
        # The single-hop route uses hops_remaining=0 for every item — there's
        # no worker recursion involved.
        embed_client.post("/embed-links/April", follow_redirects=False)
        conn = embed_client.embed_jobs.connect_embed_jobs(embed_client.jobs_db)
        try:
            job = embed_client.embed_jobs.get_active_job(conn, "enwiki")
            items = embed_client.embed_jobs.get_items(conn, job["id"])
            assert {r["hops_remaining"] for r in items} == {0}
        finally:
            conn.close()


class TestEmbedLinks2:
    """`POST /embed-links-2/{title}` enqueues source + 1-hop links at depth=1."""

    def test_404_for_unknown_source(self, embed_client):
        resp = embed_client.post(
            "/embed-links-2/NoSuchArticle",
            follow_redirects=False,
        )
        assert resp.status_code == 404

    def test_source_enqueued_at_hops_zero_links_at_hops_one(self, embed_client):
        # 'April' links to [[month]] (resolved to 'Month'). The new route
        # should enqueue April at hops=0 (no need to re-expand — its 1-hop
        # links are already enqueued) and Month at hops=1 so the worker
        # expands it once more.
        resp = embed_client.post("/embed-links-2/April", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/active-embedding"

        conn = embed_client.embed_jobs.connect_embed_jobs(embed_client.jobs_db)
        try:
            job = embed_client.embed_jobs.get_active_job(conn, "enwiki")
            assert job is not None
            items = embed_client.embed_jobs.get_items(conn, job["id"])
            by_title = {r["title"]: r["hops_remaining"] for r in items}
            assert by_title == {"April": 0, "Month": 1}
        finally:
            conn.close()

    def test_spawns_worker_when_no_active_job(self, embed_client):
        embed_client.post("/embed-links-2/April", follow_redirects=False)
        assert len(embed_client.spawned) == 1
        assert embed_client.spawned[0][1:3] == ["-m", "workers.embed"]

    def test_second_click_appends_without_spawning_again(self, embed_client):
        embed_client.post("/embed-links-2/April", follow_redirects=False)
        embed_client.post("/embed-links-2/Apple", follow_redirects=False)
        # Only one worker — second trigger appends to the running job.
        assert len(embed_client.spawned) == 1


class TestEmbedStatusWidget:
    """`GET /embed-status/{title}` reflects whether links have been embedded."""

    def test_shows_embed_links_button_when_no_job(self, embed_client):
        resp = embed_client.get("/embed-status/April")
        assert resp.status_code == 200
        assert "Embed + links" in resp.text
        # The 2-hop button (Embed + links²) sits next to it.
        assert "/embed-links-2/April" in resp.text
        assert "links embedded" not in resp.text

    def test_shows_1hop_count_inline(self, embed_client):
        # April's wikitext links to [[month]]; Month isn't in the fixture wiki
        # DB so the worker would mark it ``not_found``. The 1-hop count
        # excludes not_found targets, so the badge reads "(0)".
        resp = embed_client.get("/embed-status/April")
        assert resp.status_code == 200
        assert "Embed + links (0)" in resp.text

    def test_shows_2hop_count_placeholder(self, embed_client):
        # The widget should render the async-loading span for the 2-hop count.
        resp = embed_client.get("/embed-status/April")
        assert resp.status_code == 200
        assert 'class="link-count"' in resp.text
        assert 'hx-get="/embed-count-2/April"' in resp.text
        assert 'hx-trigger="load"' in resp.text

    def test_count_hidden_when_article_missing_from_wiki_db(self, embed_client):
        # When no wiki row exists, link_count_1hop is None — count omitted.
        resp = embed_client.get("/embed-status/DoesNotExist")
        assert resp.status_code == 200
        assert "Embed + links (" not in resp.text

    def test_shows_links_embedded_badge_after_complete_job(self, embed_client, tmp_path, monkeypatch):
        from rag.schema import connect_rag

        # Create a RAG DB with April marked as links_embedded=1.
        rag_path = tmp_path / "dumps" / "enwiki_rag.db"
        rag_path.parent.mkdir(exist_ok=True)
        rag_conn = connect_rag(rag_path)
        rag_conn.execute(
            "INSERT INTO articles_meta "
            "(page_id, title, revision_id, links_embedded) "
            "VALUES (1, 'April', 1, 1)"
        )
        rag_conn.commit()
        rag_conn.close()

        import paths

        monkeypatch.setattr(paths, "rag_db_path_for", lambda wiki: rag_path)

        resp = embed_client.get("/embed-status/April")
        assert resp.status_code == 200
        assert "links embedded" in resp.text
        assert 'hx-post="/embed-links/April"' not in resp.text

    def test_shows_links_2hop_badge_after_complete_job(self, embed_client, tmp_path, monkeypatch):
        from rag.schema import connect_rag

        rag_path = tmp_path / "dumps" / "enwiki_rag.db"
        rag_path.parent.mkdir(exist_ok=True)
        rag_conn = connect_rag(rag_path)
        rag_conn.execute(
            "INSERT INTO articles_meta "
            "(page_id, title, revision_id, links_embedded, links_embedded_2hop) "
            "VALUES (1, 'April', 1, 1, 1)"
        )
        rag_conn.commit()
        rag_conn.close()

        import paths

        monkeypatch.setattr(paths, "rag_db_path_for", lambda wiki: rag_path)

        resp = embed_client.get("/embed-status/April")
        assert resp.status_code == 200
        assert "links² embedded" in resp.text
        assert 'hx-post="/embed-links-2/April"' not in resp.text

    def test_running_job_does_not_show_badge(self, embed_client):
        # A running (not yet complete) job should not trigger the badge.
        conn = embed_client.embed_jobs.connect_embed_jobs(embed_client.jobs_db)
        try:
            embed_client.embed_jobs.create_job(conn, "enwiki", "/tmp/x.log", "April")
        finally:
            conn.close()

        resp = embed_client.get("/embed-status/April")
        assert resp.status_code == 200
        assert "Embed + links" in resp.text
        assert "links embedded" not in resp.text


class TestEmbedCount2:
    """`GET /embed-count-2/{title}` returns the deferred 2-hop link count."""

    def test_returns_count_fragment(self, embed_client):
        # April → [[month]]; Month is absent from the fixture wiki DB, so the
        # worker would mark it ``not_found`` and never embed it. The count
        # excludes not_found targets, so the badge is 0.
        resp = embed_client.get("/embed-count-2/April")
        assert resp.status_code == 200
        assert resp.text == '<span class="link-count"> (0)</span>'

    def test_missing_article_returns_empty_fragment(self, embed_client):
        resp = embed_client.get("/embed-count-2/DoesNotExist")
        assert resp.status_code == 200
        assert resp.text == '<span class="link-count"></span>'

    def test_excludes_already_embedded(self, embed_client, tmp_path, monkeypatch):
        from rag.schema import connect_rag

        # Pre-mark Month as embedded so the only 1-hop target is excluded.
        rag_path = tmp_path / "dumps" / "enwiki_rag.db"
        rag_path.parent.mkdir(exist_ok=True)
        rag_conn = connect_rag(rag_path)
        rag_conn.execute(
            "INSERT INTO articles_meta (page_id, title, revision_id) VALUES (99, 'Month', 1)"
        )
        rag_conn.commit()
        rag_conn.close()

        import paths

        monkeypatch.setattr(paths, "rag_db_path_for", lambda wiki: rag_path)

        resp = embed_client.get("/embed-count-2/April")
        assert resp.status_code == 200
        assert resp.text == '<span class="link-count"> (0)</span>'


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
        # Status chip and the source-group heading.
        assert "running" in resp.text
        assert "From" in resp.text
        assert "April" in resp.text

    def test_panel_polls_while_running(self, embed_client):
        embed_client.post("/embed-links/April", follow_redirects=False)
        resp = embed_client.get("/active-embedding/panel")
        assert resp.status_code == 200
        assert 'hx-trigger="every 3s"' in resp.text

    def test_items_paginate_at_100_per_page(self, embed_client):
        # Seed a job with 150 items directly in jobs.db.
        conn = embed_client.embed_jobs.connect_embed_jobs(embed_client.jobs_db)
        try:
            job_id = embed_client.embed_jobs.create_job(
                conn, "enwiki", "/tmp/x.log", "Seed"
            )
            embed_client.embed_jobs.append_items(
                conn, job_id, [(f"Item{i:03d}", "Seed", 0) for i in range(150)]
            )
        finally:
            conn.close()

        # Page 1: shows first 100 items, has "Next →" link.
        resp = embed_client.get("/active-embedding")
        assert resp.status_code == 200
        assert "Item000" in resp.text
        assert "Item099" in resp.text
        assert "Item100" not in resp.text
        assert "Page 1 of 2" in resp.text
        assert "panel_page=2" in resp.text

        # Page 2: shows remaining 50 items, has "← Prev" link.
        resp = embed_client.get("/active-embedding?panel_page=2")
        assert resp.status_code == 200
        assert "Item099" not in resp.text
        assert "Item100" in resp.text
        assert "Item149" in resp.text
        assert "Page 2 of 2" in resp.text
        assert "panel_page=1" in resp.text

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


class TestCrashRecovery:
    """Lifespan startup hook cleans up after a crashed worker."""

    def test_orphaned_refresh_job_marked_failed(self, crash_recovery_env):
        jobs = crash_recovery_env["jobs"]
        conn = jobs.connect_jobs(crash_recovery_env["jobs_db"])
        try:
            conn.execute(
                "INSERT INTO refresh_jobs (wiki, status) VALUES (?, ?)",
                ("enwiki", "downloading"),
            )
            conn.commit()
        finally:
            conn.close()

        # Entering TestClient triggers the lifespan hook.
        with TestClient(web_app.app):
            pass

        conn = jobs.connect_jobs(crash_recovery_env["jobs_db"])
        try:
            row = conn.execute(
                "SELECT status, error_message FROM refresh_jobs WHERE wiki = ?",
                ("enwiki",),
            ).fetchone()
        finally:
            conn.close()
        assert row["status"] == "failed"
        assert "interrupted" in row["error_message"].lower()

    def test_orphaned_embed_job_and_items_marked_failed(self, crash_recovery_env):
        embed_jobs = crash_recovery_env["embed_jobs"]
        conn = embed_jobs.connect_embed_jobs(crash_recovery_env["jobs_db"])
        try:
            cur = conn.execute(
                "INSERT INTO embed_jobs (wiki, status) VALUES (?, ?)",
                ("enwiki", "running"),
            )
            job_id = cur.lastrowid
            conn.execute(
                "INSERT INTO embed_job_items (job_id, title, source_title, status) VALUES (?, ?, ?, ?)",
                (job_id, "April", "April", "in_progress"),
            )
            conn.execute(
                "INSERT INTO embed_job_items (job_id, title, source_title, status) VALUES (?, ?, ?, ?)",
                (job_id, "Apple", "April", "queued"),
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(web_app.app):
            pass

        conn = embed_jobs.connect_embed_jobs(crash_recovery_env["jobs_db"])
        try:
            job = conn.execute(
                "SELECT status, error_message FROM embed_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            items = conn.execute(
                "SELECT title, status FROM embed_job_items WHERE job_id = ? ORDER BY id",
                (job_id,),
            ).fetchall()
        finally:
            conn.close()
        assert job["status"] == "failed"
        assert "interrupted" in job["error_message"].lower()
        # The in_progress item is reset; the queued item is left alone (the
        # parent job is failed so it won't be picked up anyway).
        item_status = {r["title"]: r["status"] for r in items}
        assert item_status["April"] == "failed"
        assert item_status["Apple"] == "queued"

    def test_dirty_fts_triggers_rebuild_on_startup(self, crash_recovery_env):
        jobs = crash_recovery_env["jobs"]
        conn = jobs.connect_jobs(crash_recovery_env["jobs_db"])
        try:
            jobs.set_fts_dirty(conn, "enwiki", True)
            assert "enwiki" in jobs.get_fts_dirty_wikis(conn)
        finally:
            conn.close()

        with TestClient(web_app.app):
            pass

        conn = jobs.connect_jobs(crash_recovery_env["jobs_db"])
        try:
            assert jobs.get_fts_dirty_wikis(conn) == []
        finally:
            conn.close()

    def test_dirty_fts_for_missing_db_clears_flag(self, crash_recovery_env):
        # Wiki marked dirty but its DB no longer exists. Lifespan should clear
        # the flag rather than crash on the rebuild attempt.
        jobs = crash_recovery_env["jobs"]
        conn = jobs.connect_jobs(crash_recovery_env["jobs_db"])
        try:
            jobs.set_fts_dirty(conn, "simplewiki", True)
        finally:
            conn.close()

        with TestClient(web_app.app):
            pass

        conn = jobs.connect_jobs(crash_recovery_env["jobs_db"])
        try:
            assert "simplewiki" not in jobs.get_fts_dirty_wikis(conn)
        finally:
            conn.close()

    def test_terminal_jobs_are_left_alone(self, crash_recovery_env):
        jobs = crash_recovery_env["jobs"]
        conn = jobs.connect_jobs(crash_recovery_env["jobs_db"])
        try:
            conn.execute(
                "INSERT INTO refresh_jobs (wiki, status) VALUES (?, ?)",
                ("enwiki", "complete"),
            )
            conn.execute(
                "INSERT INTO refresh_jobs (wiki, status, error_message) VALUES (?, ?, ?)",
                ("enwiki", "failed", "earlier failure"),
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(web_app.app):
            pass

        conn = jobs.connect_jobs(crash_recovery_env["jobs_db"])
        try:
            rows = conn.execute("SELECT status, error_message FROM refresh_jobs ORDER BY id").fetchall()
        finally:
            conn.close()
        assert rows[0]["status"] == "complete"
        assert rows[1]["status"] == "failed"
        assert rows[1]["error_message"] == "earlier failure"


class TestThemeToggle:
    def test_theme_toggle_button_exists(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "data-theme-toggle" in resp.text


class TestRefreshPage:
    def test_refresh_page_renders(self, client):
        resp = client.get("/refresh")
        assert resp.status_code == 200
        assert "Refresh" in resp.text
        assert "simplewiki" in resp.text
        assert "enwiki" in resp.text

    def test_refresh_page_has_sidebar(self, client):
        resp = client.get("/refresh")
        assert "app-sidebar" in resp.text


class TestArticleFullPage:
    def test_article_renders_full_shell(self, client):
        resp = client.get("/article/Apple")
        assert resp.status_code == 200
        assert "app-sidebar" in resp.text
        assert "article-body" in resp.text

    def test_old_home_article_param_redirects(self, client):
        resp = client.get("/?article=Apple", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/article/Apple"

    def test_article_not_found_returns_404(self, client):
        resp = client.get("/article/DoesNotExist")
        assert resp.status_code == 404


class TestSwitchWiki:
    def test_switch_with_article_redirects_to_article(self, client):
        resp = client.get("/switch-wiki?to=simplewiki&article=Apple", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/article/Apple"
        assert "wiki_pref=simplewiki" in resp.headers["set-cookie"]

    def test_switch_without_article_uses_referer_article(self, client):
        # Even when /switch-wiki is called without &article=, the Referer
        # header should be inspected so wiki-chip clicks on article pages
        # stay on the article instead of redirecting to home.
        resp = client.get(
            "/switch-wiki?to=simplewiki",
            headers={"referer": "http://testserver/article/Apple"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/article/Apple"

    def test_switch_without_article_uses_referer_wikitext(self, client):
        resp = client.get(
            "/switch-wiki?to=simplewiki",
            headers={"referer": "http://testserver/wikitext/Apple"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/wikitext/Apple"

    def test_switch_with_return_to_redirects_there(self, client):
        resp = client.get(
            "/switch-wiki?to=simplewiki&return_to=/refresh",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/refresh"

    def test_switch_falls_back_to_home(self, client):
        resp = client.get("/switch-wiki?to=simplewiki", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"

    def test_switch_rejects_external_return_to(self, client):
        # Protocol-relative URL must not be honoured as a redirect target.
        resp = client.get(
            "/switch-wiki?to=simplewiki&return_to=//evil.example.com",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"

    def test_switch_rejects_unknown_wiki(self, client):
        resp = client.get("/switch-wiki?to=fakewiki&article=Apple", follow_redirects=False)
        assert resp.status_code == 400
