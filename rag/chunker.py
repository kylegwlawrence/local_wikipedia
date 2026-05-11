"""Wikitext article chunker for the RAG pipeline.

Splits raw wikitext into plain-text chunks at section boundaries.
Uses regex for section detection (much faster than mwparserfromhell.get_sections)
and mwparserfromhell.strip_code() to convert each section fragment to plain text.
"""
import re

import mwparserfromhell

_SECTION_RE = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$", re.MULTILINE)
_CAT_RE = re.compile(r"\[\[Category:([^\]|]+)", re.IGNORECASE)
_REDIRECT_RE = re.compile(r"^\s*#\s*REDIRECT\s*\[\[", re.IGNORECASE)

MAX_CHUNK_CHARS = 1600  # ~400 tokens at 4 chars/token


def extract_categories(wikitext: str) -> list[str]:
    """Return category names found in wikitext, stripping sort keys."""
    return [m.group(1).strip() for m in _CAT_RE.finditer(wikitext)]


def is_redirect(wikitext: str) -> bool:
    """Return True if this article is a #REDIRECT stub."""
    return bool(_REDIRECT_RE.match(wikitext))


def _strip_wikitext(raw: str) -> str:
    """Convert a wikitext fragment to plain text via mwparserfromhell."""
    try:
        return mwparserfromhell.parse(raw).strip_code().strip()
    except Exception:
        return raw.strip()


def _split_long_text(text: str, max_chars: int) -> list[str]:
    """Split text into parts of at most max_chars, breaking at paragraph boundaries."""
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
) -> list[dict]:
    """Split an article into plain-text chunks at section boundaries.

    Returns a list of dicts with keys:
        section (str | None) — heading text, None for the lead section
        chunk_index (int)    — 0-based sub-index within that section
        text (str)           — plain text ready to embed

    Returns [] for redirect articles and articles that produce no text.
    """
    if is_redirect(wikitext):
        return []

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
    for section, fragment in segments:
        plain = _strip_wikitext(fragment)
        if not plain:
            continue
        parts = _split_long_text(plain, max_chars)
        for idx, part in enumerate(parts):
            if part.strip():
                chunks.append(
                    {
                        "section": section,
                        "chunk_index": idx,
                        "text": part.strip(),
                    }
                )
    return chunks
