"""Extract internal article wikilinks from raw wikitext.

Used by the "Embed + links" feature: given a source article's wikitext, return
the list of other article titles it links to so they can be queued for
embedding.

``mwparserfromhell.parse(wikitext).filter_wikilinks()`` descends recursively
into templates (infoboxes, citations, navigation boxes, table cells), so links
buried inside ``{{infobox}}`` parameters or ``{| ... |}`` tables are included
without special handling here.
"""
import mwparserfromhell

from db import normalize_title


# MediaWiki namespaces that aren't real articles. Anything starting with one of
# these prefixes is skipped. The list covers the common-and-confusing cases
# across enwiki, simplewiki, and most language editions.
_NAMESPACE_PREFIXES: tuple[str, ...] = (
    "Category:", "File:", "Image:", "Media:",
    "Help:", "User:", "User talk:", "Talk:",
    "Wikipedia:", "WP:", "Template:", "Template talk:",
    "Portal:", "Special:", "Module:", "MediaWiki:",
    "Book:", "Draft:", "TimedText:", "Education Program:",
)


def _normalize_target(raw: str) -> str:
    """Strip whitespace, drop ``#anchor``, capitalise first letter."""
    target = raw.partition("#")[0].strip()
    if not target:
        return ""
    return normalize_title(target)


def _is_excluded(target: str) -> bool:
    """True if ``target`` should be skipped (namespaced or interwiki)."""
    if not target:
        return True
    # Interwiki (``[[:de:Foo]]``) and namespace-suppressed links both start
    # with a colon — neither references a local article we can embed.
    if target.startswith(":"):
        return True
    return target.startswith(_NAMESPACE_PREFIXES)


def extract_article_links(wikitext: str, source_title: str | None = None) -> list[str]:
    """Return wikilink targets in ``wikitext`` that point to local articles.

    Args:
        wikitext: Raw article wikitext.
        source_title: If provided, links whose target equals this title are
            dropped as self-links. Must be already capitalised (MediaWiki form).

    Returns:
        Titles in first-encounter order with duplicates removed. Anchors are
        stripped, first-letter-capitalised. Namespaced and interwiki links are
        filtered out. Redirect resolution is the caller's responsibility.
    """
    if not wikitext:
        return []

    wikicode = mwparserfromhell.parse(wikitext)
    seen: set[str] = set()
    ordered: list[str] = []
    for link in wikicode.filter_wikilinks():
        target = _normalize_target(str(link.title))
        if _is_excluded(target):
            continue
        if source_title and target == source_title:
            continue
        if target in seen:
            continue
        seen.add(target)
        ordered.append(target)
    return ordered
