"""Tests for the wikilink extractor used by the embed-links feature."""

import sqlite3
from pathlib import Path

import pytest

import db as wiki_db
from rag.links import count_unembedded_1hop, count_unembedded_2hop, extract_article_links
from rag.schema import connect_rag


class TestExtractArticleLinks:
    def test_simple_links(self):
        wt = "An '''apple''' is a [[fruit]] from the [[plant kingdom]]."
        assert extract_article_links(wt) == ["Fruit", "Plant kingdom"]

    def test_pipe_label(self):
        # [[Page|Label]] should yield Page, not Label.
        wt = "See [[Tree (data structure)|trees]] for details."
        assert extract_article_links(wt) == ["Tree (data structure)"]

    def test_anchor_stripped(self):
        wt = "Background is in [[Python (programming language)#History]]."
        assert extract_article_links(wt) == ["Python (programming language)"]

    def test_first_letter_capitalised(self):
        wt = "Lowercase [[python]] should become Python."
        assert extract_article_links(wt) == ["Python"]

    def test_dedupes_preserving_order(self):
        wt = "First [[Apple]]. Second [[apple]]. Third [[banana]]. Fourth [[Apple]]."
        # The lowercase apple normalises to Apple and dedupes.
        assert extract_article_links(wt) == ["Apple", "Banana"]

    def test_namespace_filter(self):
        wt = (
            "Body text mentions [[Statistics]] and [[Category:Math]] "
            "and [[File:Chart.png]] and [[Image:Foo.jpg]] and [[Help:Linking]] "
            "and [[User:Alice]] and [[Talk:Statistics]] and [[Template:Cite]] "
            "and [[Wikipedia:About]] and [[Portal:Math]]."
        )
        assert extract_article_links(wt) == ["Statistics"]

    def test_interwiki_filter(self):
        wt = "See [[:de:Statistik]] for the German version, plus [[Statistics]]."
        assert extract_article_links(wt) == ["Statistics"]

    def test_self_link_filtered(self):
        wt = "About [[Statistics]] and itself: [[Descriptive statistics]]."
        out = extract_article_links(wt, source_title="Descriptive statistics")
        assert out == ["Statistics"]

    def test_links_inside_templates_are_found(self):
        # mwparserfromhell.filter_wikilinks() descends into templates by
        # default, so infobox/cite/whatever params yield their links too.
        wt = "{{Infobox\n| field1 = [[Linked from infobox]]\n| field2 = plain text\n}}\nBody links to [[Body link]]."
        result = extract_article_links(wt)
        assert "Linked from infobox" in result
        assert "Body link" in result

    def test_links_inside_tables_are_found(self):
        wt = '{| class="wikitable"\n|-\n| [[Cell link]] || plain\n|}\n'
        assert extract_article_links(wt) == ["Cell link"]

    def test_empty_and_whitespace_returns_empty(self):
        assert extract_article_links("") == []
        assert extract_article_links("   \n\n  ") == []

    def test_redirect_stub_still_extracts_target(self):
        # A redirect stub has a single wikilink; the extractor doesn't try to
        # interpret the #REDIRECT semantics — that's the caller's job.
        assert extract_article_links("#REDIRECT [[Statistics]]") == ["Statistics"]


# --- Count helpers -----------------------------------------------------------


def _build_wiki_db(path: Path, articles: list[tuple[int, str, str]]) -> None:
    """Create a minimal articles table. ``articles`` = [(page_id, title, wikitext)]."""
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


def _mark_embedded(rag_conn: sqlite3.Connection, *titles: str) -> None:
    """Populate articles_meta with rows for the given titles."""
    for i, title in enumerate(titles, start=1000):
        rag_conn.execute(
            "INSERT INTO articles_meta (page_id, title, revision_id) VALUES (?, ?, 1)",
            (i, title),
        )
    rag_conn.commit()


@pytest.fixture
def chain_wiki(tmp_path):
    """A→B,C; B→D,E; C→E,F (E is reachable through both B and C — dedup case).

    Also includes a redirect Bredirect→B so we can verify redirect collapsing.
    """
    db = tmp_path / "wiki.db"
    _build_wiki_db(
        db,
        [
            (1, "A", "Article A links to [[B]] and [[C]]."),
            (2, "B", "B links to [[D]] and [[E]]."),
            (3, "C", "C links to [[E]] and [[F]]."),
            (4, "D", "D has no internal links."),
            (5, "E", "E has no internal links."),
            (6, "F", "F has no internal links."),
            (7, "Bredirect", "#REDIRECT [[B]]"),
        ],
    )
    return db


@pytest.fixture
def rag_db(tmp_path):
    db = connect_rag(tmp_path / "rag.db")
    yield db
    db.close()


class TestCountUnembedded1Hop:
    def test_all_unembedded(self, chain_wiki, rag_db):
        with wiki_db.connect(chain_wiki) as wconn:
            n = count_unembedded_1hop(wconn, rag_db, "A", "Links to [[B]] and [[C]].")
        assert n == 2

    def test_partial_embedded(self, chain_wiki, rag_db):
        _mark_embedded(rag_db, "B")
        with wiki_db.connect(chain_wiki) as wconn:
            n = count_unembedded_1hop(wconn, rag_db, "A", "Links to [[B]] and [[C]].")
        assert n == 1

    def test_all_embedded(self, chain_wiki, rag_db):
        _mark_embedded(rag_db, "B", "C")
        with wiki_db.connect(chain_wiki) as wconn:
            n = count_unembedded_1hop(wconn, rag_db, "A", "Links to [[B]] and [[C]].")
        assert n == 0

    def test_no_links(self, chain_wiki, rag_db):
        with wiki_db.connect(chain_wiki) as wconn:
            n = count_unembedded_1hop(wconn, rag_db, "A", "Article body with no links at all.")
        assert n == 0

    def test_rag_conn_none_counts_all(self, chain_wiki):
        with wiki_db.connect(chain_wiki) as wconn:
            n = count_unembedded_1hop(wconn, None, "A", "Links to [[B]] and [[C]].")
        assert n == 2

    def test_redirect_collapses_to_canonical(self, chain_wiki, rag_db):
        # Both [[B]] and [[Bredirect]] point to the same canonical B.
        with wiki_db.connect(chain_wiki) as wconn:
            n = count_unembedded_1hop(wconn, rag_db, "A", "Links to [[B]] and [[Bredirect]].")
        assert n == 1  # B counted once, not twice

    def test_source_title_excluded(self, chain_wiki, rag_db):
        # A self-link via [[A]] must not contribute to the count.
        with wiki_db.connect(chain_wiki) as wconn:
            n = count_unembedded_1hop(wconn, rag_db, "A", "Self-link [[A]] and [[B]].")
        assert n == 1

    def test_excludes_not_found_target(self, chain_wiki, rag_db):
        # A link to a non-existent article would be marked ``not_found`` by
        # the worker — it must not contribute to the count.
        with wiki_db.connect(chain_wiki) as wconn:
            n = count_unembedded_1hop(wconn, rag_db, "A", "Links to [[B]] and [[NoSuchArticle]].")
        assert n == 1  # only B counts

    def test_excludes_redirect_target_not_resolving_to_canonical(self, tmp_path, rag_db):
        # A redirect whose target chain ends in a non-existent article — the
        # resolve_redirect fallback used to leave the dangling title in the
        # candidate set; with skip-filtering it now drops out.
        db = tmp_path / "broken.db"
        _build_wiki_db(
            db,
            [
                (1, "A", "Links to [[B]] and [[Broken]]."),
                (2, "B", "Leaf."),
                (3, "Broken", "#REDIRECT [[NoSuchArticle]]"),
            ],
        )
        with wiki_db.connect(db) as wconn:
            n = count_unembedded_1hop(wconn, rag_db, "A", "Links to [[B]] and [[Broken]].")
        # B is embeddable; Broken resolves to a non-existent target so the
        # _resolve_and_dedup fallback keeps "NoSuchArticle" as a candidate,
        # which _filter_embeddable then drops.
        assert n == 1


class TestCountUnembedded2Hop:
    def test_union_of_hops_deduplicated(self, chain_wiki, rag_db):
        # 1-hop set = {B, C}; 2-hop expansion from B = {D, E}; from C = {E, F}.
        # Union = {B, C, D, E, F}. E is reachable via both B and C but counted once.
        with wiki_db.connect(chain_wiki) as wconn:
            n = count_unembedded_2hop(wconn, rag_db, "A", "Links to [[B]] and [[C]].")
        assert n == 5

    def test_partial_embedded(self, chain_wiki, rag_db):
        _mark_embedded(rag_db, "D", "E")
        with wiki_db.connect(chain_wiki) as wconn:
            n = count_unembedded_2hop(wconn, rag_db, "A", "Links to [[B]] and [[C]].")
        # Union minus embedded = {B, C, F}.
        assert n == 3

    def test_source_excluded_across_hops(self, tmp_path, rag_db):
        # Build a wiki where the 2-hop set would re-include the source.
        db = tmp_path / "cycle.db"
        _build_wiki_db(
            db,
            [
                (1, "A", "Links to [[B]]."),
                (2, "B", "Links back to [[A]] and to [[C]]."),
                (3, "C", "Has nothing."),
            ],
        )
        with wiki_db.connect(db) as wconn:
            n = count_unembedded_2hop(wconn, rag_db, "A", "Links to [[B]].")
        # 1-hop = {B}; B expands to {A (excluded), C}. Union minus source = {B, C}.
        assert n == 2

    def test_redirect_in_1hop_not_expanded(self, tmp_path, rag_db):
        # If a 1-hop neighbour is itself a redirect article, we should NOT use
        # its body for link extraction — _resolve_and_dedup already collapses
        # the redirect to its target, so the redirect body never enters the
        # expansion loop.
        db = tmp_path / "rd.db"
        _build_wiki_db(
            db,
            [
                (1, "A", "Links to [[Bredirect]]."),
                (2, "Bredirect", "#REDIRECT [[B]]"),
                (3, "B", "B links to [[C]]."),
                (4, "C", "Leaf."),
            ],
        )
        with wiki_db.connect(db) as wconn:
            n = count_unembedded_2hop(wconn, rag_db, "A", "Links to [[Bredirect]].")
        # 1-hop resolves to {B}; B expands to {C}. Total {B, C} = 2.
        assert n == 2

    def test_rag_conn_none_counts_all(self, chain_wiki):
        with wiki_db.connect(chain_wiki) as wconn:
            n = count_unembedded_2hop(wconn, None, "A", "Links to [[B]] and [[C]].")
        assert n == 5

    def test_no_links(self, chain_wiki, rag_db):
        with wiki_db.connect(chain_wiki) as wconn:
            n = count_unembedded_2hop(wconn, rag_db, "A", "No links here.")
        assert n == 0

    def test_excludes_not_found_1hop_target(self, tmp_path, rag_db):
        # A 1-hop link that points at a non-existent article would be marked
        # ``not_found`` by the worker; it must not contribute to the count.
        db = tmp_path / "missing.db"
        _build_wiki_db(
            db,
            [
                (1, "A", "Links to [[B]] and [[NoSuchArticle]]."),
                (2, "B", "B links to [[C]]."),
                (3, "C", "Leaf."),
            ],
        )
        with wiki_db.connect(db) as wconn:
            n = count_unembedded_2hop(wconn, rag_db, "A", "Links to [[B]] and [[NoSuchArticle]].")
        # Without filtering this would be 3 (B, NoSuchArticle, C). With
        # filtering: NoSuchArticle drops out, leaving {B, C} = 2.
        assert n == 2

    def test_excludes_not_found_2hop_target(self, tmp_path, rag_db):
        # B's body links at a non-existent article — the worker would mark
        # that hop2 target as ``not_found`` and never embed it.
        db = tmp_path / "missing2.db"
        _build_wiki_db(
            db,
            [
                (1, "A", "Links to [[B]]."),
                (2, "B", "B links to [[C]] and [[NoSuchArticle]]."),
                (3, "C", "Leaf."),
            ],
        )
        with wiki_db.connect(db) as wconn:
            n = count_unembedded_2hop(wconn, rag_db, "A", "Links to [[B]].")
        # Without filtering this would be 3 (B, C, NoSuchArticle). With
        # filtering: NoSuchArticle drops out, leaving {B, C} = 2.
        assert n == 2

    def test_excludes_redirect_2hop_target(self, tmp_path, rag_db):
        # A hop2 link target whose body is a #REDIRECT must be excluded — the
        # worker either resolves it to its canonical target (counted via its
        # direct link, if any) or marks it ``skipped_redirect``.
        db = tmp_path / "redirect2.db"
        _build_wiki_db(
            db,
            [
                (1, "A", "Links to [[B]]."),
                (2, "B", "B links to [[C]] and [[Cdup]]."),
                (3, "C", "Leaf."),
                (4, "Cdup", "#REDIRECT [[C]]"),
            ],
        )
        with wiki_db.connect(db) as wconn:
            n = count_unembedded_2hop(wconn, rag_db, "A", "Links to [[B]].")
        # Without filtering this would be 3 (B, C, Cdup). With filtering:
        # Cdup is a redirect → drops out, leaving {B, C} = 2.
        assert n == 2
