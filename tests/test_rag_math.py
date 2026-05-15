"""Tests for rag/math.py — math/chem normalization for embedding."""

import mwparserfromhell

from rag.chunker import chunk_article
from rag.math import normalize_math


def _stripped(wikitext: str) -> str:
    """Run wikitext through normalize_math + mwparserfromhell.strip_code(),
    mirroring what chunk_article does to each section fragment."""
    return mwparserfromhell.parse(normalize_math(wikitext)).strip_code()


class TestMathTags:
    def test_inline_math_tag_body_preserved(self):
        assert "E=mc^2" in _stripped("Energy <math>E=mc^2</math> is famous.")

    def test_block_math_tag_body_preserved(self):
        out = _stripped('<math display="block">\\int_0^1 x dx</math>')
        assert "\\int_0^1 x dx" in out

    def test_math_tag_with_attributes(self):
        assert "a^2+b^2=c^2" in _stripped('<math class="foo">a^2+b^2=c^2</math>')

    def test_multiple_math_tags(self):
        out = _stripped("First <math>x</math> then <math>y</math>.")
        assert "x" in out and "y" in out

    def test_unclosed_math_tag_left_alone(self):
        # No matching close — regex doesn't fire, source preserved.
        src = "Broken <math>x = 1 in text."
        assert "<math>" in normalize_math(src)


class TestMathTemplates:
    def test_html_math_template(self):
        assert "x^2 + y^2" in _stripped("{{math|x^2 + y^2}}")

    def test_mvar_template(self):
        assert "x" in _stripped("Let {{mvar|x}} be a real number.")

    def test_tmath_template(self):
        # Brace-aware: \frac{a}{b}}} must close on the outer }}.
        assert "\\frac{a}{b}" in _stripped("{{tmath|\\frac{a}{b}}}")

    def test_bigmath_template(self):
        assert "x = 1" in _stripped("{{bigmath|x = 1}}")

    def test_math_block_template(self):
        assert "E=mc^2" in _stripped("{{math block|E=mc^2}}")

    def test_tmath_block_template(self):
        assert "\\sum x_i" in _stripped("{{tmath block|\\sum x_i}}")

    def test_equals_escape_inside_math(self):
        # {{=}} inside an HTML-math body should become a literal =.
        assert "a = b" in _stripped("{{math|a {{=}} b}}")

    def test_empty_math_template_no_crash(self):
        # {{math}} with no body — should normalize cleanly.
        out = _stripped("Text {{math}} more text.")
        assert "Text" in out and "more text" in out

    def test_math_inside_prose(self):
        out = _stripped("Pythagoras: {{math|a^2+b^2=c^2}} is famous.")
        assert "Pythagoras" in out
        assert "a^2+b^2=c^2" in out
        assert "famous" in out


class TestChemTags:
    def test_chem_tag_body_preserved(self):
        assert "H2O" in _stripped("Water is <chem>H2O</chem>.")

    def test_ce_tag_body_preserved(self):
        assert "2H2 + O2 -> 2H2O" in _stripped("<ce>2H2 + O2 -> 2H2O</ce>")

    def test_chem_tag_with_attributes(self):
        assert "CO2" in _stripped('<chem class="foo">CO2</chem>')

    def test_ce_tag_case_insensitive(self):
        # Tags should match regardless of case (<CE> as well as <ce>).
        assert "H2SO4" in _stripped("<CE>H2SO4</CE>")


class TestChemTemplates:
    def test_chem_three_args(self):
        # {{chem|H|2|O}} renders as H₂O on Wikipedia; we want plain "H2O".
        assert "H2O" in _stripped("Water is {{chem|H|2|O}}.")

    def test_chem_single_arg(self):
        assert "NaCl" in _stripped("{{chem|NaCl}}")

    def test_chem2_template(self):
        assert "CO2" in _stripped("{{chem2|CO2}}")

    def test_mhchem_template(self):
        assert "H2SO4" in _stripped("{{mhchem|H2SO4}}")

    def test_ce_template_form(self):
        assert "2H2 + O2 -> 2H2O" in _stripped("{{ce|2H2 + O2 -> 2H2O}}")

    def test_chem_hydrate(self):
        # MgSO4 hydrate: positional concatenation should reconstruct the formula.
        assert "MgSO4" in _stripped("{{chem|MgSO|4|.7H|2|O}}")

    def test_chem_named_param_dropped(self):
        # state=g should be filtered; only positional concatenated.
        out = _stripped("{{ce|H2O|state=g}}")
        assert "H2O" in out
        assert "state=g" not in out

    def test_unclosed_chem_template_left_alone(self):
        # Unbalanced — regex matches the open but closer-scan returns None.
        src = "Broken {{chem|H|2|O"
        assert "{{chem" in normalize_math(src)


class TestChunkerIntegration:
    def test_math_tag_survives_chunk(self):
        chunks = chunk_article(
            "Pythagorean theorem",
            "The theorem states <math>a^2 + b^2 = c^2</math> for any right triangle.",
        )
        assert chunks
        assert any("a^2 + b^2 = c^2" in c["text"] for c in chunks)

    def test_math_template_survives_chunk(self):
        chunks = chunk_article(
            "Mass energy",
            "Einstein's equation {{math|E=mc^2}} relates mass and energy.",
        )
        assert chunks
        text = " ".join(c["text"] for c in chunks)
        assert "E=mc^2" in text
        assert "Einstein" in text  # context preserved

    def test_chem_tag_survives_chunk(self):
        chunks = chunk_article(
            "Water",
            "Water is a compound, <chem>H2O</chem>, essential for life.",
        )
        assert chunks
        assert any("H2O" in c["text"] for c in chunks)

    def test_math_in_infobox_field_value(self):
        wikitext = "{{Infobox physics quantity\n| name = Mass-energy\n| formula = {{math|E=mc^2}}\n}}\nLead text."
        chunks = chunk_article("Mass-energy equivalence", wikitext)
        infobox = [c for c in chunks if c["chunk_type"] == "infobox"]
        assert infobox
        assert any("E=mc^2" in c["text"] for c in infobox)

    def test_math_in_table_cell(self):
        wikitext = '{| class="wikitable"\n|-\n! Name !! Formula\n|-\n| Pythagoras || <math>a^2+b^2=c^2</math>\n|}'
        chunks = chunk_article("Theorems", wikitext)
        table = [c for c in chunks if c["chunk_type"] == "table"]
        assert table
        assert any("a^2+b^2=c^2" in c["text"] for c in table)
