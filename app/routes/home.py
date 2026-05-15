"""Home page, search fragment, and wiki-switch redirect."""

import os
import pathlib
import sqlite3
from urllib.parse import quote, urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import db as wiki_db
import paths
from app.config import WIKI_LABELS, templates
from app.deps import active_wiki
from app.helpers import daily_random_articles, search_titles
from paths import KNOWN_WIKIS

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def index(request: Request, article: str = "", wiki: str = "") -> HTMLResponse:
    if article:
        return RedirectResponse(f"/article/{quote(article)}", status_code=302)

    selected_wiki = wiki if wiki in KNOWN_WIKIS else active_wiki(request)
    other_wiki = next(w for w in KNOWN_WIKIS if w != selected_wiki)
    other_wiki_db = paths.db_path_for(other_wiki)
    other_wiki_for_template = other_wiki if other_wiki_db.exists() else None
    wiki_db_path = pathlib.Path(os.environ["WIKI_DB"]) if "WIKI_DB" in os.environ else paths.db_path_for(selected_wiki)
    with wiki_db.connect(wiki_db_path) as conn:
        try:
            row = conn.execute("SELECT value FROM db_metadata WHERE key = 'article_count'").fetchone()
            article_count = int(row["value"]) if row else None
        except sqlite3.OperationalError:
            article_count = None
        if article_count is None:
            article_count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
            conn.execute("CREATE TABLE IF NOT EXISTS db_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute(
                "INSERT OR REPLACE INTO db_metadata (key, value) VALUES ('article_count', ?)",
                (str(article_count),),
            )
        daily_articles = daily_random_articles(conn)
    response = templates.TemplateResponse(
        request,
        "index.html",
        {
            "wiki": selected_wiki,
            "wiki_label": WIKI_LABELS[selected_wiki],
            "other_wiki": other_wiki_for_template,
            "current_page": "home",
            "article_count": f"{article_count:,}",
            "daily_articles": daily_articles,
        },
    )
    if wiki in KNOWN_WIKIS:
        response.set_cookie("wiki_pref", wiki, max_age=365 * 24 * 3600)
    return response


@router.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = "") -> HTMLResponse:
    titles = search_titles(q, request)
    return templates.TemplateResponse(request, "search_results.html", {"titles": titles, "q": q})


@router.get("/switch-wiki")
def switch_wiki(request: Request, to: str, article: str = "", return_to: str = "") -> RedirectResponse:
    if to not in KNOWN_WIKIS:
        raise HTTPException(status_code=400, detail=f"Unknown wiki: {to}")
    if article:
        redirect_url = f"/article/{quote(article)}"
    elif return_to and return_to.startswith("/") and not return_to.startswith("//"):
        redirect_url = return_to
    else:
        # Fallback: if the caller came from an /article/ or /wikitext/ page,
        # stay there so wiki-switch chips on those pages always preserve
        # context even if the link doesn't include &article=.
        referer = request.headers.get("referer", "")
        redirect_url = "/"
        if referer:
            parsed = urlparse(referer)
            path = parsed.path or ""
            if path.startswith("/article/") or path.startswith("/wikitext/"):
                redirect_url = path
    response = RedirectResponse(redirect_url, status_code=302)
    response.set_cookie("wiki_pref", to, max_age=365 * 24 * 3600)
    return response
