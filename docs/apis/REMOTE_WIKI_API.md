# Remote Wiki API (placeholder)

This is the wire contract that a **remote** local_wikipedia instance must
serve so the local app can use its SQLite DB as a wiki backend. The
local-side client is in `remote.py` (`RemoteSqliteConnection`); the matching
server endpoint has **not yet been added** to this app — it will live
alongside the existing routes once the remote machine is provisioned.

The design is a thin SQL-over-HTTP proxy: one endpoint accepts arbitrary SQL,
returns rows as JSON. The local app's `connect()` returns a connection-like
object that proxies `.execute(sql, params)` to it, so call sites in
`app.helpers` etc. keep working unchanged.

The remote is expected to live on a trusted network (LAN, Tailscale,
WireGuard); there is no auth in the placeholder.

Base URL is configured locally per wiki via env var:

```bash
WIKI_REMOTE_ENWIKI=http://192.168.1.10:8000
```

## `POST /api/sql/{wiki}`

Execute one SQL statement against the named wiki's SQLite DB.

**Path params:**
- `wiki` — wiki name, e.g. `enwiki`. The remote uses this to pick the right
  DB file (analogous to `paths.db_path_for(wiki)`).

**Request body (JSON):**
```json
{
  "sql": "SELECT title FROM articles WHERE title = ? LIMIT 1",
  "params": ["Apple"]
}
```

- `sql` — single statement. Multi-statement scripts are out of scope.
- `params` — list of positional parameters. The remote binds them with
  SQLite's `?`-style placeholders.

**Response (200):**
```json
{
  "columns": ["title"],
  "rows": [["Apple"]]
}
```

- `columns` — column names in the order the SQL produced them. Same order
  as `cursor.description` on the local side.
- `rows` — list of row arrays. Element order matches `columns`.
- For non-SELECT statements (INSERT, UPDATE, CREATE TABLE), the response
  shape is the same with `columns: []` and `rows: []`.

**Response (4xx/5xx):** the local client raises `RemoteSqliteError` with the
status code and the first 500 bytes of the body. Any structured error format
is fine; the client only surfaces it for diagnostics.

## Type encoding

SQLite values map to JSON as follows:

| SQLite type | JSON |
|---|---|
| `INTEGER` | number |
| `REAL`    | number |
| `TEXT`    | string |
| `NULL`    | `null` |
| `BLOB`    | **not supported** — the wiki schema has no BLOB columns; the contract can be extended (base64) later if needed |

## Transaction semantics

Each `POST /api/sql/{wiki}` runs in its own implicit transaction on the
remote (autocommit). The local proxy's `commit()` is a no-op; `rollback()`
raises `RemoteSqliteError` so anyone depending on rollback behaviour gets a
loud failure instead of silent data loss.

This means **multi-statement transactions are not supported** over the
remote connection. The local code only relies on per-statement atomicity
(`INSERT OR REPLACE`, `INSERT INTO ... VALUES('rebuild')`, etc.) so this is
acceptable — but anything fancier (e.g. `BEGIN IMMEDIATE` lock-contention
guards used by the refresh-job system) is local-machine-only by design.

## Error handling

- Network errors (`httpx.HTTPError`) become `RemoteSqliteError` on the
  client.
- SQL errors on the remote should return 4xx (or 500 for unexpected
  failures) with an error message in the body — the client raises with the
  status and a short snippet of the body for debugging.

## What's deliberately *not* in this contract

- **No auth.** Add bearer-token middleware here if the remote ever leaves a
  trusted network.
- **No multi-statement transactions** (see above).
- **No BLOB columns** (see above).
- **No RAG endpoints.** Retrieval (`/rag/info`, `/rag/retrieve`) already runs
  against the local RAG DB; remote RAG is a separate future project.
- **No streaming.** Result sets in this app are small (≤ a few hundred rows
  per query) so JSON-in-one-shot is fine.

## Implementing the server side

When ready, the server is a small FastAPI route alongside the existing app:

```python
@app.post("/api/sql/{wiki}")
def remote_sql(wiki: str, body: dict):
    path = paths.db_path_for(wiki)
    if not path.exists():
        raise HTTPException(404, f"unknown wiki: {wiki}")
    conn = db.connect(path)
    try:
        cur = conn.execute(body["sql"], body.get("params", []))
        rows = cur.fetchall()
        columns = [d[0] for d in cur.description] if cur.description else []
        conn.commit()
        return {"columns": columns, "rows": [list(r) for r in rows]}
    finally:
        conn.close()
```

(Pseudocode — real implementation will add validation, error mapping, and
should bind to a private interface only.)
