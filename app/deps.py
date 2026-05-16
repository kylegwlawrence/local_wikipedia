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
from remote import RemoteSqliteConnection


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


def connect(request: Request) -> sqlite3.Connection | RemoteSqliteConnection:
    """Open a per-request connection to the active wiki.

    Returns a local ``sqlite3.Connection`` for local wikis, or a
    :class:`RemoteSqliteConnection` (same ``execute / fetchone / fetchall /
    commit / close`` surface) when the wiki is configured as remote via
    ``WIKI_REMOTE_<WIKI>``. The ``WIKI_DB`` env var (used by tests) wins over
    remote config so suites can force a local fixture.

    Raises:
        HTTPException: 503 if a local DB is selected but the file is missing.
    """
    wiki = active_wiki(request)
    if "WIKI_DB" not in os.environ:
        remote_url = paths.remote_url_for(wiki)
        if remote_url is not None:
            return RemoteSqliteConnection(remote_url, wiki)
    path = db_path(request)
    if not path.exists():
        raise HTTPException(status_code=503, detail=f"Database not found: {path}")
    return wiki_db.connect(path)


def rag_connect(wiki: str):
    """Open the RAG DB if it exists; return None if not yet created."""
    if paths.is_remote(wiki):
        import httpx
        base_url = paths.remote_url_for(wiki)
        try:
            r = httpx.get(f"{base_url}/api/exists/{wiki}_rag", timeout=5.0)
            if not r.json().get("exists"):
                return None
        except Exception:
            return None
        return RemoteSqliteConnection(base_url, f"{wiki}_rag")
    path = paths.rag_db_path_for(wiki)
    if not path.exists():
        return None
    return connect_rag(path)
