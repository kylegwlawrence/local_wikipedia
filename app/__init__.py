"""FastAPI web app for browsing the local Wikipedia SQLite database.

The single-page UI serves ``GET /`` and HTMX-driven fragments for search and
article rendering. ``WIKI_DB`` env var overrides the database path so the
tests can point the app at a hermetic fixture DB.
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.lifespan import lifespan
from app.routes import (
    active_embedding,
    article,
    embeddings,
    home,
    rag,
    refresh,
)
from paths import BASE_DIR

app = FastAPI(title="Local Wikipedia", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

for _module in (home, article, refresh, embeddings, active_embedding, rag):
    app.include_router(_module.router)
