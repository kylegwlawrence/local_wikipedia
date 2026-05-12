"""FastAPI web app for browsing the local Wikipedia SQLite database.

The single-page UI serves ``GET /`` and HTMX-driven fragments for search and
article rendering. ``WIKI_DB`` env var overrides the database path so the
tests can point the app at a hermetic fixture DB.
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.lifespan import lifespan
from paths import BASE_DIR


app = FastAPI(title="Local Wikipedia", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

from app.routes import (  # noqa: E402
    active_embedding as active_embedding_route,
    article as article_route,
    embeddings as embeddings_route,
    home as home_route,
    refresh as refresh_route,
)

for module in (
    home_route,
    article_route,
    refresh_route,
    embeddings_route,
    active_embedding_route,
):
    app.include_router(module.router)
