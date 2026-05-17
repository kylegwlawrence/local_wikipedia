# arXiv RAG plan

## Goal

Metadata-only semantic search over arXiv papers. Harvest titles/abstracts/categories via OAI-PMH from `export.arxiv.org/oai2`, embed each paper as a single chunk with `nomic-embed-text`, expose `/arxiv` + `/arxiv/search` routes that link out to `arxiv.org/abs/{id}`. Parallel to the existing wiki abstraction — no shared DB, no shared code path, no changes to `KNOWN_WIKIS`.

## Scope (v1)

- Corpus: papers with `submitted_date >= 2021-01-01` (~1M papers)
- One chunk per paper, embed text: `f"{title}\n\n{abstract}\n\nCategories: {categories}"`
- Manual ingest via `python -m arxiv.ingest`
- Click search result → external link to `https://arxiv.org/abs/{id}` (no local reading view)

## Out of scope (v1) - implement in future versions

- PDF download / extraction / chunking / math preservation
- Local reading view, pagination, category / date filter UI
- Job system (`arxiv_jobs` table, UI refresh button, worker subprocess) — manual CLI only
- Cross-corpus retrieval combining arxiv + wiki in one query

## File layout

```
arxiv/
  __init__.py
  schema.py          — papers DDL (arxiv.db) + papers_meta/fts/vec DDL (arxiv_rag.db); connect helpers
  oai.py             — OAI-PMH ListRecords client: resumption tokens, disk cache, rate-limit, retry
  ingest.py          — CLI: harvest via oai.py → upsert papers + ingest_state
  embed.py           — CLI: read papers, embed via rag.embedder, write papers_meta + papers_vec + papers_fts
  retriever.py       — RRF over papers_vec + papers_fts (mirrors rag/retriever.py); joins back to arxiv.db
  templates_meta.py  — single source for embed-text formatting

app/routes/arxiv.py  — GET /arxiv shell, GET /arxiv/search?q= HTMX fragment

templates/arxiv.html, templates/arxiv_results.html
templates/_nav.html  — add "arXiv" link

paths.py             — add ARXIV_DB, ARXIV_RAG_DB, ARXIV_OAI_CACHE_DIR constants

dumps/arxiv.db
dumps/arxiv_rag.db
dumps/arxiv_oai_cache/{sha1(url)}.xml
dumps/arxiv_ingest.log
dumps/arxiv_embed.log

tests/
  conftest.py        — add build_arxiv_fixture(path) + arxiv_db_path fixture
  test_arxiv_schema.py
  test_arxiv_oai.py        — respx-mocked HTTP; XML parsing, resumption tokens, cache reads/writes
  test_arxiv_ingest.py     — upsert, datestamp-change handling, ingest_state tracking, --from-cache mode
  test_arxiv_embed.py      — embed flow with Ollama mocked via respx
  test_arxiv_retriever.py  — RRF over fixture vec + fts (no Ollama needed)
  test_arxiv_routes.py     — /arxiv search via TestClient
```

## Schema

### `dumps/arxiv.db`

```sql
CREATE TABLE papers (
  id TEXT PRIMARY KEY,                -- arXiv identifier, e.g. "2401.12345"
  oai_datestamp TEXT NOT NULL,        -- OAI-PMH header datestamp; change ⇒ re-embed
  title TEXT NOT NULL,
  abstract TEXT NOT NULL,
  authors TEXT NOT NULL,              -- JSON-encoded list of "Forename Keyname" strings
  categories TEXT NOT NULL,           -- space-separated, e.g. "cs.CL cs.LG"
  primary_category TEXT NOT NULL,
  submitted_date TEXT NOT NULL,       -- <created> from arXiv metadata, ISO date
  updated_date TEXT,                  -- <updated> if present
  doi TEXT,
  journal_ref TEXT,
  comments TEXT
);
CREATE INDEX idx_papers_submitted ON papers(submitted_date);
CREATE INDEX idx_papers_primary_cat ON papers(primary_category);

CREATE TABLE ingest_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- stores last_harvested_date for incremental runs
```

### `dumps/arxiv_rag.db`

```sql
CREATE TABLE papers_meta (
  rowid INTEGER PRIMARY KEY,
  arxiv_id TEXT UNIQUE NOT NULL,
  oai_datestamp TEXT NOT NULL,
  embed_text TEXT NOT NULL
);
CREATE VIRTUAL TABLE papers_fts USING fts5(
  embed_text,
  content='papers_meta',
  content_rowid='rowid',
  tokenize='porter ascii'
);
CREATE VIRTUAL TABLE papers_vec USING vec0(
  rowid INTEGER PRIMARY KEY,
  embedding float[768]
);
```

`connect_arxiv_rag(path)` loads `sqlite_vec` on every connection — mirrors `rag.schema.connect_rag`. Re-embed trigger: `papers.oai_datestamp > papers_meta.oai_datestamp` for a given `arxiv_id`.

## OAI-PMH client (`arxiv/oai.py`)

- Endpoint: `http://export.arxiv.org/oai2`
- First request params: `verb=ListRecords&metadataPrefix=arXiv&from={from_date}` (and optional `until`)
- Subsequent: `verb=ListRecords&resumptionToken={token}` (no other params per OAI spec)
- Headers: `User-Agent: local_wikipedia/0.1 (mailto:kylegwlawrence@gmail.com)`
- Rate-limit: 3s between requests (`MIN_REQUEST_INTERVAL = 3.0`)
- Retry: 3 attempts on 429 / 5xx with exponential backoff; honor `Retry-After` if present
- Cache: every XML response written to `dumps/arxiv_oai_cache/{sha1(url)}.xml` before parsing
- Yields one dict per `<record>` matching the `papers` schema (plus `oai_datestamp` from the `<header><datestamp>`)

Parsing: `xml.etree.ElementTree` with explicit namespace map for `arXiv` (`http://arxiv.org/OAI/arXiv/`) and `oai` (`http://www.openarchives.org/OAI/2.0/`). Author names rendered as `"Forename(s) Keyname"` and stored as a JSON list.

## Ingest CLI (`arxiv/ingest.py`)

```
python -m arxiv.ingest [--from YYYY-MM-DD] [--until YYYY-MM-DD] [--from-cache] [--reset]
```

- `--from` default: `ingest_state.last_harvested_date`, falling back to `2021-01-01` on first run
- `--from-cache`: read existing OAI cache XML files instead of hitting the network — main path during schema iteration so you don't re-burn arxiv's rate limit on local dev mistakes
- `--reset`: drop and recreate `papers` + `ingest_state`
- Upsert by `id`; skip if `oai_datestamp` unchanged
- Commit every 1000 rows
- After successful completion, set `ingest_state.last_harvested_date = today_iso`

Logging to `dumps/arxiv_ingest.log` via `workers/runner.py`-style harness reused if it's a clean fit; otherwise inline.

## Embed CLI (`arxiv/embed.py`)

```
python -m arxiv.embed [--limit N] [--batch 100] [--reset]
```

- Diff `arxiv.db.papers` against `arxiv_rag.db.papers_meta`:
  - New → insert into papers_meta, embed, insert papers_vec
  - `papers.oai_datestamp > papers_meta.oai_datestamp` → delete + re-insert
  - Unchanged → skip
- Embed text computed via `arxiv.templates_meta.format_embed_text(paper)` — single source of truth
- Batch via existing `rag.embedder.embed_texts_batch`
- Commit every `--batch` papers
- Bulk-rebuild FTS once at end: `INSERT INTO papers_fts(papers_fts) VALUES('rebuild')`

## Retriever (`arxiv/retriever.py`)

- Signature: `retrieve(query: str, k: int = 10) -> list[dict]`
- Dense: vec0 ANN over `papers_vec`, top 2k
- Sparse: FTS5 BM25 over `papers_fts` with per-word quoting (`"w1" "w2"` to force AND-of-terms, not phrase match — same trick as `rag/retriever.py`), top 2k
- RRF merge (k=60), same formula as `rag/retriever.py`
- Join `papers_meta.arxiv_id` → `arxiv.db.papers` for full result rows
- Falls back to sparse-only if Ollama is unreachable

## Web routes (`app/routes/arxiv.py`)

- `GET /arxiv` → render `arxiv.html` shell (HTMX `hx-get="/arxiv/search"`, input `hx-trigger="keyup changed delay:200ms"`, target `#arxiv-results`)
- `GET /arxiv/search?q=...` → render `arxiv_results.html` fragment with top 20 results
- Result card fields: title (linking to `https://arxiv.org/abs/{id}` with `target="_blank" rel="noopener"`), authors (first 3 + "et al" if more), abstract snippet (first ~200 chars), category badges, submitted date

## Nav integration

- Add an "arXiv" link to the nav button group in `templates/_nav.html`
- Route handlers pass `current_page="arxiv"` for active state
- Other routes pass through unchanged
- Wiki-switching badges: arxiv has no analog (single corpus); the nav button alone is the affordance

## Embed-text formatter (`arxiv/templates_meta.py`)

```python
def format_embed_text(paper: dict) -> str:
    return f"{paper['title']}\n\n{paper['abstract']}\n\nCategories: {paper['categories']}"
```

Imported by both `embed.py` (write-time) and any future re-embed paths. Keeping it as a one-line function in its own module makes the choice obvious and trivially testable; if we later A/B against a title+abstract-only variant, the change lives in one place.

## Implementation order

1. `arxiv/schema.py` + `tests/test_arxiv_schema.py`
2. `arxiv/oai.py` + `tests/test_arxiv_oai.py` (mocked HTTP, fixture XML responses)
3. `arxiv/ingest.py` + `tests/test_arxiv_ingest.py` (uses `--from-cache` against fixture XML)
4. `arxiv/embed.py` + `tests/test_arxiv_embed.py` (Ollama mocked via respx)
5. `arxiv/retriever.py` + `tests/test_arxiv_retriever.py` (no Ollama needed)
6. `app/routes/arxiv.py` + templates + `_nav.html` change + `tests/test_arxiv_routes.py`
7. End-to-end smoke: `python -m arxiv.ingest --from 2024-01-01 --until 2024-01-02` (one day, ~500 papers); embed; query `/arxiv/search?q=transformer`

## Open / deferred

- Crash-resume from last successful resumption token: cache helps but doesn't fully cover an interrupted harvest. Punt to v2 if it bites in practice.
- Surfacing category badges as links to a category-filtered view — defer until the v1 UI feels limiting.
- Cross-corpus retrieval (arxiv + wiki in one RRF) — intentionally out of scope. The parallel architecture means adding it later requires a unifier in front (e.g. a `search.py` that calls both retrievers and merges), not a refactor of either side.
