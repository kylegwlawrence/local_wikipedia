"""Tests for arxiv/chunker.py — section-aware markdown splitting."""

import pathlib

import pytest

from arxiv.chunker import chunk_paper
from arxiv.render import html_to_markdown

FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures" / "arxiv"


class TestEmptyAndEdge:
    def test_empty_input(self):
        assert chunk_paper("") == []

    def test_whitespace_only(self):
        assert chunk_paper("   \n\n  \n") == []

    def test_no_headings_yields_single_unnamed_chunk(self):
        out = chunk_paper("Just some prose.\n\nMore prose.")
        assert len(out) == 1
        assert out[0]["section"] == ""
        assert out[0]["chunk_index"] == 0
        assert "Just some prose." in out[0]["text"]


class TestSectionSplitting:
    def test_splits_on_h2(self):
        md = "## Alpha\n\nFirst body.\n\n## Beta\n\nSecond body."
        out = chunk_paper(md)
        sections = [c["section"] for c in out]
        assert sections == ["Alpha", "Beta"]

    def test_splits_on_h3_and_h4(self):
        md = "## A\n\nbody1\n\n### B\n\nbody2\n\n#### C\n\nbody3"
        out = chunk_paper(md)
        assert [c["section"] for c in out] == ["A", "B", "C"]

    def test_lead_before_first_heading_becomes_unnamed_section(self):
        md = "Preamble text.\n\n## First\n\nbody"
        out = chunk_paper(md)
        assert out[0]["section"] == ""
        assert "Preamble text." in out[0]["text"]
        assert out[1]["section"] == "First"

    def test_empty_section_skipped(self):
        md = "## A\n\n## B\n\nReal body."
        out = chunk_paper(md)
        # Section A has no body — should be dropped, not yield an empty chunk.
        assert [c["section"] for c in out] == ["B"]


class TestChunkIndex:
    def test_chunk_index_resets_per_section(self):
        # Two sections, each forced to split into multiple parts.
        para = "x" * 400
        body = "\n\n".join([para] * 5)  # ~2000 chars > 1600 max
        md = f"## Alpha\n\n{body}\n\n## Beta\n\n{body}"
        out = chunk_paper(md)
        alpha = [c for c in out if c["section"] == "Alpha"]
        beta = [c for c in out if c["section"] == "Beta"]
        assert [c["chunk_index"] for c in alpha] == list(range(len(alpha)))
        assert [c["chunk_index"] for c in beta] == list(range(len(beta)))

    def test_short_section_is_single_chunk(self):
        out = chunk_paper("## A\n\nShort body.")
        assert len(out) == 1
        assert out[0]["chunk_index"] == 0


class TestLongSectionSplit:
    def test_long_section_split_at_paragraph_boundary(self):
        para = "x" * 500
        body = "\n\n".join([para] * 5)
        out = chunk_paper(f"## A\n\n{body}", max_chars=1200)
        assert len(out) > 1
        for c in out:
            assert len(c["text"]) <= 1200

    def test_respects_custom_max_chars(self):
        body = "alpha. " * 200  # ~1400 chars
        out = chunk_paper(f"## A\n\n{body}", max_chars=300)
        assert all(len(c["text"]) <= 300 for c in out)
        assert len(out) > 1


class TestHeadingEdgeCases:
    def test_heading_with_inline_math(self):
        md = "## Section $\\alpha$ details\n\nbody"
        out = chunk_paper(md)
        assert out[0]["section"] == "Section $\\alpha$ details"

    def test_h1_not_treated_as_heading(self):
        # h1 is paper title (already in metadata) — our render.py drops it,
        # but if one leaks through it should not create a section boundary.
        md = "# Paper title\n\n## Real section\n\nbody"
        out = chunk_paper(md)
        # The first section is "Real section"; "Paper title" lands in the lead.
        assert out[-1]["section"] == "Real section"

    def test_trailing_whitespace_in_heading_stripped(self):
        out = chunk_paper("## Trailing space   \n\nbody")
        assert out[0]["section"] == "Trailing space"


@pytest.mark.skipif(
    not (FIXTURE_DIR / "2310.06825.html").exists(),
    reason="real arxiv fixture not present",
)
class TestRealMistralPaper:
    @pytest.fixture
    def chunks(self):
        html = (FIXTURE_DIR / "2310.06825.html").read_text(encoding="utf-8")
        md = html_to_markdown(html)
        return chunk_paper(md)

    def test_produces_chunks(self, chunks):
        assert len(chunks) > 0

    def test_covers_expected_sections(self, chunks):
        names = {c["section"] for c in chunks}
        for s in ("Abstract", "Introduction", "Architectural details", "Results", "Conclusion"):
            assert s in names, f"missing section: {s}"

    def test_no_chunk_exceeds_max(self, chunks):
        for c in chunks:
            assert len(c["text"]) <= 1600

    def test_chunk_index_starts_at_zero_per_section(self, chunks):
        seen_sections: dict[str, list[int]] = {}
        for c in chunks:
            seen_sections.setdefault(c["section"], []).append(c["chunk_index"])
        for section, indices in seen_sections.items():
            assert indices == list(range(len(indices))), f"non-contiguous indices for {section}: {indices}"
