"""Tests for ``workers/embed.py``'s per-item processing and 2-hop expansion.

The 2-hop "Embed + links²" trigger relies on the worker to discover the
second hop dynamically: when it finishes embedding an item whose
``hops_remaining > 0``, it must extract that article's wikilinks and append
them to the same job at ``hops_remaining - 1``. These tests exercise that
behaviour directly against ``_process_item`` / ``_expand_links`` so the full
subprocess + log-redirect harness is out of the way.
"""

import sqlite3
from pathlib import Path

import pytest

import db as wiki_db
from jobs import embed as embed_jobs
from rag.schema import connect_rag
from workers import embed as embed_worker

# --- Fixture builders --------------------------------------------------------


def _build_wiki_db(path: Path, articles: list[tuple[int, str, str]]) -> None:
    """Create a minimal articles + FTS DB. ``articles`` = [(page_id, title, wikitext)]."""
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
        "INSERT INTO articles (page_id, title, namespace, revision_id, timestamp, "
        "text_bytes, text_content) VALUES (?, ?, 0, 1, '2026-01-01T00:00:00Z', ?, ?)",
        [(pid, t, len(w), w) for pid, t, w in articles],
    )
    conn.commit()
    conn.close()


@pytest.fixture
def chain_db(tmp_path):
    """A wiki DB shaped like A → B → C (each article links to the next)."""
    wiki_path = tmp_path / "chain.db"
    _build_wiki_db(
        wiki_path,
        [
            (1, "A", "Some intro. See also [[B]] for details."),
            (2, "B", "B references [[C]] in passing."),
            (3, "C", "C has no outgoing links of interest."),
        ],
    )
    return wiki_path


@pytest.fixture
def jobs_conn(tmp_path):
    conn = embed_jobs.connect_embed_jobs(tmp_path / "jobs.db")
    yield conn
    conn.close()


@pytest.fixture
def rag_conn(tmp_path):
    conn = connect_rag(tmp_path / "rag.db")
    yield conn
    conn.close()


@pytest.fixture
def stub_embed_one(monkeypatch):
    """Replace ``embed_one`` with a stub that records the page_id in articles_meta."""
    calls: list[int] = []

    def fake_embed_one(rag_conn, page_id, title, revision_id, wikitext, *, wiki_conn=None):
        calls.append(page_id)
        rag_conn.execute(
            "INSERT OR REPLACE INTO articles_meta "
            "(page_id, title, revision_id, links_embedded) "
            "VALUES (?, ?, ?, 0)",
            (page_id, title, revision_id),
        )
        rag_conn.commit()
        return 1  # one chunk

    monkeypatch.setattr(embed_worker, "embed_one", fake_embed_one)
    return calls


# --- Helpers -----------------------------------------------------------------


def _enqueue(jobs_conn, job_id, title, source_title, hops):
    """Insert one item and return its row (with the auto-assigned id)."""
    embed_jobs.append_items(jobs_conn, job_id, [(title, source_title, hops)])
    return jobs_conn.execute(
        "SELECT * FROM embed_job_items WHERE job_id = ? AND title = ?",
        (job_id, title),
    ).fetchone()


# --- Tests -------------------------------------------------------------------


class TestExpandLinks:
    """``_expand_links`` enqueues children with the right depth and source_title."""

    def test_no_expansion_at_zero_hops(self, chain_db, jobs_conn, stub_embed_one):
        job_id = embed_jobs.create_job(jobs_conn, "enwiki", "/tmp/x.log", "A")
        item = _enqueue(jobs_conn, job_id, "B", "A", hops=0)

        with wiki_db.connect(chain_db) as wconn:
            embed_worker._expand_links(item, "B", "B references [[C]]", jobs_conn, wconn)

        titles = [r["title"] for r in embed_jobs.get_items(jobs_conn, job_id)]
        assert titles == ["B"]  # no C added

    def test_expansion_decrements_hops_and_uses_parent_as_source(self, chain_db, jobs_conn, stub_embed_one):
        job_id = embed_jobs.create_job(jobs_conn, "enwiki", "/tmp/x.log", "A")
        item = _enqueue(jobs_conn, job_id, "B", "A", hops=1)

        with wiki_db.connect(chain_db) as wconn:
            embed_worker._expand_links(item, "B", "B references [[C]]", jobs_conn, wconn)

        rows = {r["title"]: r for r in embed_jobs.get_items(jobs_conn, job_id)}
        assert "C" in rows
        assert rows["C"]["hops_remaining"] == 0
        # The child's source_title should point at its discoverer (B), not the
        # original triggering article (A) — that way the end-of-job finalizer
        # marks B as links_embedded too.
        assert rows["C"]["source_title"] == "B"

    def test_self_links_are_skipped(self, chain_db, jobs_conn, stub_embed_one):
        job_id = embed_jobs.create_job(jobs_conn, "enwiki", "/tmp/x.log", "A")
        item = _enqueue(jobs_conn, job_id, "B", "A", hops=1)

        with wiki_db.connect(chain_db) as wconn:
            # Wikitext contains a self-link to [[B]] alongside [[C]].
            embed_worker._expand_links(item, "B", "[[B]] and [[C]]", jobs_conn, wconn)

        titles = {r["title"] for r in embed_jobs.get_items(jobs_conn, job_id)}
        assert titles == {"B", "C"}


class TestProcessItem:
    """``_process_item`` drives embedding + status update + optional expansion."""

    def test_complete_with_hops_triggers_expansion(self, chain_db, jobs_conn, rag_conn, stub_embed_one):
        job_id = embed_jobs.create_job(jobs_conn, "enwiki", "/tmp/x.log", "A")
        b_item = _enqueue(jobs_conn, job_id, "B", "A", hops=1)

        with wiki_db.connect(chain_db) as wconn:
            embed_worker._process_item(b_item, jobs_conn, wconn, rag_conn, {})

        rows = {r["title"]: r for r in embed_jobs.get_items(jobs_conn, job_id)}
        # B was embedded.
        assert rows["B"]["status"] == "complete"
        # C was discovered and queued at hops=0.
        assert "C" in rows
        assert rows["C"]["status"] == "queued"
        assert rows["C"]["hops_remaining"] == 0

    def test_skipped_unchanged_still_expands(self, chain_db, jobs_conn, rag_conn, stub_embed_one):
        # Pre-populate articles_meta so B is considered unchanged.
        rag_conn.execute("INSERT INTO articles_meta (page_id, title, revision_id) VALUES (2, 'B', 1)")
        rag_conn.commit()

        job_id = embed_jobs.create_job(jobs_conn, "enwiki", "/tmp/x.log", "A")
        b_item = _enqueue(jobs_conn, job_id, "B", "A", hops=1)

        with wiki_db.connect(chain_db) as wconn:
            embed_worker._process_item(b_item, jobs_conn, wconn, rag_conn, {2: 1})

        rows = {r["title"]: r for r in embed_jobs.get_items(jobs_conn, job_id)}
        # B was skipped (revision matched) — embed_one should NOT have been called.
        assert rows["B"]["status"] == "skipped_unchanged"
        assert stub_embed_one == []
        # But expansion must still happen so 2-hop reaches C.
        assert "C" in rows
        assert rows["C"]["hops_remaining"] == 0

    def test_skipped_redirect_does_not_expand(self, tmp_path, jobs_conn, rag_conn, stub_embed_one):
        wiki_path = tmp_path / "redirects.db"
        _build_wiki_db(
            wiki_path,
            [
                (1, "OldName", "#REDIRECT [[Target]]"),
                # ``Target`` exists so resolve_redirect could resolve it, but the
                # worker never looks at the redirect's body for link extraction.
                (2, "Target", "Hello [[World]]"),
            ],
        )
        job_id = embed_jobs.create_job(jobs_conn, "enwiki", "/tmp/x.log", "OldName")
        item = _enqueue(jobs_conn, job_id, "OldName", "OldName", hops=2)

        with wiki_db.connect(wiki_path) as wconn:
            embed_worker._process_item(item, jobs_conn, wconn, rag_conn, {})

        titles = {r["title"] for r in embed_jobs.get_items(jobs_conn, job_id)}
        # Redirect body's [[Target]] wikilink must NOT enqueue Target via
        # expansion — that's the route's job, not the redirect's.
        assert titles == {"OldName"}

    def test_expansion_failure_does_not_void_embed(self, chain_db, jobs_conn, rag_conn, stub_embed_one, monkeypatch):
        # Force the extraction helper to blow up; the item should still mark complete.
        def boom(*args, **kwargs):
            raise RuntimeError("simulated parse failure")

        monkeypatch.setattr(embed_worker, "extract_article_links", boom)

        job_id = embed_jobs.create_job(jobs_conn, "enwiki", "/tmp/x.log", "A")
        b_item = _enqueue(jobs_conn, job_id, "B", "A", hops=1)

        with wiki_db.connect(chain_db) as wconn:
            embed_worker._process_item(b_item, jobs_conn, wconn, rag_conn, {})

        rows = embed_jobs.get_items(jobs_conn, job_id)
        statuses = {r["title"]: r["status"] for r in rows}
        assert statuses["B"] == "complete"


class TestFinalizeLinksEmbedded:
    """``_finalize_links_embedded`` sets the per-article flags at job complete."""

    def _seed_meta(self, rag_conn, titles: list[str]) -> None:
        for i, t in enumerate(titles, start=1):
            rag_conn.execute(
                "INSERT INTO articles_meta (page_id, title, revision_id) VALUES (?, ?, 1)",
                (i, t),
            )
        rag_conn.commit()

    def _empty_wiki_db(self, tmp_path):
        """A wiki DB with no rows — finalize will skip its count-refresh loop."""
        wiki_path = tmp_path / "empty.db"
        _build_wiki_db(wiki_path, [])
        return wiki_path

    def test_one_hop_job_marks_links_embedded_only(self, tmp_path, jobs_conn, rag_conn):
        # 1-hop trigger on A: every item has hops_remaining=0.
        self._seed_meta(rag_conn, ["A", "B", "C"])
        job_id = embed_jobs.create_job(jobs_conn, "enwiki", "/tmp/x.log", "A")
        embed_jobs.append_items(jobs_conn, job_id, [("A", "A", 0), ("B", "A", 0), ("C", "A", 0)])
        # Mark all items terminal so the finalizer picks them up.
        jobs_conn.execute("UPDATE embed_job_items SET status = 'complete' WHERE job_id = ?", (job_id,))
        jobs_conn.commit()

        wiki_path = self._empty_wiki_db(tmp_path)
        with wiki_db.connect(wiki_path) as wconn:
            embed_worker._finalize_links_embedded(jobs_conn, rag_conn, wconn, job_id)

        row = rag_conn.execute(
            "SELECT links_embedded, links_embedded_2hop FROM articles_meta WHERE title = 'A'"
        ).fetchone()
        assert row["links_embedded"] == 1
        assert row["links_embedded_2hop"] == 0

    def test_two_hop_job_marks_both_flags_on_trigger(self, tmp_path, jobs_conn, rag_conn):
        # 2-hop trigger on A: (A, A, 0), (B, A, 1), (C, A, 1). Worker later
        # adds (D, B, 0) and (E, C, 0) as depth-1 children.
        self._seed_meta(rag_conn, ["A", "B", "C", "D", "E"])
        job_id = embed_jobs.create_job(jobs_conn, "enwiki", "/tmp/x.log", "A")
        embed_jobs.append_items(
            jobs_conn,
            job_id,
            [("A", "A", 0), ("B", "A", 1), ("C", "A", 1), ("D", "B", 0), ("E", "C", 0)],
        )
        jobs_conn.execute("UPDATE embed_job_items SET status = 'complete' WHERE job_id = ?", (job_id,))
        jobs_conn.commit()

        wiki_path = self._empty_wiki_db(tmp_path)
        with wiki_db.connect(wiki_path) as wconn:
            embed_worker._finalize_links_embedded(jobs_conn, rag_conn, wconn, job_id)

        rows = {
            r["title"]: r
            for r in rag_conn.execute(
                "SELECT title, links_embedded, links_embedded_2hop FROM articles_meta"
            ).fetchall()
        }
        # 1-hop flag set on every source_title (the trigger plus each expanded parent).
        assert rows["A"]["links_embedded"] == 1
        assert rows["B"]["links_embedded"] == 1
        assert rows["C"]["links_embedded"] == 1
        # 2-hop flag set only on A — only A has an item enqueued at hops_remaining=1.
        assert rows["A"]["links_embedded_2hop"] == 1
        assert rows["B"]["links_embedded_2hop"] == 0
        assert rows["C"]["links_embedded_2hop"] == 0
        # Leaf depth-2 articles weren't sources of anything.
        assert rows["D"]["links_embedded"] == 0
        assert rows["E"]["links_embedded"] == 0

    def test_skips_unfinished_items(self, tmp_path, jobs_conn, rag_conn):
        # An item still queued or in_progress must not contribute to the flags.
        self._seed_meta(rag_conn, ["A", "B"])
        job_id = embed_jobs.create_job(jobs_conn, "enwiki", "/tmp/x.log", "A")
        embed_jobs.append_items(jobs_conn, job_id, [("A", "A", 0), ("B", "A", 1)])
        # Leave (B, A, 1) in queued state.
        jobs_conn.execute(
            "UPDATE embed_job_items SET status = 'complete' WHERE job_id = ? AND title = 'A'",
            (job_id,),
        )
        jobs_conn.commit()

        wiki_path = self._empty_wiki_db(tmp_path)
        with wiki_db.connect(wiki_path) as wconn:
            embed_worker._finalize_links_embedded(jobs_conn, rag_conn, wconn, job_id)

        row = rag_conn.execute(
            "SELECT links_embedded, links_embedded_2hop FROM articles_meta WHERE title = 'A'"
        ).fetchone()
        # A is itself terminal so its 1-hop flag is set, but its only
        # hops_remaining=1 item is still queued — 2-hop flag stays 0.
        assert row["links_embedded"] == 1
        assert row["links_embedded_2hop"] == 0

    def test_finalize_populates_link_counts(self, tmp_path, jobs_conn, rag_conn):
        # Wiki has A→[[B]], B→[[C]], C as leaf. All three are seeded in
        # articles_meta so every link target counts as "already embedded".
        wiki_path = tmp_path / "chain.db"
        _build_wiki_db(
            wiki_path,
            [
                (1, "A", "A talks about [[B]]."),
                (2, "B", "B talks about [[C]]."),
                (3, "C", "Leaf."),
            ],
        )
        self._seed_meta(rag_conn, ["A", "B", "C"])

        # 2-hop trigger on A; B becomes a 1-hop source via expansion.
        job_id = embed_jobs.create_job(jobs_conn, "enwiki", "/tmp/x.log", "A")
        embed_jobs.append_items(
            jobs_conn,
            job_id,
            [("A", "A", 1), ("B", "A", 0), ("C", "B", 0)],
        )
        jobs_conn.execute("UPDATE embed_job_items SET status='complete' WHERE job_id=?", (job_id,))
        jobs_conn.commit()

        with wiki_db.connect(wiki_path) as wconn:
            embed_worker._finalize_links_embedded(jobs_conn, rag_conn, wconn, job_id)

        rows = {
            r["title"]: r
            for r in rag_conn.execute(
                "SELECT title, unembedded_link_count_1hop, unembedded_link_count_2hop "
                "FROM articles_meta"
            ).fetchall()
        }
        # A's only outbound link is B, which is embedded → 1-hop count = 0.
        assert rows["A"]["unembedded_link_count_1hop"] == 0
        # A had a hops_remaining=1 item → 2-hop count is also computed (also 0).
        assert rows["A"]["unembedded_link_count_2hop"] == 0
        # B is a source of hops=0 items only → 1-hop refreshed, 2-hop stays NULL.
        assert rows["B"]["unembedded_link_count_1hop"] == 0
        assert rows["B"]["unembedded_link_count_2hop"] is None
        # C never appears as a source_title → both stay NULL.
        assert rows["C"]["unembedded_link_count_1hop"] is None
        assert rows["C"]["unembedded_link_count_2hop"] is None
