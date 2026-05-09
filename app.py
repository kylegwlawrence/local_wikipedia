"""FastAPI web app for browsing the local Wikipedia SQLite database.

Serves three things:
- ``GET /`` — the single-page UI (search box + result/article containers).
- ``GET /search?q=...`` — an HTML fragment listing matching article titles,
  intended to be swapped into the page by HTMX.
- ``GET /article/{title}`` — an HTML fragment with the article rendered
  from wikitext to Markdown to HTML.

The app reads from the SQLite database produced by ``parse/parse.py`` and
reuses ``parse.wikitext_to_markdown`` for the wikitext conversion step. The
database path can be overridden with the ``WIKI_DB`` environment variable,
which is what the tests use to point at a temporary fixture database.
"""
import os
import pathlib
import sqlite3

import markdown as md_lib
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from parse.wikitext_to_markdown import convert_wikitext_to_markdown

# Resolve paths relative to this file so the app works regardless of CWD.
BASE_DIR = pathlib.Path(__file__).parent
DEFAULT_DB = BASE_DIR / "dumps" / "enwiki.db"

# Cap search results so the dropdown stays manageable and the LIKE scan
# can stop early.
SEARCH_LIMIT = 20

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


def _wiki_base_url() -> str:
    """Derive the Wikipedia base URL from the database filename.

    Strips the ``wiki`` suffix from the stem to get the language code:
    ``simplewiki`` → ``simple``, ``enwiki`` → ``en``, ``frwiki`` → ``fr``.
    """
    lang = _db_path().stem.removesuffix("wiki") or "en"
    return f"https://{lang}.wikipedia.org/wiki/"


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
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _search_titles(q: str) -> list[str]:
    """Find article titles matching ``q``, preferring prefix matches.

    Strategy: run a fast prefix query (``title LIKE 'q%'``) first — this hits
    ``idx_articles_title`` directly. If that returns fewer than ``SEARCH_LIMIT``
    rows, fall back to a substring query (``title LIKE '%q%'``) for the
    remainder, excluding titles already returned by the prefix pass. This
    gives users prefix-style autocomplete behavior while still surfacing
    mid-title matches when a prefix search comes up short.

    Args:
        q: Raw search query from the user; whitespace is trimmed.

    Returns:
        A list of up to ``SEARCH_LIMIT`` matching titles, in alphabetical
        order within each pass. An empty list is returned for an empty
        query so the UI can render a clean "type to search" state.
    """
    q = q.strip()
    if not q:
        return []

    conn = _connect()
    try:
        # Prefix pass: indexed, sub-millisecond on ~395k rows.
        cur = conn.execute(
            "SELECT title FROM articles WHERE title LIKE ? ORDER BY title LIMIT ?",
            (q + "%", SEARCH_LIMIT),
        )
        rows = [r["title"] for r in cur.fetchall()]

        # Substring fallback: only run if the prefix pass didn't fill the
        # quota. The NOT LIKE clause prevents duplicates between passes.
        if len(rows) < SEARCH_LIMIT:
            seen = set(rows)
            cur = conn.execute(
                "SELECT title FROM articles WHERE title LIKE ? AND title NOT LIKE ? "
                "ORDER BY title LIMIT ?",
                (f"%{q}%", q + "%", SEARCH_LIMIT - len(rows)),
            )
            for r in cur.fetchall():
                if r["title"] not in seen:
                    rows.append(r["title"])
        return rows
    finally:
        conn.close()


def _fetch_article(title: str) -> sqlite3.Row | None:
    """Look up a single article by exact title.

    Args:
        title: The article's exact title (case-sensitive, matching what the
            search endpoint returned).

    Returns:
        A ``sqlite3.Row`` with ``title``, ``text_content``, ``text_bytes``,
        and ``timestamp`` columns, or ``None`` if no row matches.
    """
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT title, text_content, text_bytes, timestamp "
            "FROM articles WHERE title = ?",
            (title,),
        )
        return cur.fetchone()
    finally:
        conn.close()


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


@app.get("/article/{title:path}", response_class=HTMLResponse)
def article(request: Request, title: str) -> HTMLResponse:
    """Render a single article as HTML.

    The conversion pipeline is:
        SQLite ``text_content`` (wikitext)
            → ``convert_wikitext_to_markdown`` (cleans templates/refs, etc.)
            → ``markdown.markdown`` (renders Markdown to HTML)

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
    row = _fetch_article(title)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Article not found: {title}")

    markdown_text = convert_wikitext_to_markdown(row["text_content"], _wiki_base_url())
    # The "extra" extension covers tables/fenced blocks; "sane_lists" stops
    # ordered/unordered lists from bleeding into each other.
    html = md_lib.markdown(markdown_text, extensions=["extra", "sane_lists"])

    return templates.TemplateResponse(
        request,
        "article.html",
        {
            "title": row["title"],
            "html": html,
            "text_bytes": row["text_bytes"],
            "timestamp": row["timestamp"],
        },
    )
