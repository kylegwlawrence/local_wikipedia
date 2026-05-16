"""Minimal SQL-over-HTTP proxy for serving enwiki.db from a remote machine.

Deploy this file to the remote machine and run:
    ENWIKI_DB=/path/to/enwiki.db uvicorn proxy_server:app --host 0.0.0.0 --port 8000

The local app talks to it via WIKI_REMOTE_ENWIKI=http://<host>:8000.
Wire contract: docs/apis/REMOTE_WIKI_API.md
"""

import base64
import os
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

_DB_PATHS: dict[str, Path] = {
    "enwiki": Path(os.environ.get("ENWIKI_DB", "enwiki.db")),
    "enwiki_rag": Path(os.environ.get("ENWIKI_RAG_DB", "enwiki_rag.db")),
}

_RAG_WIKIS = {"enwiki_rag"}


def _decode_param(v: Any) -> Any:
    """Decode a base64-encoded blob param sent by RemoteSqliteConnection."""
    if isinstance(v, str) and v.startswith("__base64__:"):
        return base64.b64decode(v[11:])
    return v


class SqlRequest(BaseModel):
    sql: str
    params: list[Any] = []


def _connect(wiki: str) -> sqlite3.Connection:
    if wiki in _RAG_WIKIS:
        # Use connect_rag so the RAG schema migrations run — the local app
        # adds columns to articles_meta over time, and the remote DB must be
        # kept in sync or the proxy returns "no such column" for new SELECTs.
        from rag.schema import connect_rag
        return connect_rag(_DB_PATHS[wiki])
    return sqlite3.connect(_DB_PATHS[wiki])


@app.get("/api/exists/{wiki}")
def db_exists(wiki: str):
    if wiki not in _DB_PATHS:
        return {"exists": False}
    return {"exists": _DB_PATHS[wiki].exists()}


@app.post("/api/sql/{wiki}")
def run_sql(wiki: str, body: SqlRequest):
    if wiki not in _DB_PATHS:
        raise HTTPException(status_code=404, detail=f"unknown wiki: {wiki}")
    if not _DB_PATHS[wiki].exists():
        raise HTTPException(status_code=404, detail=f"database not found: {_DB_PATHS[wiki]}")
    try:
        conn = _connect(wiki)
        try:
            decoded = [_decode_param(p) for p in body.params]
            cur = conn.execute(body.sql, decoded)
            rows = cur.fetchall()
            columns = [d[0] for d in cur.description] if cur.description else []
            return {
                "columns": columns,
                "rows": [list(r) for r in rows],
                "last_insert_rowid": cur.lastrowid or 0,
            }
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=str(exc))
