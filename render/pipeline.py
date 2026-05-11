"""Top-level wikitext → HTML pipeline.

The pipeline is a fixed sequence of stages whose order is load-bearing:

  1. wikicode-level template handlers (templates.py): infobox, math, code,
     lang, indicator, section-link, citation, ref collection, reflist
  2. wikicode-level stripping (strip.py): templates, refs, comments, categories
  3. flatten to string
  4. extract code/math blocks behind placeholders (protect.py)
  5. block-level converters: tables, lists, headings (tables.py + blocks.py)
  6. inline converters: bold/italic, links (inline.py)
  7. paragraph wrapping (blocks.py)
  8. restore code/math from placeholders
  9. whitespace cleanup
"""
import html as _html
import re

import mwparserfromhell

from render import strip
from render.strip import strip_external_links_section
from render.blocks import convert_headings, convert_lists, wrap_paragraphs
from render.inline import convert_bold_italic, convert_links
from render.protect import (
    extract_math_tags,
    extract_syntaxhighlight,
    restore_code_blocks,
    restore_math_tags,
)
from render.tables import convert_tables
from render.templates import (
    collect_inline_refs,
    convert_annotated_link_templates,
    convert_citation_templates,
    convert_code_templates,
    convert_indicator_templates,
    convert_infobox_templates,
    convert_lang_templates,
    convert_math_templates,
    convert_reflist_template,
    convert_section_link_templates,
    convert_wikidata_templates,
)


def clean_extra_markup(text: str) -> str:
    """Collapse runs of blank lines and trim trailing whitespace per line."""
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text


def convert_wikitext_to_html(wikitext: str) -> str:
    """Convert wikitext to HTML.

    Wikilinks point at the local ``/article/{title}`` endpoint and carry HTMX
    attributes so the front-end loads them as fragment swaps into ``#article``.
    """
    if not wikitext or not wikitext.strip():
        return ""

    try:
        wikicode = mwparserfromhell.parse(wikitext)

        # 1. Wikicode-level templates that produce output (must run before strip).
        convert_wikidata_templates(wikicode)
        convert_annotated_link_templates(wikicode)
        convert_infobox_templates(wikicode)
        convert_math_templates(wikicode)
        convert_code_templates(wikicode)
        convert_lang_templates(wikicode)
        convert_indicator_templates(wikicode)
        convert_section_link_templates(wikicode)
        convert_citation_templates(wikicode)
        collected_refs = collect_inline_refs(wikicode)
        convert_reflist_template(wikicode, collected_refs)

        # 2. Flatten to string.
        text = str(wikicode)

        # 3. Strip remaining noise via regex (faster than wikicode.remove() loops).
        text = strip.strip_comments(text)
        text = strip.strip_refs(text)
        text = strip.strip_templates(text)
        text = strip.strip_categories(text)
        text = strip_external_links_section(text)

        # 4. Protect code/math from later string-level passes.
        text, code_blocks = extract_syntaxhighlight(text)
        text, math_blocks = extract_math_tags(text)

        # 5. Block-level structure first.
        text = convert_tables(text)
        text = convert_lists(text)
        text = convert_headings(text)

        # 6. Inline formatting (tables handle their own inline pass).
        text = convert_bold_italic(text)
        text = convert_links(text)

        # 7. Wrap remaining bare lines in paragraphs.
        text = wrap_paragraphs(text)

        # 8. Restore protected blocks.
        text = restore_code_blocks(text, code_blocks)
        text = restore_math_tags(text, math_blocks)

        # 9. Tidy.

        text = clean_extra_markup(text)
        return text.strip()

    except Exception:
        # Last-ditch fallback: never let a render bug crash the request handler.
        # The article is shown as escaped plaintext so the user sees *something*.
        return f"<p>{_html.escape(wikitext)}</p>"
