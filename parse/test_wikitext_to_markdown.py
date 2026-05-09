"""Tests for wikitext_to_markdown.py."""
import pytest
from parse.wikitext_to_markdown import (
    convert_wikitext_to_markdown,
    _convert_bold_italic,
    _convert_headings,
    _convert_links,
    _convert_lists,
    _convert_tables,
    _extract_cell_content,
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
    EN = "https://en.wikipedia.org/wiki/"
    SIMPLE = "https://en.wikipedia.org/wiki/"

    def test_simple_link(self) -> None:
        text = "See [[Python]]"
        assert _convert_links(text, self.EN) == "See [Python](https://en.wikipedia.org/wiki/Python)"

    def test_link_with_label(self) -> None:
        text = "See [[Python (programming language)|Python]]"
        assert _convert_links(text, self.EN) == "See [Python](https://en.wikipedia.org/wiki/Python_(programming_language))"

    def test_link_with_spaces(self) -> None:
        text = "[[United States]]"
        assert _convert_links(text, self.EN) == "[United States](https://en.wikipedia.org/wiki/United_States)"

    def test_multiple_links(self) -> None:
        text = "[[First]] and [[Second]]"
        result = _convert_links(text, self.EN)
        assert "[First](https://en.wikipedia.org/wiki/First)" in result
        assert "[Second](https://en.wikipedia.org/wiki/Second)" in result

    def test_link_in_sentence(self) -> None:
        text = "Programming in [[Python]] is fun"
        assert _convert_links(text, self.EN) == "Programming in [Python](https://en.wikipedia.org/wiki/Python) is fun"

    def test_custom_base_url(self) -> None:
        text = "See [[Python]]"
        assert _convert_links(text, self.SIMPLE) == "See [Python](https://en.wikipedia.org/wiki/Python)"


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

    def test_nested_numbered_list(self) -> None:
        text = "# Level 1\n## Level 2\n### Level 3"
        result = _convert_lists(text)
        lines = result.split("\n")
        assert lines[0] == "1. Level 1"
        assert lines[1] == "  1. Level 2"
        assert lines[2] == "    1. Level 3"

    def test_definition_term(self) -> None:
        text = "; Python"
        assert _convert_lists(text) == "**Python**"

    def test_definition_description(self) -> None:
        text = ": A programming language"
        assert _convert_lists(text) == "> A programming language"

    def test_deeper_indentation(self) -> None:
        text = ":: Further indented"
        assert _convert_lists(text) == ">> Further indented"

    def test_definition_list(self) -> None:
        text = "; Term\n: Description"
        result = _convert_lists(text)
        lines = result.split("\n")
        assert lines[0] == "**Term**"
        assert lines[1] == "> Description"

    def test_mixed_ordered_then_bullet(self) -> None:
        text = "#* Sub-bullet under numbered"
        assert _convert_lists(text) == "  - Sub-bullet under numbered"

    def test_mixed_bullet_then_ordered(self) -> None:
        text = "*# Sub-number under bullet"
        assert _convert_lists(text) == "  1. Sub-number under bullet"

    def test_mixed_content(self) -> None:
        text = "Normal text\n* List item\nMore text"
        result = _convert_lists(text)
        assert "Normal text" in result
        assert "- List item" in result
        assert "More text" in result

    def test_blank_line_inserted_before_list_after_paragraph(self) -> None:
        text = "Statements include the following:\n* Item one\n* Item two"
        result = _convert_lists(text)
        lines = result.split("\n")
        assert lines[0] == "Statements include the following:"
        assert lines[1] == ""
        assert lines[2] == "- Item one"
        assert lines[3] == "- Item two"


class TestExtractCellContent:
    def test_plain_content(self) -> None:
        assert _extract_cell_content(" value ") == "value"

    def test_strips_style_attribute(self) -> None:
        assert _extract_cell_content('style="text-align:center" | 42') == "42"

    def test_strips_colspan_attribute(self) -> None:
        assert _extract_cell_content("colspan=2 | text") == "text"

    def test_preserves_wikilink_with_label(self) -> None:
        # The | inside [[...]] must not be treated as an attribute separator
        assert _extract_cell_content("[[Python (programming language)|Python]]") == "[[Python (programming language)|Python]]"

    def test_strips_attrs_before_wikilink(self) -> None:
        assert _extract_cell_content('align="center" | [[Link|Label]]') == "[[Link|Label]]"


class TestConvertTables:
    def test_basic_table_with_headers(self) -> None:
        wikitext = (
            "{| class=\"wikitable\"\n"
            "|-\n"
            "! Name !! Age\n"
            "|-\n"
            "| Alice || 30\n"
            "|-\n"
            "| Bob || 25\n"
            "|}"
        )
        result = _convert_tables(wikitext)
        assert "| Name | Age |" in result
        assert "| --- | --- |" in result
        assert "| Alice | 30 |" in result
        assert "| Bob | 25 |" in result

    def test_table_without_explicit_headers(self) -> None:
        wikitext = (
            "{|\n"
            "|-\n"
            "| A || B\n"
            "|-\n"
            "| C || D\n"
            "|}"
        )
        result = _convert_tables(wikitext)
        # First data row becomes the header
        assert "| A | B |" in result
        assert "| --- | --- |" in result
        assert "| C | D |" in result

    def test_caption_is_skipped(self) -> None:
        wikitext = (
            "{|\n"
            "|+ My Caption\n"
            "|-\n"
            "! H1\n"
            "|-\n"
            "| D1\n"
            "|}"
        )
        result = _convert_tables(wikitext)
        assert "My Caption" not in result
        assert "| H1 |" in result

    def test_cell_attributes_stripped(self) -> None:
        wikitext = (
            "{|\n"
            "|-\n"
            '! style="width:50%" | Name\n'
            "|-\n"
            '| align="center" | Alice\n'
            "|}"
        )
        result = _convert_tables(wikitext)
        assert "style=" not in result
        assert "align=" not in result
        assert "Name" in result
        assert "Alice" in result

    def test_cells_on_separate_lines(self) -> None:
        wikitext = (
            "{|\n"
            "|-\n"
            "| Cell 1\n"
            "| Cell 2\n"
            "| Cell 3\n"
            "|}"
        )
        result = _convert_tables(wikitext)
        assert "| Cell 1 | Cell 2 | Cell 3 |" in result

    def test_multiple_tables(self) -> None:
        wikitext = (
            "{|\n|-\n! A\n|-\n| 1\n|}\n"
            "Some text\n"
            "{|\n|-\n! B\n|-\n| 2\n|}"
        )
        result = _convert_tables(wikitext)
        assert "| A |" in result
        assert "| B |" in result
        assert "Some text" in result

    def test_unclosed_table_does_not_eat_subsequent_content(self) -> None:
        wikitext = "{| class='wikitable'\n| Cell\n* List item after unclosed table\n"
        result = _convert_tables(wikitext)
        assert "* List item after unclosed table" in result

    def test_empty_table_returns_empty(self) -> None:
        result = _convert_tables("{|\n|}")
        assert result.strip() == ""

    def test_non_table_text_unchanged(self) -> None:
        text = "Normal paragraph\nwith two lines"
        assert _convert_tables(text) == text

    def test_full_conversion_renders_table_links(self) -> None:
        wikitext = (
            "{| class=\"wikitable\"\n"
            "|-\n"
            "! Language !! Creator\n"
            "|-\n"
            "| [[Python (programming language)|Python]] || [[Guido van Rossum]]\n"
            "|}"
        )
        result = convert_wikitext_to_markdown(wikitext)
        assert "[Python](https://en.wikipedia.org/wiki/Python_(programming_language))" in result
        assert "[Guido van Rossum](https://en.wikipedia.org/wiki/Guido_van_Rossum)" in result

    def test_full_conversion_renders_table_bold(self) -> None:
        wikitext = (
            "{|\n"
            "|-\n"
            "| '''bold cell''' || normal cell\n"
            "|}"
        )
        result = convert_wikitext_to_markdown(wikitext)
        assert "**bold cell**" in result


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
        assert "[Object-oriented](https://en.wikipedia.org/wiki/Object-oriented_programming)" in result

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
        assert "[Programming language](https://en.wikipedia.org/wiki/Programming_language)" in result

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

    def test_list_items_with_inline_code(self) -> None:
        wikitext = (
            "===Statements and control flow===\n"
            "Python's [[statement (computer science)|statements]] include the following:\n"
            "* The [[Assignment (computer science)|assignment]] statement, "
            "using a single equals sign <code>=</code>\n"
            "* The <code>[[if-then-else|if]]</code> statement\n"
            "* The <code>[[Foreach#Python|for]]</code> statement\n"
        )
        result = convert_wikitext_to_markdown(wikitext)
        list_lines = [l for l in result.splitlines() if l.startswith("- ")]
        assert len(list_lines) == 3

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
        assert "- [Painting](https://en.wikipedia.org/wiki/Painting)" in result
        assert "- [Sculpture](https://en.wikipedia.org/wiki/Sculpture)" in result

        # Check links converted
        assert "[History of art](https://en.wikipedia.org/wiki/History_of_art)" in result
