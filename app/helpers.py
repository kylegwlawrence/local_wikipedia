"""Pure helpers used across routes.

These don't manage application lifecycle and depend only on their arguments
(plus module-level constants from ``app.config`` and the ``paths`` module).
"""

import json
import random
import re
import sqlite3
from datetime import UTC, date, datetime

from fastapi import Request, Response
from fastapi.responses import RedirectResponse

import db as wiki_db
from app.config import SEARCH_LIMIT, WIKI_LABELS
from app.deps import connect
from paths import REDIRECT_MAX_HOPS
from rag.chunker import _strip_wikitext
from remote import RemoteSqliteError
from workers.spawn import spawn_worker as _spawn_worker


def wiki_label(wiki: str) -> str:
    """Return the display label for ``wiki``, falling back to ``wiki`` itself."""
    return WIKI_LABELS.get(wiki, wiki)


def escape_fts5(q: str) -> str:
    """Wrap a raw query in FTS5 phrase quotes, escaping internal double-quotes."""
    return '"' + q.replace('"', '""') + '"'


def search_titles(q: str, request: Request) -> list[str]:
    """Find article titles matching ``q`` using the FTS5 trigram index.

    The ``articles_fts`` virtual table uses the ``trigram`` tokenizer, which
    supports both prefix and substring matching in a single indexed query.
    Queries shorter than 3 characters fall back to a prefix LIKE on
    ``idx_articles_title`` because the trigram index requires at least 3 chars.

    Returns up to ``SEARCH_LIMIT`` titles ordered by BM25 relevance rank.
    """
    q = q.strip()
    if not q:
        return []

    conn = connect(request)
    try:
        if len(q) < 3:
            needle = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            cur = conn.execute(
                "SELECT title FROM articles WHERE title LIKE ? ESCAPE '\\' ORDER BY title LIMIT ?",
                (needle + "%", SEARCH_LIMIT),
            )
            return [r["title"] for r in cur.fetchall()]

        cur = conn.execute(
            "SELECT title FROM articles_fts WHERE articles_fts MATCH ? ORDER BY rank LIMIT ?",
            (escape_fts5(q), SEARCH_LIMIT),
        )
        return [r["title"] for r in cur.fetchall()]
    finally:
        conn.close()


def fetch_article(title: str, request: Request) -> tuple[sqlite3.Row | None, str | None]:
    """Look up an article by title, following ``#REDIRECT`` chains.

    A third of the rows in a typical enwiki dump are redirect stubs whose
    ``text_content`` is just ``#REDIRECT [[Target]]``. Follow those up to
    ``REDIRECT_MAX_HOPS`` times so the user sees the real target article.

    Returns ``(row, redirected_from)``; ``redirected_from`` is the *original*
    title if at least one redirect was followed, otherwise ``None``.
    """
    conn = connect(request)
    try:
        original_title = title
        seen: set[str] = set()
        for _ in range(REDIRECT_MAX_HOPS + 1):
            if title in seen:
                break  # cycle
            seen.add(title)
            cur = conn.execute(
                "SELECT title, text_content, text_bytes, timestamp FROM articles WHERE title = ?",
                (title,),
            )
            row = cur.fetchone()
            if row is None:
                return None, None
            target = wiki_db.redirect_target(row["text_content"])
            if target is None:
                redirected_from = original_title if title != original_title else None
                return row, redirected_from
            title = target
        return None, None
    finally:
        conn.close()


# Daily-articles cache keys live in the per-wiki ``db_metadata`` table so
# the picks rotate independently for each wiki.
_DAILY_DATE_KEY = "daily_articles_date"
_DAILY_PAYLOAD_KEY = "daily_articles_payload"

# Lead = everything before the first ``== Heading ==`` line; match any 2–6
# equals heading at the start of a line.
_LEAD_END_RE = re.compile(r"^={2,6}\s*.+?\s*={2,6}\s*$", re.MULTILINE)
# Treat a period as a sentence break only when followed by whitespace + an
# uppercase letter — keeps ``U.S.``, ``Mr.``, ``Inc.`` etc. intact.
_SENTENCE_END_RE = re.compile(r"\.(?=\s+[A-Z])")

_SNIPPET_MAX_CHARS = 240
_SNIPPET_MIN_CHARS = 10

# Skip stubs / very short articles when picking daily features.
_MIN_DAILY_ARTICLE_BYTES = 3000


def _today_iso() -> str:
    """Return today's date as ``YYYY-MM-DD`` (server-local)."""
    return date.today().isoformat()


def _extract_first_sentence(wikitext: str) -> str:
    """Return a plain-text first sentence from the lead of ``wikitext``, or ``""``."""
    heading = _LEAD_END_RE.search(wikitext)
    lead = wikitext[: heading.start()] if heading else wikitext
    plain = _strip_wikitext(lead)
    if not plain:
        return ""
    boundary = _SENTENCE_END_RE.search(plain)
    if boundary:
        sentence = plain[: boundary.start() + 1].strip()
    elif len(plain) > _SNIPPET_MAX_CHARS:
        sentence = plain[:_SNIPPET_MAX_CHARS].rsplit(" ", 1)[0].strip() + "…"
    else:
        sentence = plain.strip()
    return sentence if len(sentence) >= _SNIPPET_MIN_CHARS else ""


def _pick_random_articles(conn: sqlite3.Connection, n: int = 2, max_attempts: int = 20) -> list[dict[str, str]]:
    """Pick ``n`` non-redirect articles with usable lead snippets.

    Uses indexed ``page_id``-offset selection so it stays O(log n) on 19M-row
    DBs, unlike ``ORDER BY RANDOM()`` which scans the full table.
    """
    row = conn.execute(
        "SELECT MAX(page_id) AS max_id FROM articles WHERE namespace = 0 AND text_bytes >= ?",
        (_MIN_DAILY_ARTICLE_BYTES,),
    ).fetchone()
    if not row or not row["max_id"]:
        return []
    max_id = int(row["max_id"])
    picks: list[dict[str, str]] = []
    seen: set[int] = set()
    for _ in range(max_attempts):
        if len(picks) >= n:
            break
        offset = random.randint(1, max_id)
        cur = conn.execute(
            "SELECT page_id, title, text_content FROM articles "
            "WHERE namespace = 0 AND text_bytes >= ? AND page_id >= ? "
            "ORDER BY page_id LIMIT 1",
            (_MIN_DAILY_ARTICLE_BYTES, offset),
        )
        article = cur.fetchone()
        if not article or article["page_id"] in seen:
            continue
        seen.add(article["page_id"])
        if wiki_db.redirect_target(article["text_content"]):
            continue
        snippet = _extract_first_sentence(article["text_content"])
        if not snippet:
            continue
        picks.append({"title": article["title"], "snippet": snippet})
    return picks


def daily_random_articles(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """Return two articles featured today, cached daily in ``db_metadata``."""
    today = _today_iso()
    try:
        date_row = conn.execute("SELECT value FROM db_metadata WHERE key = ?", (_DAILY_DATE_KEY,)).fetchone()
        payload_row = conn.execute("SELECT value FROM db_metadata WHERE key = ?", (_DAILY_PAYLOAD_KEY,)).fetchone()
    except (sqlite3.OperationalError, RemoteSqliteError):
        date_row = payload_row = None
    if date_row and date_row["value"] == today and payload_row:
        try:
            cached = json.loads(payload_row["value"])
            if isinstance(cached, list) and all(isinstance(x, dict) for x in cached):
                return cached
        except json.JSONDecodeError:
            pass
    picks = _pick_random_articles(conn, n=2)
    conn.execute("CREATE TABLE IF NOT EXISTS db_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        "INSERT OR REPLACE INTO db_metadata (key, value) VALUES (?, ?)",
        (_DAILY_DATE_KEY, today),
    )
    conn.execute(
        "INSERT OR REPLACE INTO db_metadata (key, value) VALUES (?, ?)",
        (_DAILY_PAYLOAD_KEY, json.dumps(picks)),
    )
    conn.commit()
    return picks


def format_elapsed(started_at: str) -> str:
    try:
        start = datetime.fromisoformat(started_at)
        elapsed = int((datetime.now(UTC) - start.replace(tzinfo=UTC)).total_seconds())
        if elapsed < 60:
            return f"{elapsed}s"
        if elapsed < 3600:
            return f"{elapsed // 60}m {elapsed % 60}s"
        return f"{elapsed // 3600}h {(elapsed % 3600) // 60}m"
    except Exception:
        return ""


def format_started_at(started_at: str) -> str:
    try:
        dt = datetime.fromisoformat(started_at)
        return dt.strftime("%-d %b %Y %H:%M UTC")
    except Exception:
        return started_at


def format_embedded_at(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts


def spawn_worker(module: str, wiki: str, job_id: int, log_suffix: str) -> None:
    """Thin forwarder to ``workers.spawn.spawn_worker`` (kept for stable import path)."""
    _spawn_worker(module, wiki, job_id, log_suffix)


def htmx_redirect(url: str, request: Request) -> Response:
    """Return a 204 with ``HX-Redirect`` for HTMX requests, else a 303 redirect.

    Lets the same code path serve both HTMX-driven UI and plain HTTP clients.
    """
    if request.headers.get("hx-request") or "HX-Request" in request.headers:
        return Response(status_code=204, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)
