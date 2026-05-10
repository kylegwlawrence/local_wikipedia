"""FastAPI web app for browsing the local Wikipedia SQLite database.

Serves three things:
- ``GET /`` — the single-page UI (search box + result/article containers).
- ``GET /search?q=...`` — an HTML fragment listing matching article titles,
  intended to be swapped into the page by HTMX.
- ``GET /article/{title}`` — an HTML fragment with the article rendered
  from wikitext to HTML.

The app reads from the SQLite database produced by ``parse.cli`` and uses
``render.convert_wikitext_to_html`` for conversion. The database path can be
overridden with the ``WIKI_DB`` environment variable, which is what the tests
use to point at a temporary fixture database.
"""
import os
import pathlib
import re
import sqlite3

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import db as wiki_db
from paths import BASE_DIR, DEFAULT_WIKI, db_path_for
from render import convert_wikitext_to_html

DEFAULT_DB = db_path_for(DEFAULT_WIKI)

# Cap search results so the dropdown stays manageable and the LIKE scan
# can stop early.
SEARCH_LIMIT = 20

# Cap redirect-chain following so a cycle can't hang the request. MediaWiki's
# own limit is 5 hops; matching that is conservative.
REDIRECT_MAX_HOPS = 5

# Match a wikitext redirect line, e.g. ``#REDIRECT [[Target]]``. MediaWiki
# accepts case-insensitive ``REDIRECT`` and ignores leading whitespace.
_REDIRECT_RE = re.compile(
    r"^\s*#\s*REDIRECT\s*\[\[\s*([^\]\|#]+?)\s*(?:#[^\]\|]*)?(?:\|[^\]]*)?\s*\]\]",
    re.IGNORECASE,
)

app = FastAPI(title="Local Wikipedia")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def _db_path() -> pathlib.Path:
    """Return the SQLite database path, honoring the ``WIKI_DB`` env var.

    Reading the env var on each call (rather than at import time) lets tests
    point the app at a temporary fixture database via ``monkeypatch.setenv``
    after the app object has already been constructed.
    """
    return pathlib.Path(os.environ.get("WIKI_DB", DEFAULT_DB))


def _connect() -> sqlite3.Connection:
    """Open a per-request SQLite connection with row-dict access.

    Returns:
        A new ``sqlite3.Connection`` whose ``row_factory`` is set to
        ``sqlite3.Row`` so callers can index columns by name.

    Raises:
        HTTPException: 503 if the configured database file does not exist.
            This surfaces a clear error in the UI when a user starts the
            web app before running the parser.
    """
    path = _db_path()
    if not path.exists():
        raise HTTPException(status_code=503, detail=f"Database not found: {path}")
    return wiki_db.connect(path)


def _escape_like(s: str) -> str:
    """Escape SQL LIKE wildcards (% _) and the escape char itself.

    Without this, a query like ``foo_bar`` would silently match ``foo bar``,
    ``foo-bar``, etc. since ``_`` is a single-char wildcard in SQL LIKE.
    Paired with ``ESCAPE '\\'`` in the query.
    """
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_titles(q: str) -> list[str]:
    """Find article titles matching ``q``, preferring prefix matches.

    Strategy: run a fast prefix query (``title LIKE 'q%'``) first — this hits
    ``idx_articles_title`` directly. If that returns fewer than ``SEARCH_LIMIT``
    rows, fall back to a substring query (``title LIKE '%q%'``) for the
    remainder, excluding titles already returned by the prefix pass.

    Args:
        q: Raw search query from the user; whitespace is trimmed and SQL
            LIKE wildcards (``%`` and ``_``) are escaped.

    Returns:
        Up to ``SEARCH_LIMIT`` titles in alphabetical order within each pass.
    """
    q = q.strip()
    if not q:
        return []
    needle = _escape_like(q)

    conn = _connect()
    try:
        # Prefix pass: indexed, sub-millisecond on ~395k rows.
        cur = conn.execute(
            "SELECT title FROM articles WHERE title LIKE ? ESCAPE '\\' "
            "ORDER BY title LIMIT ?",
            (needle + "%", SEARCH_LIMIT),
        )
        rows = [r["title"] for r in cur.fetchall()]

        # Substring fallback: only run if the prefix pass didn't fill the
        # quota. The NOT LIKE clause prevents duplicates between passes.
        if len(rows) < SEARCH_LIMIT:
            seen = set(rows)
            cur = conn.execute(
                "SELECT title FROM articles WHERE title LIKE ? ESCAPE '\\' "
                "AND title NOT LIKE ? ESCAPE '\\' ORDER BY title LIMIT ?",
                (f"%{needle}%", needle + "%", SEARCH_LIMIT - len(rows)),
            )
            for r in cur.fetchall():
                if r["title"] not in seen:
                    rows.append(r["title"])
        return rows
    finally:
        conn.close()


def _fetch_article(title: str) -> tuple[sqlite3.Row | None, str | None]:
    """Look up an article by title, following ``#REDIRECT`` chains.

    A third of the rows in a typical enwiki dump are redirect stubs whose
    ``text_content`` is just ``#REDIRECT [[Target]]``. Follow those up to
    ``REDIRECT_MAX_HOPS`` times so the user sees the real target article.

    Args:
        title: The article's exact title (case-sensitive, matching what the
            search endpoint returned).

    Returns:
        A tuple ``(row, redirected_from)`` where ``row`` is the resolved
        article row (or ``None`` if not found / redirect target missing),
        and ``redirected_from`` is the *original* title the caller asked
        for if at least one redirect was followed, otherwise ``None``.
    """
    conn = _connect()
    try:
        original_title = title
        seen: set[str] = set()
        for _ in range(REDIRECT_MAX_HOPS + 1):
            if title in seen:
                break  # cycle
            seen.add(title)
            cur = conn.execute(
                "SELECT title, text_content, text_bytes, timestamp "
                "FROM articles WHERE title = ?",
                (title,),
            )
            row = cur.fetchone()
            if row is None:
                return None, None
            target = _redirect_target(row["text_content"])
            if target is None:
                redirected_from = original_title if title != original_title else None
                return row, redirected_from
            title = target
        # Hit the hop cap — bail out and return what we have.
        return None, None
    finally:
        conn.close()


def _redirect_target(text_content: str | None) -> str | None:
    """Return the redirect target if ``text_content`` is a redirect stub.

    MediaWiki redirects look like ``#REDIRECT [[Target]]`` (case-insensitive)
    optionally followed by a section anchor or display label which we drop
    since neither affects the resolved article.
    """
    if not text_content:
        return None
    m = _REDIRECT_RE.match(text_content)
    if not m:
        return None
    target = m.group(1).strip()
    # MediaWiki capitalises the first letter of every page title.
    if target:
        target = target[:1].upper() + target[1:]
    return target or None


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    """Render the single-page UI shell (search box + empty result containers).

    Args:
        request: FastAPI request, required by Jinja2's ``TemplateResponse``.

    Returns:
        The full ``index.html`` page. HTMX takes over from here and swaps
        fragments into ``#results`` and ``#article`` without reloading.
    """
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = "") -> HTMLResponse:
    """Return an HTML fragment listing titles that match the query.

    Designed as the target of an ``hx-get`` from the search input. The
    fragment is swapped into the ``#results`` container so the user sees
    matches update as they type.

    Args:
        request: FastAPI request used by Jinja2.
        q: The user's search string; defaults to empty so the endpoint
            behaves cleanly when called without a parameter.

    Returns:
        Rendered ``search_results.html`` partial. An empty ``q`` produces
        an empty list rather than an error.
    """
    titles = _search_titles(q)
    return templates.TemplateResponse(
        request, "search_results.html", {"titles": titles, "q": q}
    )


@app.get("/wikitext/{title:path}", response_class=HTMLResponse)
def wikitext(request: Request, title: str) -> HTMLResponse:
    """Return the raw wikitext for an article, bypassing the Markdown pipeline.

    Paired with ``/article/{title}`` — the two endpoints toggle between the
    rendered and raw views by swapping each other's fragment into ``#article``.

    Args:
        request: FastAPI request used by Jinja2.
        title: Exact article title taken from the URL path.

    Returns:
        Rendered ``wikitext.html`` partial containing the raw wikitext in a
        ``<pre>`` block.

    Raises:
        HTTPException: 404 when no article with that exact title exists.
    """
    row, _redirected_from = _fetch_article(title)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Article not found: {title}")

    return templates.TemplateResponse(
        request,
        "wikitext.html",
        {
            "title": row["title"],
            "wikitext": row["text_content"],
            "text_bytes": row["text_bytes"],
            "timestamp": row["timestamp"],
        },
    )


@app.get("/article/{title:path}", response_class=HTMLResponse)
def article(request: Request, title: str) -> HTMLResponse:
    """Render a single article as HTML.

    The conversion pipeline is:
        SQLite ``text_content`` (wikitext)
            → ``convert_wikitext_to_html`` (converts to HTML directly)

    The ``{title:path}`` converter is used so titles containing slashes —
    rare but legal in MediaWiki — round-trip correctly.

    Args:
        request: FastAPI request used by Jinja2.
        title: Exact article title taken from the URL path.

    Returns:
        Rendered ``article.html`` partial containing the article body and
        small metadata header (size and last-edit timestamp).

    Raises:
        HTTPException: 404 when no article with that exact title exists.
    """
    row, redirected_from = _fetch_article(title)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Article not found: {title}")

    html = convert_wikitext_to_html(row["text_content"])

    return templates.TemplateResponse(
        request,
        "article.html",
        {
            "title": row["title"],
            "html": html,
            "text_bytes": row["text_bytes"],
            "timestamp": row["timestamp"],
            "redirected_from": redirected_from,
        },
    )
