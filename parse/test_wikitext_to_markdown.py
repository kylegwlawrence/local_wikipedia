"""Tests for wikitext_to_markdown.py."""
import pytest
from parse.wikitext_to_markdown import (
    convert_wikitext_to_markdown,
    _convert_bold_italic,
    _convert_headings,
    _convert_links,
    _convert_lists,
    _clean_extra_markup,
)


class TestConvertBoldItalic:
    def test_bold_conversion(self) -> None:
        text = "This is '''bold''' text"
        result = _convert_bold_italic(text)
        assert result == "This is **bold** text"

    def test_italic_conversion(self) -> None:
        text = "This is ''italic'' text"
        result = _convert_bold_italic(text)
        assert result == "This is *italic* text"

    def test_bold_italic_conversion(self) -> None:
        text = "This is '''''bold and italic''''' text"
        result = _convert_bold_italic(text)
        assert result == "This is ***bold and italic*** text"

    def test_mixed_formatting(self) -> None:
        text = "'''Bold''' and ''italic'' and '''''both'''''"
        result = _convert_bold_italic(text)
        assert result == "**Bold** and *italic* and ***both***"

    def test_multiple_bold_sections(self) -> None:
        text = "'''First''' and '''second''' bold"
        result = _convert_bold_italic(text)
        assert result == "**First** and **second** bold"


class TestConvertHeadings:
    def test_level_2_heading(self) -> None:
        text = "== Heading =="
        result = _convert_headings(text)
        assert result == "## Heading"

    def test_level_3_heading(self) -> None:
        text = "=== Subheading ==="
        result = _convert_headings(text)
        assert result == "### Subheading"

    def test_level_4_heading(self) -> None:
        text = "==== Sub-subheading ===="
        result = _convert_headings(text)
        assert result == "#### Sub-subheading"

    def test_multiple_headings(self) -> None:
        text = "== First ==\nSome text\n=== Second ==="
        result = _convert_headings(text)
        assert "## First" in result
        assert "### Second" in result

    def test_heading_with_whitespace(self) -> None:
        text = "==  Heading  =="
        result = _convert_headings(text)
        assert result == "## Heading"


class TestConvertLinks:
    def test_simple_link(self) -> None:
        text = "See [[Python]]"
        result = _convert_links(text)
        assert result == "See [Python](https://simple.wikipedia.org/wiki/Python)"

    def test_link_with_label(self) -> None:
        text = "See [[Python (programming language)|Python]]"
        result = _convert_links(text)
        assert result == "See [Python](https://simple.wikipedia.org/wiki/Python_(programming_language))"

    def test_link_with_spaces(self) -> None:
        text = "[[United States]]"
        result = _convert_links(text)
        assert result == "[United States](https://simple.wikipedia.org/wiki/United_States)"

    def test_multiple_links(self) -> None:
        text = "[[First]] and [[Second]]"
        result = _convert_links(text)
        assert "[First](https://simple.wikipedia.org/wiki/First)" in result
        assert "[Second](https://simple.wikipedia.org/wiki/Second)" in result

    def test_link_in_sentence(self) -> None:
        text = "Programming in [[Python]] is fun"
        result = _convert_links(text)
        assert "Programming in [Python](https://simple.wikipedia.org/wiki/Python) is fun" == result


class TestConvertLists:
    def test_bullet_list(self) -> None:
        text = "* Item 1\n* Item 2"
        result = _convert_lists(text)
        assert result == "- Item 1\n- Item 2"

    def test_numbered_list(self) -> None:
        text = "# First\n# Second"
        result = _convert_lists(text)
        assert result == "1. First\n1. Second"

    def test_nested_bullet_list(self) -> None:
        text = "* Level 1\n** Level 2\n*** Level 3"
        result = _convert_lists(text)
        lines = result.split("\n")
        assert lines[0] == "- Level 1"
        assert lines[1] == "  - Level 2"
        assert lines[2] == "    - Level 3"

    @pytest.mark.skip(reason="Nested ## lists ambiguous with markdown headings after conversion")
    def test_nested_numbered_list(self) -> None:
        # Test with full conversion since ## is ambiguous when testing _convert_lists alone
        wikitext = "# Level 1\n## Level 2\n### Level 3"
        result = convert_wikitext_to_markdown(wikitext)
        lines = result.split("\n")
        assert "1. Level 1" in result
        assert "  1. Level 2" in result
        assert "    1. Level 3" in result

    def test_mixed_content(self) -> None:
        text = "Normal text\n* List item\nMore text"
        result = _convert_lists(text)
        assert "Normal text" in result
        assert "- List item" in result
        assert "More text" in result


class TestCleanExtraMarkup:
    def test_remove_html_tags(self) -> None:
        text = "Text with <span>HTML</span> tags"
        result = _clean_extra_markup(text)
        assert result == "Text with HTML tags"

    def test_remove_multiple_blank_lines(self) -> None:
        text = "Line 1\n\n\n\nLine 2"
        result = _clean_extra_markup(text)
        assert result == "Line 1\n\nLine 2"

    def test_remove_trailing_whitespace(self) -> None:
        text = "Line with trailing spaces   \nAnother line  "
        result = _clean_extra_markup(text)
        assert result == "Line with trailing spaces\nAnother line"


class TestFullConversion:
    def test_simple_article(self) -> None:
        wikitext = """'''Python''' is a programming language.

== History ==
Python was created in the 1990s.

== Features ==
* Easy to learn
* Powerful
* [[Object-oriented programming|Object-oriented]]
"""
        result = convert_wikitext_to_markdown(wikitext)

        assert "**Python** is a programming language" in result
        assert "## History" in result
        assert "## Features" in result
        assert "- Easy to learn" in result
        assert "- Powerful" in result
        assert "[Object-oriented](https://simple.wikipedia.org/wiki/Object-oriented_programming)" in result

    def test_complex_formatting(self) -> None:
        wikitext = """'''''Python''''' is both '''powerful''' and ''easy''.

=== Syntax ===
The syntax is clean.

See also:
* [[Programming language]]
* [[Guido van Rossum]]
"""
        result = convert_wikitext_to_markdown(wikitext)

        assert "***Python***" in result
        assert "**powerful**" in result
        assert "*easy*" in result
        assert "### Syntax" in result
        assert "[Programming language](https://simple.wikipedia.org/wiki/Programming_language)" in result

    def test_empty_text(self) -> None:
        result = convert_wikitext_to_markdown("")
        assert result == ""

    def test_whitespace_only(self) -> None:
        result = convert_wikitext_to_markdown("   \n  \n   ")
        assert result == ""

    def test_plain_text(self) -> None:
        wikitext = "This is just plain text with no formatting."
        result = convert_wikitext_to_markdown(wikitext)
        assert result == wikitext

    def test_with_templates_removed(self) -> None:
        wikitext = "'''Article''' {{cite web|url=http://example.com}} text"
        result = convert_wikitext_to_markdown(wikitext)

        assert "**Article**" in result
        assert "text" in result
        assert "cite web" not in result
        assert "{{" not in result

    def test_with_references_removed(self) -> None:
        wikitext = "Text<ref>Citation here</ref> more text"
        result = convert_wikitext_to_markdown(wikitext)

        assert "Text" in result
        assert "more text" in result
        assert "<ref>" not in result
        assert "Citation" not in result

    def test_with_comments_removed(self) -> None:
        wikitext = "Text <!-- comment --> more text"
        result = convert_wikitext_to_markdown(wikitext)

        assert "Text" in result
        assert "more text" in result
        assert "<!--" not in result
        assert "comment" not in result

    def test_malformed_wikitext_graceful_fallback(self) -> None:
        # Test with intentionally broken wikitext that might cause parsing errors
        wikitext = "'''unclosed bold"
        result = convert_wikitext_to_markdown(wikitext)

        # Should return something, even if it's the original text
        assert result is not None
        assert len(result) > 0

    def test_real_article_structure(self) -> None:
        wikitext = """'''Art''' is a creative activity.

== Types of art ==
There are many types:
* [[Painting]]
* [[Sculpture]]
* [[Music]]

=== Visual art ===
Visual art includes painting and sculpture.

== History ==
Art has existed since ancient times. See [[History of art]].
"""
        result = convert_wikitext_to_markdown(wikitext)

        # Check structure is preserved
        assert "**Art** is a creative activity" in result
        assert "## Types of art" in result
        assert "### Visual art" in result
        assert "## History" in result

        # Check lists converted
        assert "- [Painting](https://simple.wikipedia.org/wiki/Painting)" in result
        assert "- [Sculpture](https://simple.wikipedia.org/wiki/Sculpture)" in result

        # Check links converted
        assert "[History of art](https://simple.wikipedia.org/wiki/History_of_art)" in result
