"""Single source for the arXiv embed-text format.

Lives in its own module so the format is documented in exactly one place
and can be swapped (e.g. for an A/B against title+abstract-only) by editing
one function. ``format_embed_text`` returns the *plain* text that gets
stored in ``papers_meta.embed_text`` and FTS-indexed. The
``search_document:`` prefix that nomic-embed-text expects is applied
on-the-fly at embed time by ``arxiv.embed`` — it has no business inside
the FTS index.
"""

from typing import Any


def format_embed_text(paper: dict[str, Any]) -> str:
    """Return the plain-text body indexed and embedded for ``paper``.

    Shape: ``"{title}\\n\\n{abstract}\\n\\nCategories: {categories}"``.

    Args:
        paper: Dict with at minimum ``title``, ``abstract``, ``categories``.
    """
    return f"{paper['title']}\n\n{paper['abstract']}\n\nCategories: {paper['categories']}"
