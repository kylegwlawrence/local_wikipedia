"""Per-request helpers for picking the active wiki and opening connections.

These reach ``paths`` via module attribute lookup (``paths.db_path_for(...)``)
so tests can monkeypatch the lookup in one place.
"""

import os
import pathlib
import sqlite3

from fastapi import HTTPException, Request

import db as wiki_db
import paths
from paths import DEFAULT_WIKI
from rag.schema import connect_rag


def active_wiki(request: Request) -> str:
    """Return the wiki the user has selected, defaulting to ``DEFAULT_WIKI``."""
    return request.cookies.get("wiki_pref", DEFAULT_WIKI)


def db_path(request: Request) -> pathlib.Path:
    """Return the SQLite database path.

    ``WIKI_DB`` env var wins (used by tests); otherwise the ``wiki_pref``
    cookie determines which wiki database to open.
    """
    if "WIKI_DB" in os.environ:
        return pathlib.Path(os.environ["WIKI_DB"])
    return paths.db_path_for(active_wiki(request))


def connect(request: Request) -> sqlite3.Connection:
    """Open a per-request SQLite connection with row-dict access.

    Raises:
        HTTPException: 503 if the configured database file does not exist —
            surfaces a clear error when the web app starts before the parser.
    """
    path = db_path(request)
    if not path.exists():
        raise HTTPException(status_code=503, detail=f"Database not found: {path}")
    return wiki_db.connect(path)


def rag_connect(wiki: str):
    """Open the RAG DB if it exists; return None if not yet created."""
    path = paths.rag_db_path_for(wiki)
    if not path.exists():
        return None
    return connect_rag(path)
