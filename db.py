"""SQLite connection helper and redirect resolution shared across the app."""
import pathlib
import re
import sqlite3


def connect(path: pathlib.Path) -> sqlite3.Connection:
    """Open a SQLite connection with ``sqlite3.Row`` row factory."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


# Match a wikitext redirect line, e.g. ``#REDIRECT [[Target]]``. MediaWiki
# accepts case-insensitive ``REDIRECT`` and ignores leading whitespace. The
# optional section anchor and display label are dropped — neither affects the
# resolved article.
_REDIRECT_RE = re.compile(
    r"^\s*#\s*REDIRECT\s*\[\[\s*([^\]\|#]+?)\s*(?:#[^\]\|]*)?(?:\|[^\]]*)?\s*\]\]",
    re.IGNORECASE,
)


def normalize_title(title: str) -> str:
    """Capitalise the first letter of a title (MediaWiki convention)."""
    return title[:1].upper() + title[1:]


def redirect_target(text_content: str | None) -> str | None:
    """Return the redirect target if ``text_content`` is a redirect stub.

    The first letter is capitalised to match MediaWiki's title-normalisation
    convention.
    """
    if not text_content:
        return None
    m = _REDIRECT_RE.match(text_content)
    if not m:
        return None
    target = m.group(1).strip()
    if target:
        target = normalize_title(target)
    return target or None


def resolve_redirect(
    conn: sqlite3.Connection, title: str, max_hops: int = 5
) -> str | None:
    """Follow ``#REDIRECT`` chains and return the canonical article title.

    Returns ``None`` if the title is not found, the chain exceeds ``max_hops``,
    or a cycle is detected. Only ``text_content`` is fetched at each hop so the
    chain walk is cheap.
    """
    seen: set[str] = set()
    for _ in range(max_hops + 1):
        if title in seen:
            return None
        seen.add(title)
        row = conn.execute(
            "SELECT text_content FROM articles WHERE title = ?", (title,)
        ).fetchone()
        if row is None:
            return None
        target = redirect_target(row["text_content"])
        if target is None:
            return title
        title = target
    return None
