"""Extract internal article wikilinks from raw wikitext.

Used by the "Embed + links" feature: given a source article's wikitext, return
the list of other article titles it links to so they can be queued for
embedding. Also exposes ``count_unembedded_1hop`` / ``count_unembedded_2hop``
for the in-button "(N)" badges, which count link targets that are not yet
present in the RAG database's ``articles_meta`` table.

``mwparserfromhell.parse(wikitext).filter_wikilinks()`` descends recursively
into templates (infoboxes, citations, navigation boxes, table cells), so links
buried inside ``{{infobox}}`` parameters or ``{| ... |}`` tables are included
without special handling here.
"""

import re
import sqlite3

import mwparserfromhell

from db import normalize_title, resolve_redirect
from paths import REDIRECT_MAX_HOPS
from rag.chunker import is_redirect

# SQLite caps host parameters per statement (``SQLITE_MAX_VARIABLE_NUMBER``);
# 500 leaves plenty of headroom on every platform / build flag combination.
_IN_BATCH = 500

# Regex used by the fast (approximate) count path. Captures only the target
# portion of ``[[Target]]`` / ``[[Target|Label]]`` / ``[[Target#anchor]]``.
# Deliberately does NOT recurse into template invocations the way
# ``mwparserfromhell`` does — full parsing of enwiki hub articles is 50–100×
# slower and made the 2-hop count take minutes. Missing template-embedded
# links is acceptable for a count badge: a slight under-count is fine, a UI
# that never updates is not.
_WIKILINK_RE = re.compile(r"\[\[\s*([^\[\]|\n#]+)")

# MediaWiki namespaces that aren't real articles. Anything starting with one of
# these prefixes is skipped. The list covers the common-and-confusing cases
# across enwiki, simplewiki, and most language editions.
_NAMESPACE_PREFIXES: tuple[str, ...] = (
    "Category:",
    "File:",
    "Image:",
    "Media:",
    "Help:",
    "User:",
    "User talk:",
    "Talk:",
    "Wikipedia:",
    "WP:",
    "Template:",
    "Template talk:",
    "Portal:",
    "Special:",
    "Module:",
    "MediaWiki:",
    "Book:",
    "Draft:",
    "TimedText:",
    "Education Program:",
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


def _extract_links_fast(wikitext: str, source_title: str | None = None) -> list[str]:
    """Regex-based wikilink extraction — fast approximation of ``extract_article_links``.

    Used by the count helpers (``count_unembedded_*``) where exact accuracy
    matters less than completing in under a second on enwiki hub articles.
    Returns titles in first-encounter order with duplicates removed; misses
    links nested inside template invocations (parse those with
    ``extract_article_links`` if you need them).
    """
    if not wikitext:
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for m in _WIKILINK_RE.finditer(wikitext):
        raw = m.group(1).strip()
        if not raw:
            continue
        target = normalize_title(raw)
        if _is_excluded(target):
            continue
        if source_title and target == source_title:
            continue
        if target in seen:
            continue
        seen.add(target)
        ordered.append(target)
    return ordered


def _resolve_and_dedup(
    wiki_conn: sqlite3.Connection,
    raw_targets: list[str],
    source_title: str,
) -> list[str]:
    """Canonicalise ``raw_targets`` via redirect chains and dedup.

    Mirrors the enqueue logic in ``app/routes/embeddings.py::_enqueue_links``:
    when ``resolve_redirect`` returns ``None`` (target not in DB / cycle / past
    max hops) we keep the raw target as the candidate, so unresolvable links
    still register as "to embed". ``source_title`` is always excluded.
    """
    result: list[str] = []
    seen: set[str] = {source_title}
    for t in raw_targets:
        canonical = resolve_redirect(wiki_conn, t, REDIRECT_MAX_HOPS) or t
        if canonical in seen:
            continue
        seen.add(canonical)
        result.append(canonical)
    return result


def _embedded_titles(rag_conn: sqlite3.Connection, candidates: list[str]) -> set[str]:
    """Return the subset of ``candidates`` already present in ``articles_meta``.

    Batched with an ``IN`` clause so the cost stays one SQLite roundtrip per
    ``_IN_BATCH`` candidates rather than one per title.
    """
    embedded: set[str] = set()
    for i in range(0, len(candidates), _IN_BATCH):
        chunk = candidates[i : i + _IN_BATCH]
        placeholders = ",".join("?" * len(chunk))
        rows = rag_conn.execute(
            f"SELECT title FROM articles_meta WHERE title IN ({placeholders})",
            chunk,
        ).fetchall()
        embedded.update(r["title"] for r in rows)
    return embedded


def _filter_embeddable(wiki_conn: sqlite3.Connection, candidates: set[str]) -> set[str]:
    """Return the subset of ``candidates`` the worker would actually embed.

    Drops titles the embed worker would mark ``not_found`` (no row in the wiki
    DB) or ``skipped_redirect`` (the article body is a ``#REDIRECT`` stub) —
    neither produces a new embedding, so neither should inflate the count
    shown on the "Embed + links" / "Embed + links²" badges.

    Only the first 100 characters of ``text_content`` are pulled per row: the
    redirect regex anchors at the start of the string, and avoiding the full
    article body keeps the scan cheap when ``candidates`` runs into the
    thousands on hub articles.
    """
    if not candidates:
        return set()
    candidate_list = list(candidates)
    keep: set[str] = set()
    for i in range(0, len(candidate_list), _IN_BATCH):
        chunk = candidate_list[i : i + _IN_BATCH]
        placeholders = ",".join("?" * len(chunk))
        rows = wiki_conn.execute(
            f"SELECT title, substr(text_content, 1, 100) AS prefix "
            f"FROM articles WHERE title IN ({placeholders})",
            chunk,
        ).fetchall()
        for r in rows:
            if not is_redirect(r["prefix"] or ""):
                keep.add(r["title"])
    return keep


def count_unembedded_1hop(
    wiki_conn: sqlite3.Connection,
    rag_conn: sqlite3.Connection | None,
    source_title: str,
    wikitext: str,
) -> int:
    """Count 1-hop wikilink targets the worker would actually embed.

    This is what the "Embed + links" button's ``(N)`` badge displays. Excludes
    candidates the worker would skip — articles missing from the wiki DB
    (``not_found``), redirect stubs (``skipped_redirect``), and ones already
    present in ``articles_meta`` (``skipped_unchanged``) — so the count
    reflects only new work the click would actually do.

    Uses the fast regex extractor (see ``_extract_links_fast``) for speed;
    the value is a close approximation, not an exact match of what the worker
    would enqueue.
    """
    raw = _extract_links_fast(wikitext, source_title=source_title)
    candidates = _resolve_and_dedup(wiki_conn, raw, source_title)
    if not candidates:
        return 0
    embeddable = _filter_embeddable(wiki_conn, set(candidates))
    if not embeddable:
        return 0
    if rag_conn is None:
        return len(embeddable)
    embedded = _embedded_titles(rag_conn, list(embeddable))
    return len(embeddable) - len(embedded)


def count_unembedded_2hop(
    wiki_conn: sqlite3.Connection,
    rag_conn: sqlite3.Connection | None,
    source_title: str,
    wikitext: str,
) -> int:
    """Count the union of 1-hop and 2-hop link targets the worker would embed.

    This is what the "Embed + links²" button's ``(N)`` badge displays. Excludes
    candidates the worker would skip — articles missing from the wiki DB
    (``not_found``), redirect stubs (``skipped_redirect``), and ones already
    present in ``articles_meta`` (``skipped_unchanged``) — so the count reflects
    only new work the click would actually do.

    Uses the fast regex extractor — the alternative (mwparserfromhell on
    hundreds of hub articles) pushed this past 2 minutes for "United States"
    on enwiki; the regex brings that under a second at the cost of missing
    template-nested links.
    """
    raw_hop1 = _extract_links_fast(wikitext, source_title=source_title)
    hop1 = _resolve_and_dedup(wiki_conn, raw_hop1, source_title)
    if not hop1:
        return 0

    # Pull the 1-hop neighbours' full wikitext: it's needed both to filter out
    # not_found / redirect hop1 entries and to extract their wikilinks for
    # hop2 expansion.
    parent_wikitext: dict[str, str] = {}
    for i in range(0, len(hop1), _IN_BATCH):
        chunk = hop1[i : i + _IN_BATCH]
        placeholders = ",".join("?" * len(chunk))
        rows = wiki_conn.execute(
            f"SELECT title, text_content FROM articles WHERE title IN ({placeholders})",
            chunk,
        ).fetchall()
        for r in rows:
            parent_wikitext[r["title"]] = r["text_content"]

    candidates: set[str] = set()
    hop2_only: set[str] = set()
    for parent_title in hop1:
        text = parent_wikitext.get(parent_title)
        if not text or is_redirect(text):
            # not_found (no row) or skipped_redirect — worker wouldn't embed
            # the parent and wouldn't expand from its body either.
            continue
        candidates.add(parent_title)
        for t in _extract_links_fast(text, source_title=parent_title):
            # Skip redirect resolution here: resolving ~80k–180k individual
            # titles via SQLite took 20–110 s on hub articles. The count badge
            # is already an approximation; a slight over-count is fine.
            if t == source_title or t in candidates:
                continue
            hop2_only.add(t)

    # Drop hop2 candidates the worker would skip (not_found / redirect).
    candidates |= _filter_embeddable(wiki_conn, hop2_only)

    if rag_conn is None:
        return len(candidates)
    candidate_list = list(candidates)
    embedded = _embedded_titles(rag_conn, candidate_list)
    return len(candidates) - len(embedded)


def recompute_link_counts(
    rag_conn: sqlite3.Connection,
    wiki_conn: sqlite3.Connection,
    title: str,
    wikitext: str,
    *,
    include_2hop: bool,
) -> None:
    """Cache 1-hop (and optionally 2-hop) unembedded-link counts on ``articles_meta``.

    Called from two trigger sites:
      * ``rag.embed.embed_one`` — right after a single-article embed (1-hop
        only; 2-hop is too expensive for a synchronous request).
      * ``workers.embed._finalize_links_embedded`` — once a job's queue drains
        (both 1-hop AND 2-hop, for each distinct ``source_title``).

    Best-effort: exceptions are swallowed and logged. The cache is allowed to
    go stale (over-count) when neighbours get embedded outside one of these
    trigger sites; clicking Embed+Links on the affected source refreshes it.
    """
    try:
        n1 = count_unembedded_1hop(wiki_conn, rag_conn, title, wikitext)
        if include_2hop:
            n2 = count_unembedded_2hop(wiki_conn, rag_conn, title, wikitext)
            rag_conn.execute(
                "UPDATE articles_meta "
                "SET unembedded_link_count_1hop = ?, "
                "    unembedded_link_count_2hop = ? "
                "WHERE title = ?",
                (n1, n2, title),
            )
        else:
            rag_conn.execute(
                "UPDATE articles_meta SET unembedded_link_count_1hop = ? WHERE title = ?",
                (n1, title),
            )
        rag_conn.commit()
    except Exception as exc:
        print(
            f"[recompute_link_counts] {title!r}: {type(exc).__name__}: {exc}",
            flush=True,
        )
