"""Section-aware markdown chunker for arXiv full papers.

Splits the markdown output of ``arxiv/render.py`` into per-section text
chunks suitable for embedding. Splits on every heading (``## … ######``);
material before the first heading is grouped into a section with an empty
name. Sections that exceed ``max_chars`` are split at paragraph (blank-line)
boundaries via the shared helper in ``rag/chunker.py``.

Parallel to ``rag/chunker.py``: same chunk-size ceiling, same paragraph-aware
splitter, same per-section ``chunk_index``. Different content language
(markdown vs. wikitext) and no wiki-specific concepts (no infoboxes,
redirects, or category extraction).
"""

import re

from rag.chunker import MAX_CHUNK_CHARS, split_long_text

_HEADING_RE = re.compile(r"^(#{2,6})\s+(.+?)\s*$", re.MULTILINE)


def chunk_paper(markdown: str, *, max_chars: int = MAX_CHUNK_CHARS) -> list[dict]:
    """Split paper markdown into section-based chunks.

    Returns list of dicts:
        - ``section`` (str): heading text; empty string for pre-first-heading
          material (front-matter, abstract intro, etc.).
        - ``chunk_index`` (int): 0-based within section.
        - ``text`` (str): chunk content, stripped.

    Empty / whitespace-only sections are skipped.
    """
    if not markdown.strip():
        return []

    matches = list(_HEADING_RE.finditer(markdown))

    segments: list[tuple[str, str]] = []
    if not matches:
        segments.append(("", markdown))
    else:
        lead = markdown[: matches[0].start()]
        if lead.strip():
            segments.append(("", lead))
        for i, m in enumerate(matches):
            section_name = m.group(2).strip()
            content_start = m.end()
            content_end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
            segments.append((section_name, markdown[content_start:content_end]))

    chunks: list[dict] = []
    for section, text in segments:
        text = text.strip()
        if not text:
            continue
        text = re.sub(r"\n{3,}", "\n\n", text)
        for idx, part in enumerate(split_long_text(text, max_chars)):
            if part.strip():
                chunks.append({"section": section, "chunk_index": idx, "text": part})
    return chunks
