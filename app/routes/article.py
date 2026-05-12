"""Article and wikitext views + the chunks debug page."""
import json
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

import db as wiki_db
import paths
from app.config import templates
from app.deps import active_wiki, rag_connect
from app.helpers import fetch_article, wiki_label
from paths import KNOWN_WIKIS
from render import convert_wikitext_to_html

router = APIRouter()


@router.get("/article/{title:path}", response_class=HTMLResponse)
def article(request: Request, title: str) -> HTMLResponse:
    """Render a single article as HTML.

    The ``{title:path}`` converter is used so titles containing slashes —
    rare but legal in MediaWiki — round-trip correctly.

    Raises:
        HTTPException: 404 when no article with that exact title exists.
    """
    row, redirected_from = fetch_article(title, request)
    if row is None:
        if request.headers.get("HX-Request") == "true":
            wiki = active_wiki(request)
            resp = Response(content="", status_code=200)
            resp.headers["HX-Reswap"] = "none"
            resp.headers["HX-Trigger"] = json.dumps(
                {"articleNotFound": {"title": title, "wiki": wiki_label(wiki)}}
            )
            return resp
        raise HTTPException(status_code=404, detail=f"Article not found: {title}")

    html = convert_wikitext_to_html(row["text_content"])

    wiki = active_wiki(request)
    other_wiki = next(w for w in KNOWN_WIKIS if w != wiki)
    other_wiki_for_template = None
    other_wiki_db = paths.db_path_for(other_wiki)
    if other_wiki_db.exists():
        try:
            with wiki_db.connect(other_wiki_db) as ow_conn:
                hit = ow_conn.execute(
                    "SELECT 1 FROM articles WHERE title = ? LIMIT 1", (row["title"],)
                ).fetchone()
                if hit:
                    other_wiki_for_template = other_wiki
        except Exception:
            pass
    response = templates.TemplateResponse(
        request,
        "article.html",
        {
            "title": row["title"],
            "html": html,
            "text_bytes": row["text_bytes"],
            "timestamp": row["timestamp"],
            "redirected_from": redirected_from,
            "wiki": wiki,
            "other_wiki": other_wiki_for_template,
        },
    )
    if request.headers.get("HX-Request") == "true":
        response.headers["HX-Push-Url"] = f"/?wiki={wiki}&article={quote(row['title'])}"
    return response


@router.get("/wikitext/{title:path}", response_class=HTMLResponse)
def wikitext(request: Request, title: str) -> HTMLResponse:
    """Return the raw wikitext for an article, bypassing the render pipeline.

    Paired with ``/article/{title}`` — the two endpoints toggle between the
    rendered and raw views by swapping each other's fragment into ``#article``.
    """
    row, _redirected_from = fetch_article(title, request)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Article not found: {title}")

    wiki = active_wiki(request)
    other_wiki = next(w for w in KNOWN_WIKIS if w != wiki)
    other_wiki_for_template = None
    other_wiki_db = paths.db_path_for(other_wiki)
    if other_wiki_db.exists():
        try:
            with wiki_db.connect(other_wiki_db) as ow_conn:
                hit = ow_conn.execute(
                    "SELECT 1 FROM articles WHERE title = ? LIMIT 1", (row["title"],)
                ).fetchone()
                if hit:
                    other_wiki_for_template = other_wiki
        except Exception:
            pass

    return templates.TemplateResponse(
        request,
        "wikitext.html",
        {
            "title": row["title"],
            "wikitext": row["text_content"],
            "text_bytes": row["text_bytes"],
            "timestamp": row["timestamp"],
            "wiki": wiki,
            "other_wiki": other_wiki_for_template,
        },
    )


@router.get("/chunks/{title:path}", response_class=HTMLResponse)
def chunks(request: Request, title: str) -> HTMLResponse:
    wiki = active_wiki(request)
    rag_conn = rag_connect(wiki)

    if rag_conn is None:
        raise HTTPException(status_code=404, detail="No RAG database for this wiki")

    try:
        meta = rag_conn.execute(
            "SELECT page_id FROM articles_meta WHERE title = ?", (title,)
        ).fetchone()
        if meta is None:
            raise HTTPException(status_code=404, detail=f"Article not embedded: {title}")

        rows = rag_conn.execute(
            "SELECT section, chunk_index, text, text_length "
            "FROM chunks WHERE page_id = ? ORDER BY chunk_id",
            (meta["page_id"],),
        ).fetchall()
        chunk_list = [dict(r) for r in rows]
    finally:
        rag_conn.close()

    return templates.TemplateResponse(request, "chunks.html", {
        "title": title,
        "chunks": chunk_list,
        "wiki": wiki,
        "current_page": "",
    })
