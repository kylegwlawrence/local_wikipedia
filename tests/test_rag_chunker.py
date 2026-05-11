"""Tests for rag/chunker.py — wikitext chunking logic."""
import pytest

from rag.chunker import MAX_CHUNK_CHARS, chunk_article, extract_categories, is_redirect


class TestExtractCategories:
    def test_simple_category(self):
        assert extract_categories("[[Category:Months]]") == ["Months"]

    def test_category_with_sort_key(self):
        assert extract_categories("[[Category:Months|*04]]") == ["Months"]

    def test_multiple_categories(self):
        cats = extract_categories("[[Category:A]]\n[[Category:B]]")
        assert cats == ["A", "B"]

    def test_no_categories(self):
        assert extract_categories("Some article text.") == []

    def test_case_insensitive(self):
        cats = extract_categories("[[category:science]]")
        assert cats == ["science"]

    def test_strips_whitespace(self):
        cats = extract_categories("[[Category:  History  ]]")
        assert cats == ["History"]


class TestIsRedirect:
    def test_simple_redirect(self):
        assert is_redirect("#REDIRECT [[Target]]")

    def test_lowercase_redirect(self):
        assert is_redirect("#redirect [[Target]]")

    def test_mixed_case(self):
        assert is_redirect("#Redirect [[Target]]")

    def test_with_leading_whitespace(self):
        assert is_redirect("  #REDIRECT [[Target]]")

    def test_not_redirect(self):
        assert not is_redirect("April is the fourth month.")

    def test_empty_string(self):
        assert not is_redirect("")


class TestChunkArticle:
    def test_redirect_returns_empty(self):
        assert chunk_article("T", "#REDIRECT [[Other]]") == []

    def test_empty_wikitext_returns_empty(self):
        assert chunk_article("T", "") == []

    def test_whitespace_only_returns_empty(self):
        assert chunk_article("T", "   \n\n   ") == []

    def test_no_sections_produces_single_chunk(self):
        chunks = chunk_article("T", "Some introductory text.")
        assert len(chunks) == 1
        assert chunks[0]["section"] is None
        assert chunks[0]["chunk_index"] == 0

    def test_lead_and_one_section(self):
        wikitext = "Lead text.\n\n== History ==\nSection text."
        chunks = chunk_article("T", wikitext)
        sections = [c["section"] for c in chunks]
        assert None in sections
        assert "History" in sections

    def test_section_heading_captured(self):
        wikitext = "== Geography ==\nSome geographic info."
        chunks = chunk_article("T", wikitext)
        assert chunks[0]["section"] == "Geography"

    def test_empty_section_skipped(self):
        wikitext = "Lead.\n\n== Empty ==\n\n== Real ==\nContent here."
        chunks = chunk_article("T", wikitext)
        sections = [c["section"] for c in chunks]
        assert "Empty" not in sections
        assert "Real" in sections

    def test_chunk_index_increments_on_split(self):
        long_text = "Word " * 500  # well over MAX_CHUNK_CHARS
        wikitext = f"== Section ==\n{long_text}"
        chunks = chunk_article("T", wikitext, max_chars=100)
        indices = [c["chunk_index"] for c in chunks if c["section"] == "Section"]
        assert indices == list(range(len(indices)))
        assert len(indices) > 1

    def test_long_lead_splits(self):
        long_text = "Paragraph one. " * 200
        chunks = chunk_article("T", long_text, max_chars=100)
        assert len(chunks) > 1

    def test_text_is_plain_not_wikitext(self):
        wikitext = "'''Bold''' and [[Link|label]] text."
        chunks = chunk_article("T", wikitext)
        assert chunks
        # mwparserfromhell strip_code removes wikitext markup
        assert "'''" not in chunks[0]["text"]
        assert "[[" not in chunks[0]["text"]

    def test_returns_dicts_with_required_keys(self):
        chunks = chunk_article("T", "Some text.")
        assert chunks
        for chunk in chunks:
            assert "section" in chunk
            assert "chunk_index" in chunk
            assert "text" in chunk

    def test_h3_section_captured(self):
        wikitext = "=== Subsection ===\nContent."
        chunks = chunk_article("T", wikitext)
        assert chunks[0]["section"] == "Subsection"

    def test_file_image_stripped(self):
        wikitext = "[[File:Grand_Canyon.jpg|thumb|The Grand Canyon, Arizona]]\nGeology text."
        chunks = chunk_article("T", wikitext)
        assert chunks
        text = chunks[0]["text"]
        assert "thumb" not in text
        assert "Grand Canyon, Arizona" not in text
        assert "Geology text" in text

    def test_image_namespace_stripped(self):
        wikitext = "[[Image:foo.jpg|200px|left|A caption here]]\nSome content."
        chunks = chunk_article("T", wikitext)
        assert chunks
        text = chunks[0]["text"]
        assert "200px" not in text
        assert "caption" not in text

    def test_media_namespace_stripped(self):
        wikitext = "[[Media:audio.ogg|Listen here]]\nArticle text."
        chunks = chunk_article("T", wikitext)
        assert chunks
        assert "Listen here" not in chunks[0]["text"]

    def test_normal_wikilinks_preserved(self):
        wikitext = "See [[Python (programming language)|Python]] for details."
        chunks = chunk_article("T", wikitext)
        assert chunks
        assert "Python" in chunks[0]["text"]
