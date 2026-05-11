"""Tests for render.py (wikitext → HTML converter)."""
import pytest
from render import (
    convert_wikitext_to_html,
    _convert_bold_italic,
    _convert_headings,
    _convert_links,
    _convert_lists,
    _convert_tables,
    _parse_cell,
    _clean_extra_markup,
    _extract_math_tags,
    _restore_math_tags,
)


class TestConvertBoldItalic:
    def test_bold_conversion(self) -> None:
        text = "This is '''bold''' text"
        result = _convert_bold_italic(text)
        assert result == "This is <strong>bold</strong> text"

    def test_italic_conversion(self) -> None:
        text = "This is ''italic'' text"
        result = _convert_bold_italic(text)
        assert result == "This is <em>italic</em> text"

    def test_bold_italic_conversion(self) -> None:
        text = "This is '''''bold and italic''''' text"
        result = _convert_bold_italic(text)
        assert result == "This is <strong><em>bold and italic</em></strong> text"

    def test_mixed_formatting(self) -> None:
        text = "'''Bold''' and ''italic'' and '''''both'''''"
        result = _convert_bold_italic(text)
        assert result == "<strong>Bold</strong> and <em>italic</em> and <strong><em>both</em></strong>"

    def test_multiple_bold_sections(self) -> None:
        text = "'''First''' and '''second''' bold"
        result = _convert_bold_italic(text)
        assert result == "<strong>First</strong> and <strong>second</strong> bold"


class TestConvertHeadings:
    def test_level_2_heading(self) -> None:
        text = "== Heading =="
        result = _convert_headings(text)
        assert result == '<h2 id="Heading">Heading</h2>'

    def test_level_3_heading(self) -> None:
        text = "=== Subheading ==="
        result = _convert_headings(text)
        assert result == '<h3 id="Subheading">Subheading</h3>'

    def test_level_4_heading(self) -> None:
        text = "==== Sub-subheading ===="
        result = _convert_headings(text)
        assert result == '<h4 id="Sub-subheading">Sub-subheading</h4>'

    def test_multiple_headings(self) -> None:
        text = "== First ==\nSome text\n=== Second ==="
        result = _convert_headings(text)
        assert '<h2 id="First">First</h2>' in result
        assert '<h3 id="Second">Second</h3>' in result

    def test_heading_with_whitespace(self) -> None:
        text = "==  Heading  =="
        result = _convert_headings(text)
        assert result == '<h2 id="Heading">Heading</h2>'

    def test_heading_with_spaces_in_text(self) -> None:
        text = "== Heat transfer =="
        result = _convert_headings(text)
        assert result == '<h2 id="Heat_transfer">Heat transfer</h2>'


class TestConvertLinks:
    def test_simple_link(self) -> None:
        text = "See [[Python]]"
        result = _convert_links(text)
        assert 'href="/article/Python"' in result
        assert 'hx-get="/article/Python"' in result
        assert 'hx-target="#article"' in result
        assert ">Python</a>" in result

    def test_link_with_label(self) -> None:
        text = "See [[Python (programming language)|Python]]"
        result = _convert_links(text)
        assert 'href="/article/Python%20%28programming%20language%29"' in result
        assert 'hx-get="/article/Python%20%28programming%20language%29"' in result
        assert ">Python</a>" in result

    def test_link_with_spaces(self) -> None:
        text = "[[United States]]"
        result = _convert_links(text)
        assert 'href="/article/United%20States"' in result
        assert ">United States</a>" in result

    def test_multiple_links(self) -> None:
        text = "[[First]] and [[Second]]"
        result = _convert_links(text)
        assert 'href="/article/First"' in result
        assert 'href="/article/Second"' in result

    def test_link_in_sentence(self) -> None:
        text = "Programming in [[Python]] is fun"
        result = _convert_links(text)
        assert 'href="/article/Python"' in result
        assert "Programming in " in result
        assert "</a> is fun" in result

    def test_lowercase_first_letter_capitalised(self) -> None:
        # MediaWiki capitalises the first letter of every wikilink target.
        text = "[[python]]"
        result = _convert_links(text)
        assert 'href="/article/Python"' in result
        # The visible label keeps the original casing the author wrote.
        assert ">python</a>" in result

    def test_anchor_split_into_url_fragment(self) -> None:
        # [[Foo#Bar]] should look up "Foo" but keep "#Bar" as the URL fragment.
        text = "[[Python#History]]"
        result = _convert_links(text)
        assert 'href="/article/Python#History"' in result
        # The hx-get URL should NOT include the anchor — it's not a different
        # endpoint, just a scroll target on the same article.
        assert 'hx-get="/article/Python"' in result

    def test_label_can_contain_inline_code(self) -> None:
        # Labels are not HTML-escaped so inline tags survive.
        text = "[[Python|<code>print()</code>]]"
        result = _convert_links(text)
        assert "<code>print()</code></a>" in result

    def test_file_link_stripped(self) -> None:
        result = _convert_links("[[File:Map.jpg|thumb|A caption]]")
        assert "thumb" not in result
        assert "File:" not in result

    def test_image_link_stripped(self) -> None:
        result = _convert_links("[[Image:Test.png|center|200px]]")
        assert "Image:" not in result

    def test_media_link_stripped(self) -> None:
        result = _convert_links("[[Media:Audio.ogg|Listen]]")
        assert "Media:" not in result

    def test_category_link_stripped(self) -> None:
        result = _convert_links("[[Category:Maps]]")
        assert "Category:" not in result


class TestConvertLists:
    def test_bullet_list(self) -> None:
        text = "* Item 1\n* Item 2"
        result = _convert_lists(text)
        assert "<ul>" in result
        assert "<li>Item 1</li>" in result
        assert "<li>Item 2</li>" in result
        assert "</ul>" in result

    def test_numbered_list(self) -> None:
        text = "# First\n# Second"
        result = _convert_lists(text)
        assert "<ol>" in result
        assert "<li>First</li>" in result
        assert "<li>Second</li>" in result
        assert "</ol>" in result

    def test_nested_bullet_list(self) -> None:
        text = "* Level 1\n** Level 2\n*** Level 3"
        result = _convert_lists(text)
        assert result.count("<ul>") == 3
        assert result.count("</ul>") == 3
        assert "<li>Level 1</li>" in result
        assert "<li>Level 2</li>" in result
        assert "<li>Level 3</li>" in result

    def test_nested_numbered_list(self) -> None:
        text = "# Level 1\n## Level 2\n### Level 3"
        result = _convert_lists(text)
        assert result.count("<ol>") == 3
        assert result.count("</ol>") == 3
        assert "<li>Level 1</li>" in result
        assert "<li>Level 2</li>" in result
        assert "<li>Level 3</li>" in result

    def test_definition_term(self) -> None:
        text = "; Python"
        result = _convert_lists(text)
        assert "<dl>" in result
        assert "<dt>Python</dt>" in result
        assert "</dl>" in result

    def test_definition_description(self) -> None:
        text = ": A programming language"
        result = _convert_lists(text)
        assert "<dl>" in result
        assert "<dd>A programming language</dd>" in result
        assert "</dl>" in result

    def test_deeper_indentation(self) -> None:
        text = ":: Further indented"
        result = _convert_lists(text)
        # Two colons create nested definition lists
        assert result.count("<dl>") >= 1
        assert "<dd>Further indented</dd>" in result

    def test_definition_list(self) -> None:
        text = "; Term\n: Description"
        result = _convert_lists(text)
        assert "<dl>" in result
        assert "<dt>Term</dt>" in result
        assert "<dd>Description</dd>" in result
        assert "</dl>" in result

    def test_mixed_ordered_then_bullet(self) -> None:
        text = "#* Sub-bullet under numbered"
        result = _convert_lists(text)
        assert "<ol>" in result
        assert "<ul>" in result
        assert "<li>Sub-bullet under numbered</li>" in result

    def test_mixed_bullet_then_ordered(self) -> None:
        text = "*# Sub-number under bullet"
        result = _convert_lists(text)
        assert "<ul>" in result
        assert "<ol>" in result
        assert "<li>Sub-number under bullet</li>" in result

    def test_mixed_content(self) -> None:
        text = "Normal text\n* List item\nMore text"
        result = _convert_lists(text)
        assert "Normal text" in result
        assert "<ul>" in result
        assert "<li>List item</li>" in result
        assert "More text" in result


class TestParseCell:
    def test_plain_content(self) -> None:
        result = _parse_cell(" value ")
        assert result["content"] == "value"
        assert result["align"] is None

    def test_parses_style_attribute(self) -> None:
        result = _parse_cell('style="text-align:center" | 42')
        assert result["content"] == "42"
        assert result["align"] == "center"

    def test_parses_colspan_attribute(self) -> None:
        result = _parse_cell("colspan=2 | text")
        assert result["content"] == "text"
        assert result["colspan"] == 2

    def test_parses_rowspan_attribute(self) -> None:
        result = _parse_cell("rowspan=3 | text")
        assert result["content"] == "text"
        assert result["rowspan"] == 3

    def test_preserves_wikilink_with_label(self) -> None:
        # The | inside [[...]] must not be treated as an attribute separator
        result = _parse_cell("[[Python (programming language)|Python]]")
        assert "Python" in result["content"]

    def test_parses_align_attribute(self) -> None:
        result = _parse_cell('align="center" | content')
        assert result["content"] == "content"
        assert result["align"] == "center"

    def test_parses_background_style(self) -> None:
        result = _parse_cell('style="background:#eee" | content')
        assert result["content"] == "content"
        assert result["style"] == "background:#eee"


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
        assert "<table" in result
        assert "<thead>" in result
        assert "<th>Name</th>" in result
        assert "<th>Age</th>" in result
        assert "<tbody>" in result
        assert "<td>Alice</td>" in result
        assert "<td>30</td>" in result
        assert "<td>Bob</td>" in result
        assert "<td>25</td>" in result
        assert "</table>" in result

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
        assert "<thead>" in result
        assert "<th>A</th>" in result
        assert "<th>B</th>" in result
        assert "<tbody>" in result
        assert "<td>C</td>" in result
        assert "<td>D</td>" in result

    def test_caption_is_preserved(self) -> None:
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
        assert "<caption>My Caption</caption>" in result
        assert "<th>H1</th>" in result

    def test_cell_attributes_parsed(self) -> None:
        wikitext = (
            "{|\n"
            "|-\n"
            '! style="width:50%" | Name\n'
            "|-\n"
            '| align="center" | Alice\n'
            "|}"
        )
        result = _convert_tables(wikitext)
        assert "Name" in result
        assert "Alice" in result
        assert 'class="align-center"' in result

    def test_colspan_attribute(self) -> None:
        wikitext = (
            "{|\n"
            "|-\n"
            "! colspan=2 | Header\n"
            "|-\n"
            "| A || B\n"
            "|}"
        )
        result = _convert_tables(wikitext)
        assert 'colspan="2"' in result
        assert "<th" in result

    def test_cells_on_separate_lines(self) -> None:
        wikitext = (
            "{|\n"
            "|-\n"
            "! Header 1 !! Header 2\n"
            "|-\n"
            "| Cell 1\n"
            "| Cell 2\n"
            "| Cell 3\n"
            "|}"
        )
        result = _convert_tables(wikitext)
        assert "<td>Cell 1</td>" in result
        assert "<td>Cell 2</td>" in result
        assert "<td>Cell 3</td>" in result

    def test_multiple_tables(self) -> None:
        wikitext = (
            "{|\n|-\n! A\n|-\n| 1\n|}\n"
            "Some text\n"
            "{|\n|-\n! B\n|-\n| 2\n|}"
        )
        result = _convert_tables(wikitext)
        assert "<th>A</th>" in result
        assert "<th>B</th>" in result
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

    def test_colon_prefixed_table_is_converted(self) -> None:
        wikitext = (
            ':{| class="wikitable"\n'
            "|-\n"
            "! Name !! Value\n"
            "|-\n"
            "| Foo || Bar\n"
            "|}"
        )
        result = _convert_tables(wikitext)
        assert "<th>Name</th>" in result
        assert "<th>Value</th>" in result
        assert "<td>Foo</td>" in result
        assert "<td>Bar</td>" in result

    def test_full_conversion_renders_table_links(self) -> None:
        wikitext = (
            "{| class=\"wikitable\"\n"
            "|-\n"
            "! Language !! Creator\n"
            "|-\n"
            "| [[Python (programming language)|Python]] || [[Guido van Rossum]]\n"
            "|}"
        )
        result = convert_wikitext_to_html(wikitext)
        assert 'href="/article/Python%20%28programming%20language%29"' in result
        assert ">Python</a>" in result
        assert 'href="/article/Guido%20van%20Rossum"' in result
        assert ">Guido van Rossum</a>" in result

    def test_full_conversion_renders_table_bold(self) -> None:
        wikitext = (
            "{|\n"
            "|-\n"
            "| '''bold cell''' || normal cell\n"
            "|}"
        )
        result = convert_wikitext_to_html(wikitext)
        assert "<strong>bold cell</strong>" in result


class TestCleanExtraMarkup:
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
        result = convert_wikitext_to_html(wikitext)

        assert "<p><strong>Python</strong> is a programming language.</p>" in result
        assert '<h2 id="History">History</h2>' in result
        assert '<h2 id="Features">Features</h2>' in result
        assert "<ul>" in result
        assert "<li>Easy to learn</li>" in result
        assert "<li>Powerful</li>" in result
        assert 'href="/article/Object-oriented%20programming"' in result
        assert ">Object-oriented</a>" in result

    def test_complex_formatting(self) -> None:
        wikitext = """'''''Python''''' is both '''powerful''' and ''easy''.

=== Syntax ===
The syntax is clean.

See also:
* [[Programming language]]
* [[Guido van Rossum]]
"""
        result = convert_wikitext_to_html(wikitext)

        assert "<strong><em>Python</em></strong>" in result
        assert "<strong>powerful</strong>" in result
        assert "<em>easy</em>" in result
        assert '<h3 id="Syntax">Syntax</h3>' in result
        assert 'href="/article/Programming%20language"' in result
        assert ">Programming language</a>" in result

    def test_empty_text(self) -> None:
        result = convert_wikitext_to_html("")
        assert result == ""

    def test_whitespace_only(self) -> None:
        result = convert_wikitext_to_html("   \n  \n   ")
        assert result == ""

    def test_plain_text(self) -> None:
        wikitext = "This is just plain text with no formatting."
        result = convert_wikitext_to_html(wikitext)
        assert "<p>This is just plain text with no formatting.</p>" in result

    def test_with_templates_removed(self) -> None:
        wikitext = "'''Article''' {{cite web|url=http://example.com}} text"
        result = convert_wikitext_to_html(wikitext)

        assert "<strong>Article</strong>" in result
        assert "text" in result
        assert "cite web" not in result
        assert "{{" not in result

    def test_with_references_removed(self) -> None:
        wikitext = "Text<ref>Citation here</ref> more text"
        result = convert_wikitext_to_html(wikitext)

        assert "Text" in result
        assert "more text" in result
        assert "<ref>" not in result
        assert "Citation" not in result

    def test_with_comments_removed(self) -> None:
        wikitext = "Text <!-- comment --> more text"
        result = convert_wikitext_to_html(wikitext)

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
        result = convert_wikitext_to_html(wikitext)
        # Count list items
        assert result.count("<li>") == 3
        assert "<ul>" in result

    def test_malformed_wikitext_graceful_fallback(self) -> None:
        # Test with intentionally broken wikitext that might cause parsing errors
        wikitext = "'''unclosed bold"
        result = convert_wikitext_to_html(wikitext)

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
        result = convert_wikitext_to_html(wikitext)

        # Check structure is preserved
        assert "<strong>Art</strong> is a creative activity" in result
        assert '<h2 id="Types_of_art">Types of art</h2>' in result
        assert '<h3 id="Visual_art">Visual art</h3>' in result
        assert '<h2 id="History">History</h2>' in result

        # Check lists converted
        assert 'href="/article/Painting"' in result
        assert ">Painting</a>" in result
        assert 'href="/article/Sculpture"' in result
        assert ">Sculpture</a>" in result
        assert "<ul>" in result

        # Check links converted
        assert 'href="/article/History%20of%20art"' in result
        assert ">History of art</a>" in result

    def test_file_link_with_nested_caption_not_rendered(self) -> None:
        # File links whose captions contain nested wikilinks must be fully
        # stripped — the plain-text regex can't match across nested brackets.
        wikitext = "[[File:Tabula_Rogeriana.jpg|thumb|upright=1.35|center|Caption with [[nested link]] here]]"
        result = convert_wikitext_to_html(wikitext)
        assert "thumb" not in result
        assert "upright" not in result
        assert "File:" not in result

    def test_image_link_with_plain_caption_stripped(self) -> None:
        wikitext = "Some text.\n\n[[Image:Test.png|400px|A plain caption]]\n\nMore text."
        result = convert_wikitext_to_html(wikitext)
        assert "Image:" not in result
        assert "400px" not in result
        assert "Some text" in result
        assert "More text" in result


# ---------------------------------------------------------------------------
# Math rendering
# ---------------------------------------------------------------------------


class TestMathRendering:
    def test_inline_math_becomes_katex_delimiter(self) -> None:
        result = convert_wikitext_to_html("The value <math>x^2</math> is positive.")
        assert "\\(x^2\\)" in result

    def test_block_math_becomes_display_delimiter(self) -> None:
        result = convert_wikitext_to_html('<math display="block">Z = \\frac{x}{y}</math>')
        assert "$$" in result
        assert "Z = \\frac{x}{y}" in result
        assert 'class="math-display"' in result

    def test_block_math_display_single_quotes(self) -> None:
        result = convert_wikitext_to_html("<math display='block'>\\sigma</math>")
        assert "$$" in result
        assert "\\sigma" in result

    def test_z_test_se_formula(self) -> None:
        formula = r"\mathrm{SE} = \frac{\sigma}{\sqrt n} = \frac{12}{\sqrt{55}} = \frac{12}{7.42} = 1.62"
        result = convert_wikitext_to_html(f"<math>{formula}</math>")
        assert formula in result
        assert "\\(" in result
        assert "\\)" in result

    def test_math_not_mangled_by_bold_italic_pass(self) -> None:
        # LaTeX uses '' in \text{} constructs; bold/italic pass must not touch it
        result = convert_wikitext_to_html(r"<math>\text{if } x > 0</math>")
        assert r"\text{if } x > 0" in result

    def test_math_template_converted(self) -> None:
        result = convert_wikitext_to_html("Let {{math|x^2 + y^2 = z^2}}.")
        assert "\\(x^2 + y^2 = z^2\\)" in result

    def test_mvar_template_converted(self) -> None:
        result = convert_wikitext_to_html("The variable {{mvar|\\sigma}} represents standard deviation.")
        assert "\\(\\sigma\\)" in result

    def test_multiple_inline_formulas(self) -> None:
        result = convert_wikitext_to_html(
            "When <math>\\mu = 0</math> and <math>\\sigma = 1</math>."
        )
        assert result.count("\\(") == 2
        assert result.count("\\)") == 2
        assert "\\mu = 0" in result
        assert "\\sigma = 1" in result

    def test_extract_and_restore_roundtrip(self) -> None:
        text = r'Inline <math>a + b</math> and block <math display="block">c = d</math>.'
        processed, math_blocks = _extract_math_tags(text)
        # Placeholders replace originals
        assert "<math>" not in processed
        assert "a + b" not in processed
        # Restore
        restored = _restore_math_tags(processed, math_blocks)
        assert "\\(a + b\\)" in restored
        assert "$$" in restored
        assert "c = d" in restored

    def test_empty_math_tag(self) -> None:
        # Empty math tags should not crash
        result = convert_wikitext_to_html("<math></math>")
        assert result is not None

    def test_yes_indicator_template(self) -> None:
        result = convert_wikitext_to_html("{{yes}}")
        assert '<span class="indicator-yes">Yes</span>' in result

    def test_no_indicator_template(self) -> None:
        result = convert_wikitext_to_html("{{no}}")
        assert '<span class="indicator-no">No</span>' in result

    def test_partial_indicator_template(self) -> None:
        result = convert_wikitext_to_html("{{partial}}")
        assert '<span class="indicator-partial">Partial</span>' in result

    def test_indicator_in_table(self) -> None:
        wikitext = """{| class="wikitable"
! Feature !! Supported
|-
| Feature A || {{yes}}
|-
| Feature B || {{no}}
|-
| Feature C || {{partial}}
|}"""
        result = convert_wikitext_to_html(wikitext)
        assert '<span class="indicator-yes">Yes</span>' in result
        assert '<span class="indicator-no">No</span>' in result
        assert '<span class="indicator-partial">Partial</span>' in result
        assert "<table" in result

    def test_indicator_variants(self) -> None:
        # Test various template name variants
        assert 'indicator-yes' in convert_wikitext_to_html("{{tick}}")
        assert 'indicator-yes' in convert_wikitext_to_html("{{checked}}")
        assert 'indicator-no' in convert_wikitext_to_html("{{cross}}")
        assert 'indicator-unknown' in convert_wikitext_to_html("{{dunno}}")
        assert 'indicator-na' in convert_wikitext_to_html("{{n/a}}")


class TestSectionLinkTemplates:
    def test_section_link_basic(self) -> None:
        """Test basic {{Section link|Page#Section}} conversion."""
        result = convert_wikitext_to_html("{{Section link|Ferrofluid#Heat transfer}}")
        assert 'href="/article/Ferrofluid#Heat%20transfer"' in result
        assert ">Ferrofluid#Heat transfer</a>" in result

    def test_section_link_with_label(self) -> None:
        """Test {{Section link|Page#Section|Label}} with custom label."""
        result = convert_wikitext_to_html("{{Section link|Ferrofluid#Heat transfer|heat transfer}}")
        assert 'href="/article/Ferrofluid#Heat%20transfer"' in result
        assert ">heat transfer</a>" in result

    def test_section_link_in_list(self) -> None:
        """Test section link within a list context."""
        wikitext = """== See also ==
* [[Ferrofluid]]
* {{Section link|Ferrofluid#Heat transfer}}
* [[Audio system]]"""
        result = convert_wikitext_to_html(wikitext)
        assert 'href="/article/Ferrofluid"' in result
        assert 'href="/article/Ferrofluid#Heat%20transfer"' in result
        assert 'href="/article/Audio%20system"' in result


class TestReflistTemplate:
    def test_reflist_with_cite_web(self) -> None:
        wikitext = (
            '{{Reflist|2|refs=\n'
            '<ref name=Foo>{{cite web|title=Some Article|url=https://example.com|date=2020}}</ref>\n'
            '}}'
        )
        result = convert_wikitext_to_html(wikitext)
        assert '<ol class="references">' in result
        assert 'Some Article' in result
        assert 'href="https://example.com"' in result
        assert '2020' in result

    def test_reflist_multiple_refs(self) -> None:
        wikitext = (
            '{{Reflist|refs=\n'
            '<ref name=A>{{cite web|title=First|url=https://first.com}}</ref>\n'
            '<ref name=B>{{cite web|title=Second|url=https://second.com}}</ref>\n'
            '}}'
        )
        result = convert_wikitext_to_html(wikitext)
        assert 'First' in result
        assert 'Second' in result
        assert 'href="https://first.com"' in result
        assert 'href="https://second.com"' in result

    def test_reflist_without_refs_param_removed(self) -> None:
        """Plain {{Reflist}} with no refs= is silently removed."""
        result = convert_wikitext_to_html('Some text.\n{{Reflist}}\nMore text.')
        assert '{{Reflist}}' not in result
        assert 'Some text.' in result

    def test_reflist_ref_ids(self) -> None:
        """Each <li> gets an id so anchor links can target it."""
        wikitext = (
            '{{Reflist|refs=\n'
            '<ref name=MyRef>{{cite web|title=Target|url=https://x.com}}</ref>\n'
            '}}'
        )
        result = convert_wikitext_to_html(wikitext)
        assert 'id="ref_MyRef"' in result


class TestInlineRefCollection:
    def test_unnamed_inline_ref_rendered(self) -> None:
        wikitext = (
            'Text.<ref>{{cite book |last=Smith |title=Foo |date=2020}}</ref>\n'
            '{{Reflist}}'
        )
        result = convert_wikitext_to_html(wikitext)
        assert '<ol class="references">' in result
        assert 'Smith' in result
        assert '<em>Foo</em>' in result
        assert '2020' in result

    def test_named_inline_ref_rendered(self) -> None:
        wikitext = (
            'Text.<ref name="Ballou2008">{{cite book |last=Ballou |title=Handbook |date=2008}}</ref>\n'
            '{{Reflist}}'
        )
        result = convert_wikitext_to_html(wikitext)
        assert '<ol class="references">' in result
        assert 'id="ref_Ballou2008"' in result
        assert 'Ballou' in result

    def test_named_ref_appears_multiple_times(self) -> None:
        """Named ref with content appearing twice in the body renders twice."""
        wikitext = (
            'First.<ref name="A">{{cite book |title=Alpha |date=2021}}</ref> '
            'Second.<ref name="A">{{cite book |title=Alpha |date=2021}}</ref>\n'
            '{{Reflist}}'
        )
        result = convert_wikitext_to_html(wikitext)
        assert result.count('Alpha') == 2

    def test_back_ref_not_collected(self) -> None:
        wikitext = (
            'First.<ref name="B">{{cite book |title=Beta |date=2022}}</ref> '
            'Second.<ref name="B"/>\n'
            '{{Reflist}}'
        )
        result = convert_wikitext_to_html(wikitext)
        assert result.count('Beta') == 1

    def test_multiple_mixed_refs(self) -> None:
        wikitext = (
            'A.<ref>{{cite book |title=Unnamed1 |date=2001}}</ref> '
            'B.<ref name="Named">{{cite book |title=Named1 |date=2002}}</ref> '
            'C.<ref>{{cite book |title=Unnamed2 |date=2003}}</ref>\n'
            '{{Reflist}}'
        )
        result = convert_wikitext_to_html(wikitext)
        assert 'Unnamed1' in result
        assert 'Named1' in result
        assert 'Unnamed2' in result
        assert result.count('<li') >= 3

    def test_plain_text_ref_fallback(self) -> None:
        wikitext = (
            'Text.<ref>A plain-text footnote without a template.</ref>\n'
            '{{Reflist}}'
        )
        result = convert_wikitext_to_html(wikitext)
        assert 'plain-text footnote' in result
        assert '<ol class="references">' in result

    def test_bare_reflist_no_inline_refs_still_silent(self) -> None:
        result = convert_wikitext_to_html('Some text.\n{{Reflist}}\nMore text.')
        assert '<ol class="references">' not in result
        assert '{{Reflist}}' not in result

    def test_reflist_refs_param_unaffected(self) -> None:
        wikitext = (
            '{{Reflist|refs=\n'
            '<ref name=Foo>{{cite web|title=ExplicitRef|url=https://ex.com|date=2020}}</ref>\n'
            '}}'
        )
        result = convert_wikitext_to_html(wikitext)
        assert '<ol class="references">' in result
        assert 'ExplicitRef' in result
        assert 'href="https://ex.com"' in result

    def test_unnamed_ref_id_is_numeric(self) -> None:
        wikitext = (
            'A.<ref>{{cite book |title=First |date=2001}}</ref> '
            'B.<ref>{{cite book |title=Second |date=2002}}</ref>\n'
            '{{Reflist}}'
        )
        result = convert_wikitext_to_html(wikitext)
        assert 'id="ref_1"' in result
        assert 'id="ref_2"' in result

    def test_no_reflist_inline_refs_stripped(self) -> None:
        wikitext = 'Text.<ref>{{cite book |title=Gone |date=2020}}</ref> More text.'
        result = convert_wikitext_to_html(wikitext)
        assert '<ref>' not in result
        assert 'Gone' not in result


class TestStripExternalLinksSection:
    def test_removes_external_links_section(self) -> None:
        wikitext = 'Intro text.\n\n== External links ==\n* [http://example.com Example]\n'
        result = convert_wikitext_to_html(wikitext)
        assert 'External links' not in result
        assert 'example.com' not in result
        assert 'Intro text' in result

    def test_preserves_content_after_section(self) -> None:
        wikitext = (
            'Intro.\n\n'
            '== External links ==\n* [http://example.com Example]\n\n'
            '== See also ==\n* [[Python]]\n'
        )
        result = convert_wikitext_to_html(wikitext)
        assert 'External links' not in result
        assert 'See also' in result

    def test_case_insensitive(self) -> None:
        wikitext = 'Intro.\n\n== external links ==\n* [http://example.com Example]\n'
        result = convert_wikitext_to_html(wikitext)
        assert 'external links' not in result

    def test_no_external_links_section_unchanged(self) -> None:
        wikitext = '== History ==\nSome history text.\n'
        result = convert_wikitext_to_html(wikitext)
        assert 'History' in result
        assert 'history text' in result
