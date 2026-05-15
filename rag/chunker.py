"""Wikitext article chunker for the RAG pipeline.

Splits raw wikitext into plain-text chunks at section boundaries.
Uses regex for section detection (much faster than mwparserfromhell.get_sections)
and mwparserfromhell.strip_code() to convert each section fragment to plain text.
"""

import re

import mwparserfromhell

from rag.math import normalize_math
from rag.tables import extract_infoboxes, extract_tables

_SECTION_RE = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$", re.MULTILINE)
_CAT_RE = re.compile(r"\[\[Category:([^\]|]+)", re.IGNORECASE)
_REDIRECT_RE = re.compile(r"^\s*#\s*REDIRECT\s*\[\[", re.IGNORECASE)
_FILE_PREFIXES = ("file:", "image:", "media:")

# Empirically calibrated against nomic-embed-text on simplewiki via
# scripts/calibrate_chunks.py: real ratio is ~4.8 chars/token at the cap, so
# 1600 chars yields p95 ≈ 304 tokens / max ≈ 415 tokens — well inside the
# model's ~512-token quality sweet spot while keeping retrieval granular.
MAX_CHUNK_CHARS = 1600


def extract_categories(wikitext: str) -> list[str]:
    """Return category names found in wikitext, stripping sort keys.

    Args:
        wikitext: Raw wikitext string for an article.

    Returns:
        List of category name strings (e.g. ``['History', 'Science']``).
    """
    return [m.group(1).strip() for m in _CAT_RE.finditer(wikitext)]


def is_redirect(wikitext: str) -> bool:
    """Return True if this article is a #REDIRECT stub.

    Args:
        wikitext: Raw wikitext string for an article.

    Returns:
        True if the article begins with a ``#REDIRECT`` directive.
    """
    return bool(_REDIRECT_RE.match(wikitext))


def _strip_wikitext(raw: str) -> str:
    """Convert a wikitext fragment to plain text via mwparserfromhell.

    Args:
        raw: Raw wikitext string for a single section fragment.

    Returns:
        Plain text with markup stripped and whitespace trimmed. Falls back
        to ``raw.strip()`` on malformed input that raises a parser error.
    """
    try:
        parsed = mwparserfromhell.parse(raw)
        for wl in parsed.filter_wikilinks():
            if str(wl.title).lower().startswith(_FILE_PREFIXES):
                parsed.remove(wl)
        return parsed.strip_code().strip()
    except (ValueError, AttributeError):
        # mwparserfromhell can raise these on severely malformed input
        return raw.strip()


def _split_long_text(text: str, max_chars: int) -> list[str]:
    """Split text into parts of at most max_chars, breaking at paragraph boundaries.

    Paragraph boundaries (double newlines) are preferred split points.
    A single paragraph that exceeds max_chars is hard-split at max_chars.

    Args:
        text: Plain text to split.
        max_chars: Maximum character length of each returned part.

    Returns:
        List of text parts, each at most max_chars characters.
    """
    if len(text) <= max_chars:
        return [text]

    parts = []
    paragraphs = text.split("\n\n")
    current = ""
    for para in paragraphs:
        candidate = (current + "\n\n" + para).lstrip() if current else para
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                parts.append(current)
            # If a single paragraph exceeds max_chars, hard-split it
            if len(para) > max_chars:
                for i in range(0, len(para), max_chars):
                    parts.append(para[i : i + max_chars])
                current = ""
            else:
                current = para
    if current:
        parts.append(current)
    return parts


def chunk_article(
    title: str,
    wikitext: str,
    max_chars: int = MAX_CHUNK_CHARS,
) -> list[dict[str, str | int | None]]:
    """Split an article into plain-text chunks at section boundaries.

    Args:
        title: Article title (reserved for future use; not written to output).
        wikitext: Raw wikitext for the full article.
        max_chars: Maximum character length per chunk.

    Returns:
        List of dicts, each with keys:
            ``section`` (str | None): Heading text; None for the lead section.
            ``chunk_index`` (int): 0-based sub-index within that section.
            ``text`` (str): Plain text ready to embed.
            ``chunk_type`` (str): One of ``'prose'``, ``'table'``, ``'infobox'``.
        Returns ``[]`` for redirect articles and articles that produce no text.
    """
    if is_redirect(wikitext):
        return []

    # Inline math/chem constructs so strip_code() doesn't drop their bodies.
    wikitext = normalize_math(wikitext)

    infobox_chunks = extract_infoboxes(wikitext, title)

    # Find all heading positions
    matches = list(_SECTION_RE.finditer(wikitext))

    # Build (section_name, raw_fragment) pairs
    segments: list[tuple[str | None, str]] = []
    if not matches:
        segments.append((None, wikitext))
    else:
        lead = wikitext[: matches[0].start()]
        if lead.strip():
            segments.append((None, lead))
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(wikitext)
            fragment = wikitext[m.end() : end]
            segments.append((m.group(2), fragment))

    chunks: list[dict] = []
    chunks.extend(infobox_chunks)

    for section, fragment in segments:
        table_chunks, fragment = extract_tables(fragment, section)
        chunks.extend(table_chunks)

        plain = _strip_wikitext(fragment)
        if not plain:
            continue
        # Collapse excess blank lines that may appear after table lines are removed.
        plain = re.sub(r"\n{3,}", "\n\n", plain)
        parts = _split_long_text(plain, max_chars)
        for idx, part in enumerate(parts):
            if part:
                chunks.append(
                    {
                        "section": section,
                        "chunk_index": idx,
                        "text": part,
                        "chunk_type": "prose",
                    }
                )
    return chunks
