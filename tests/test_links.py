"""Tests for the wikilink extractor used by the embed-links feature."""
from rag.links import extract_article_links


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
        wt = (
            "{{Infobox\n"
            "| field1 = [[Linked from infobox]]\n"
            "| field2 = plain text\n"
            "}}\n"
            "Body links to [[Body link]]."
        )
        result = extract_article_links(wt)
        assert "Linked from infobox" in result
        assert "Body link" in result

    def test_links_inside_tables_are_found(self):
        wt = (
            "{| class=\"wikitable\"\n"
            "|-\n"
            "| [[Cell link]] || plain\n"
            "|}\n"
        )
        assert extract_article_links(wt) == ["Cell link"]

    def test_empty_and_whitespace_returns_empty(self):
        assert extract_article_links("") == []
        assert extract_article_links("   \n\n  ") == []

    def test_redirect_stub_still_extracts_target(self):
        # A redirect stub has a single wikilink; the extractor doesn't try to
        # interpret the #REDIRECT semantics — that's the caller's job.
        assert extract_article_links("#REDIRECT [[Statistics]]") == ["Statistics"]
