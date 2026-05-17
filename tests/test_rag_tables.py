"""Tests for rag/tables.py — wikitable and infobox extraction."""

from rag.chunker import chunk_article
from rag.tables import extract_infoboxes, extract_tables

# ---------------------------------------------------------------------------
# Wikitable tests
# ---------------------------------------------------------------------------


class TestExtractTables:
    def _simple_table(self):
        return """{| class="wikitable"
|-
! Country !! Capital
|-
| France || Paris
|-
| Germany || Berlin
|}"""

    def test_simple_table_produces_one_chunk(self):
        chunks, _ = extract_tables(self._simple_table(), None)
        assert len(chunks) == 1
        assert chunks[0]["chunk_type"] == "table"

    def test_simple_table_section_none(self):
        chunks, _ = extract_tables(self._simple_table(), None)
        assert chunks[0]["section"] is None

    def test_simple_table_section_passed_through(self):
        chunks, _ = extract_tables(self._simple_table(), "History")
        assert chunks[0]["section"] == "History"

    def test_simple_table_row_serialization(self):
        chunks, _ = extract_tables(self._simple_table(), None)
        text = chunks[0]["text"]
        assert "Country: France | Capital: Paris" in text
        assert "Country: Germany | Capital: Berlin" in text

    def test_caption_preserved(self):
        wikitext = """{| class="wikitable"
|+ My caption
|-
! Col
|-
| Value
|}"""
        chunks, _ = extract_tables(wikitext, None)
        assert len(chunks) == 1
        assert chunks[0]["text"].startswith("Table: My caption")

    def test_no_caption_uses_untitled(self):
        chunks, _ = extract_tables(self._simple_table(), None)
        assert chunks[0]["text"].startswith("Table: untitled")

    def test_header_inferred_from_first_body_row(self):
        wikitext = """{|
| A || B
|-
| 1 || 2
|-
| 3 || 4
|}"""
        chunks, _ = extract_tables(wikitext, None)
        assert len(chunks) == 1
        # First row ("A", "B") promoted to headers; second row body
        assert "A: 1 | B: 2" in chunks[0]["text"]

    def test_long_table_splits_at_row_boundaries(self):
        rows = "\n|-\n| " + "\n|-\n| ".join(f"Row {i}" for i in range(50))
        wikitext = "{|\n! Col\n" + rows + "\n|}"
        # Force tiny max_chars to ensure a split
        from rag import tables as tbl

        orig = tbl.MAX_TABLE_CHARS
        tbl.MAX_TABLE_CHARS = 30
        try:
            chunks, _ = extract_tables(wikitext, None)
        finally:
            tbl.MAX_TABLE_CHARS = orig

        assert len(chunks) > 1
        # Every part starts with the header line
        for c in chunks:
            assert c["text"].startswith("Table:")

    def test_cell_attribute_stripping(self):
        wikitext = """{|
! Col
|-
| colspan="2" | Wide cell
|}"""
        chunks, _ = extract_tables(wikitext, None)
        assert len(chunks) == 1
        assert "Wide cell" in chunks[0]["text"]
        assert "colspan" not in chunks[0]["text"]

    def test_cell_with_wikilink(self):
        wikitext = """{|
! City
|-
| [[Paris]]
|}"""
        chunks, _ = extract_tables(wikitext, None)
        assert len(chunks) == 1
        assert "Paris" in chunks[0]["text"]
        assert "[[" not in chunks[0]["text"]

    def test_nested_table_outer_renders_inner_skipped(self):
        wikitext = """{| class="wikitable"
|-
! Col
|-
| Outer cell
{|
| inner data
|}
|}"""
        chunks, _ = extract_tables(wikitext, None)
        assert len(chunks) == 1
        assert "Outer cell" in chunks[0]["text"]
        assert "inner data" not in chunks[0]["text"]

    def test_unclosed_table_no_chunk_source_unchanged(self):
        wikitext = "{|\n! Col\n| Value\n"  # no closing |}
        chunks, cleaned = extract_tables(wikitext, None)
        assert chunks == []
        # Source returned unchanged (unclosed tables are not consumed)
        assert "{|" in cleaned

    def test_cleaned_wikitext_has_no_table_lines(self):
        prose = "Before.\n" + self._simple_table() + "\nAfter."
        _, cleaned = extract_tables(prose, None)
        assert "{|" not in cleaned
        assert "Before." in cleaned
        assert "After." in cleaned

    def test_no_tables_returns_empty_list_and_unchanged_text(self):
        wikitext = "Just some plain text."
        chunks, cleaned = extract_tables(wikitext, None)
        assert chunks == []
        assert cleaned == wikitext


# ---------------------------------------------------------------------------
# Infobox tests
# ---------------------------------------------------------------------------


class TestExtractInfoboxes:
    def _country_infobox(self):
        return "{{Infobox country\n| name = France\n| capital = Paris\n| population = 68M\n}}"

    def test_basic_infobox_produces_one_chunk(self):
        chunks = extract_infoboxes(self._country_infobox(), "France")
        assert len(chunks) == 1
        assert chunks[0]["chunk_type"] == "infobox"

    def test_infobox_section_is_none(self):
        chunks = extract_infoboxes(self._country_infobox(), "France")
        assert chunks[0]["section"] is None

    def test_infobox_contains_field_values(self):
        chunks = extract_infoboxes(self._country_infobox(), "France")
        text = chunks[0]["text"]
        assert "capital: Paris" in text
        assert "population: 68M" in text

    def test_image_field_skipped(self):
        wikitext = "{{Infobox country\n| flag_image = Flag.svg\n| capital = Paris\n}}"
        chunks = extract_infoboxes(wikitext, "France")
        assert len(chunks) == 1
        assert "Flag.svg" not in chunks[0]["text"]
        assert "Paris" in chunks[0]["text"]

    def test_empty_value_skipped(self):
        wikitext = "{{Infobox country\n| capital =\n| population = 68M\n}}"
        chunks = extract_infoboxes(wikitext, "France")
        assert len(chunks) == 1
        text = chunks[0]["text"]
        assert "capital:" not in text
        assert "68M" in text

    def test_nested_template_value_is_non_empty(self):
        wikitext = "{{Infobox person\n| birthdate = {{birth date|1980|1|1}}\n}}"
        chunks = extract_infoboxes(wikitext, "Someone")
        assert len(chunks) == 1
        # strip_code will flatten the nested template — we just need non-empty content
        text = chunks[0]["text"]
        assert "birthdate" in text
        # Should contain at least a digit from the date
        assert any(c.isdigit() for c in text)

    def test_article_title_in_chunk_header(self):
        chunks = extract_infoboxes(self._country_infobox(), "France")
        assert chunks[0]["text"].startswith("Infobox: France")

    def test_infobox_kind_in_header(self):
        chunks = extract_infoboxes(self._country_infobox(), "France")
        assert "country" in chunks[0]["text"].split("\n")[0].lower()

    def test_no_infobox_returns_empty(self):
        chunks = extract_infoboxes("Just plain text.", "Test")
        assert chunks == []

    def test_non_infobox_template_ignored(self):
        wikitext = "{{cite web|url=https://example.com|title=Example}}\nSome text."
        chunks = extract_infoboxes(wikitext, "Test")
        assert chunks == []

    def test_speciesbox_produces_taxon_chunk(self):
        wikitext = "{{speciesbox|name=Barley|image=foo.jpg|genus=Hordeum|species=vulgare}}"
        chunks = extract_infoboxes(wikitext, "Barley")
        assert len(chunks) == 1
        text = chunks[0]["text"]
        assert "Taxon: Barley (Barley)" in text
        assert "genus: Hordeum" in text

    def test_speciesbox_name_field_not_duplicated(self):
        wikitext = "{{speciesbox|name=Barley|genus=Hordeum|species=vulgare}}"
        chunks = extract_infoboxes(wikitext, "Barley")
        text = chunks[0]["text"]
        # "name" should only appear in the header, not as a field row
        lines = text.split("\n")
        field_lines = lines[1:]
        assert not any(line.lower().startswith("name:") for line in field_lines)

    def test_speciesbox_image_skipped(self):
        wikitext = "{{speciesbox|name=Barley|image=foo.jpg|genus=Hordeum}}"
        chunks = extract_infoboxes(wikitext, "Barley")
        assert "foo.jpg" not in chunks[0]["text"]

    def test_taxobox_produces_taxon_chunk(self):
        wikitext = "{{taxobox|name=Banana|regnum=Plantae|ordo=Zingiberales}}"
        chunks = extract_infoboxes(wikitext, "Banana")
        assert len(chunks) == 1
        assert "Taxon: Banana (Banana)" in chunks[0]["text"]
        assert "Plantae" in chunks[0]["text"]

    def test_taxobox_no_name_fallback(self):
        wikitext = "{{speciesbox|genus=Hordeum|species=vulgare}}"
        chunks = extract_infoboxes(wikitext, "Barley")
        assert len(chunks) == 1
        assert "Taxon: Barley\n" in chunks[0]["text"]


# ---------------------------------------------------------------------------
# Integration tests via chunk_article
# ---------------------------------------------------------------------------


class TestChunkArticleIntegration:
    def test_all_three_types_emitted(self):
        wikitext = """{{Infobox country
| capital = Paris
}}

France is a country.

== History ==
{| class="wikitable"
|-
! Year !! Event
|-
| 1789 || Revolution
|}

More history text.
"""
        chunks = chunk_article("France", wikitext)
        types = {c["chunk_type"] for c in chunks}
        assert "infobox" in types
        assert "table" in types
        assert "prose" in types

    def test_infobox_comes_first(self):
        wikitext = """{{Infobox country
| capital = Paris
}}

France is a country.
"""
        chunks = chunk_article("France", wikitext)
        assert chunks[0]["chunk_type"] == "infobox"

    def test_table_before_prose_in_same_section(self):
        wikitext = """== Data ==
{| class="wikitable"
|-
! Key
|-
| Value
|}

Some prose after the table.
"""
        chunks = chunk_article("T", wikitext)
        section_chunks = [c for c in chunks if c["section"] == "Data"]
        types_in_order = [c["chunk_type"] for c in section_chunks]
        table_idx = types_in_order.index("table")
        prose_idx = types_in_order.index("prose")
        assert table_idx < prose_idx

    def test_only_infobox_no_empty_prose_chunk(self):
        wikitext = """{{Infobox country
| capital = Paris
}}
"""
        chunks = chunk_article("France", wikitext)
        # Should have infobox chunk; any prose chunks must be non-empty
        for c in chunks:
            assert c["text"].strip() != ""
        infobox_chunks = [c for c in chunks if c["chunk_type"] == "infobox"]
        assert len(infobox_chunks) >= 1
