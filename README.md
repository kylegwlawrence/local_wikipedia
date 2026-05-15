# Local Wikipedia

A Python toolkit for downloading, parsing, and locally querying Wikipedia dumps. Downloads and SHA-1-verifies Wikimedia dumps, parses them into SQLite, serves a browser-based reader, and builds a local RAG index for semantic search using a local Ollama model.

## Features

### Download Module
- Downloads the latest multistream Wikipedia dumps (both article XML and index files)
- Automatic SHA-1 verification against Wikimedia's official checksums
- Progress bar display for download tracking
- Smart resume: skips files that already exist with correct checksums
- Atomic writes to prevent corrupt files on interruption
- Memory-efficient streaming for multi-gigabyte files

### Parse Module
- Extracts articles from compressed Wikipedia XML dumps
- Stores articles in SQLite database with full metadata
- Memory-efficient streaming parser for multi-GB files
- Batch inserts for optimal performance
- Filters to main articles only (namespace 0)
- B-tree indexes for title/namespace/timestamp queries
- FTS5 trigram index for fast title search (prefix and substring)

### Wikitext Converter
- Converts Wikipedia wikitext to clean, readable HTML
- Handles bold, italic, headings, lists, tables, code blocks, external links, pipe tricks, and linktrails
- Renders infoboxes, citations, footnotes (`{{sfn}}`), quotes, coordinates, and common templates (lang, units, indicators)
- Renders mathematical formulas using KaTeX (vendored locally — no internet required), including `{{math|…}}` family as inline `texhtml` spans and `<math>` / `align` / `equation` / `tmath` blocks
- Handles `<poem>`, `<chem>`, and `<syntaxhighlight>` tags
- Modular pipeline: each stage lives in its own file under `render/`

### Web App
- FastAPI + Jinja2 + HTMX single-page UI for browsing the parsed database
- Search bar with debounced as-you-type title lookup via FTS5 (instant even on enwiki's 6M+ articles)
- Articles rendered server-side: wikitext → HTML, with a toggle to view raw wikitext
- Follows `#REDIRECT` chains up to 5 hops
- Wiki-switching badges in every page header: click the inactive wiki's badge to switch; active wiki is shown as a disabled span. Cookie-persisted — no app restart needed.
- Site-wide navigation bar (Home / Embeddings / Jobs) on all full pages, with the current page highlighted
- One-click incremental refresh: downloads a new dump and updates only changed articles in-place (no full re-parse)
- "Embed + links" button: enqueues an article and all its wikilink targets for batch embedding in one click; article header shows a "links embedded" badge once complete
- Active embedding page (`/active-embedding`): live progress panel showing per-article status, with cancel support
- Chunks viewer (`/chunks/{title}`): inspect the text chunks and section boundaries stored in the RAG index for any embedded article
- No JavaScript build step; all logic stays in Python

### RAG Pipeline
- Chunks articles into sections using `== Heading ==` regex boundaries and strips wikitext to plain text via mwparserfromhell
- Embeds chunks with a local Ollama model (`nomic-embed-text`, 768 dims) and stores vectors in `sqlite-vec`
- Uses nomic's task prefixes — `search_document:` (with `Title — Section\n\n` prepended) when embedding chunks and `search_query:` when embedding queries — for better dense-retrieval alignment
- Hybrid retrieval: dense ANN search + FTS5 BM25 merged with Reciprocal Rank Fusion
- Incremental embedding: skips articles whose `revision_id` hasn't changed; safe to re-run after a wiki refresh
- RAG index stored in `dumps/{wiki}_rag.db` — separate from the wiki DB so refreshes don't clobber embeddings

### External RAG HTTP API
- Retrieval-only JSON API for external chat applications to query the local index — no LLM generation happens server-side
- `GET /rag/info` advertises server identity, embedding model + dimension, available corpora (only wikis whose `_rag.db` exists on disk), and the article URL template for building citation links
- `POST /rag/retrieve` runs hybrid dense + sparse retrieval against a chosen corpus and returns ranked chunks with `corpus`, `chunk_id`, `page_id`, `title`, `section`, `chunk_index`, `text`, `text_length`, `chunk_type` (`'prose'` / `'table'` / `'infobox'`), and `score`
- Pydantic-validated requests: `top_k` clamped to `[1, 50]`, blank queries rejected with 422, unknown corpora rejected with 404

## Installation

1. Clone this repository
2. Create and activate a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Download KaTeX for offline math rendering (one-time, ~1MB):
   ```bash
   python download/download_katex.py
   ```
5. (Optional) Install `ruff` for linting/formatting; config lives in `pyproject.toml`:
   ```bash
   pip install ruff
   ruff check .
   ruff format .
   ```

## Usage

### Step 1: Download Wikipedia Dumps

Download dumps for English Wikipedia (default):
```bash
python -m download.download
```

Download dumps for Simple English Wikipedia:
```bash
python -m download.download --wiki simplewiki
```

Downloaded files are saved to the `dumps/` directory.

### Step 2: Parse Dumps into SQLite

Parse the downloaded dump into a SQLite database:
```bash
python -m parse.cli
```

Parse a specific wiki:
```bash
python -m parse.cli --wiki enwiki
```

Verify an existing database:
```bash
python -m parse.cli --verify-only --database dumps/enwiki.db
```

Add FTS5 to an already-parsed database (no re-parse needed):
```bash
python -m parse.cli --rebuild-fts --database dumps/enwiki.db
```

### Step 3 (Optional): Build the RAG Index

Requires [Ollama](https://ollama.com) running locally with the embedding model pulled:

```bash
ollama pull nomic-embed-text
```

Embed a wiki (simplewiki takes ~2–3 hours; enwiki takes much longer):

```bash
# Smoke test — first 100 articles only
python -m rag.embed --wiki simplewiki --limit 100

# Full embedding run
python -m rag.embed --wiki simplewiki

# Re-run after a wiki refresh (automatically skips unchanged articles)
python -m rag.embed --wiki simplewiki

# Re-embed everything from scratch
python -m rag.embed --wiki simplewiki --reset
```

The index is saved to `dumps/{wiki}_rag.db`. Once built, the corpus is queryable both via the in-app retriever (used by `/chunks/{title}` etc.) and via the external `/rag/info` + `/rag/retrieve` HTTP API for chat applications.

#### Querying the external RAG API

```bash
# What corpora are available?
curl http://127.0.0.1:8000/rag/info

# Hybrid retrieval over a corpus
curl -X POST http://127.0.0.1:8000/rag/retrieve \
  -H 'Content-Type: application/json' \
  -d '{"query": "what is photosynthesis", "corpus": "simplewiki", "top_k": 5}'
```

### Step 4: Query the Database

**Option A: Web App**

Launch the FastAPI app for a browser-based search UI:

```bash
uvicorn app:app --reload
```

Or, on a remote server where you need the process to outlive your SSH session:

```bash
./start.sh             # start in a persistent tmux session
./start.sh attach      # re-attach to see logs
./start.sh stop        # stop the server
```

Then open `http://127.0.0.1:8000`. Type into the search box; matching titles appear below as you type, and clicking one renders the article as HTML. Click the enwiki or simplewiki badge in the header to switch between wikis (cookie-persisted). Use the **Home / Embeddings / Jobs** nav buttons to move between the main pages. Click **Refresh** on the home page to incrementally update the database from the latest dump without a full re-parse. Click **Embed + links** on any article to enqueue it and all its linked articles for batch embedding; progress is visible at `/active-embedding`.

To point the app at a specific database file, set `WIKI_DB`:

```bash
WIKI_DB=dumps/enwiki.db uvicorn app:app --reload
```

**Option B: SQLite CLI**

Query directly using sqlite3:
```bash
sqlite3 dumps/simplewiki.db
```

Example queries:
```sql
-- Count articles
SELECT COUNT(*) FROM articles;

-- Search by title
SELECT title, text_bytes FROM articles WHERE title LIKE 'Python%';

-- Get article content
SELECT text_content FROM articles WHERE title = 'Python';

-- Find largest articles
SELECT title, text_bytes FROM articles ORDER BY text_bytes DESC LIMIT 10;

-- Recent edits
SELECT title, timestamp FROM articles ORDER BY timestamp DESC LIMIT 10;
```

## How It Works

### Download Process
1. Fetches the official SHA-1 checksum manifest from Wikimedia
2. Checks if target files already exist with correct checksums (skip if valid)
3. Downloads missing or invalid files with progress indication
4. Verifies downloaded files against checksums
5. Uses atomic file operations (temp files + rename) to prevent corruption

### Parse Process
1. Opens bz2-compressed XML dump with streaming decompression
2. Uses iterative XML parsing to process one article at a time
3. Extracts metadata: title, IDs, timestamps, contributors, text content
4. Filters to main articles only (namespace 0)
5. Batch inserts articles into SQLite (1000 per batch)
6. Creates B-tree indexes and an FTS5 trigram index on title for fast querying
7. Records parse metadata for verification

## Testing

Run all tests:
```bash
pytest
```

Run download tests only:
```bash
pytest tests/test_download.py -v
```

Run parse tests only:
```bash
pytest tests/test_pipeline.py -v
```

Run a specific test class:
```bash
pytest tests/test_download.py::TestDownloadWithVerify
```

## Dependencies

Pinned in `requirements.txt` (and mirrored in `pyproject.toml`):

- **httpx** (0.28.1) - Async HTTP client for downloads
- **tqdm** (4.67.3) - Terminal progress bars
- **pytest** (9.0.3) - Testing framework
- **respx** (0.23.1) - HTTP mocking for tests
- **mwparserfromhell** (0.7.2) - MediaWiki wikitext parser
- **fastapi** (0.136.1) - Web framework for the browser UI
- **uvicorn[standard]** (0.46.0) - ASGI server for FastAPI
- **jinja2** (3.1.6) - HTML templating
- **sqlite-vec** (0.1.9) - Vector similarity search extension for SQLite (RAG pipeline)

Dev tooling (not pinned): `ruff` for linting and formatting — config under `[tool.ruff]` in `pyproject.toml`.

## Project Structure

```
.
├── app/               # FastAPI web app (package)
│   ├── __init__.py    # Factory: creates FastAPI, mounts /static, registers routers
│   ├── config.py      # Constants + Jinja2 templates instance
│   ├── deps.py        # Per-request helpers (active_wiki, db_path, connect, rag_connect)
│   ├── helpers.py     # Pure helpers (format_*, search_titles, fetch_article, spawn_worker, …)
│   ├── lifespan.py    # Startup crash-recovery + FastAPI lifespan hook
│   ├── panels.py      # Template-data builders for refresh/active-embedding panels
│   └── routes/        # APIRouter modules grouped by feature
│       ├── home.py            # /, /search, /switch-wiki
│       ├── article.py         # /article, /wikitext, /chunks
│       ├── refresh.py         # POST /refresh, GET /refresh/status
│       ├── embeddings.py      # /embed-manager, /embed-status, /embed-article, …
│       ├── active_embedding.py # /active-embedding (+ /jobs, /panel, /cancel)
│       └── rag.py             # GET /rag/info, POST /rag/retrieve (external API)
├── paths.py           # Project paths (BASE_DIR, DUMPS_DIR, JOBS_DB, KNOWN_WIKIS)
├── db.py              # connect(), redirect_target(), resolve_redirect()
├── jobs/              # Job-queue CRUD package
│   ├── __init__.py
│   ├── refresh.py     # CRUD helpers for refresh_jobs table in dumps/jobs.db
│   └── embed.py       # CRUD helpers for embed_jobs / embed_job_items tables
├── workers/           # Background-subprocess package
│   ├── __init__.py
│   ├── runner.py      # Shared harness (log redirect, exception capture)
│   ├── refresh.py     # Download → refresh → FTS rebuild
│   └── embed.py       # Drains embed_job_items queue
├── start.sh           # tmux helper — start/stop/attach the uvicorn server
├── render/            # Wikitext → HTML converter (package)
│   ├── __init__.py    # Public API: convert_wikitext_to_html
│   ├── pipeline.py    # Orchestrator — ordered stage list
│   ├── data.py        # Static data tables (LANG_NAMES, INDICATORS, …)
│   ├── templates.py   # Wikicode-level template handlers (infobox, cite, lang, math, …)
│   ├── tables.py      # Wikitext table → HTML
│   ├── blocks.py      # Lists, headings, paragraph wrapping
│   ├── inline.py      # Bold/italic, wikilinks
│   ├── protect.py     # Syntaxhighlight + math block extraction/restore
│   └── strip.py       # Remove templates, refs, comments, categories
├── download/
│   ├── download.py        # Dump downloader + SHA-1 verifier
│   └── download_katex.py  # One-time script to vendor KaTeX for offline math rendering
├── parse/
│   ├── schema.py      # SQLite schema + PRAGMAs (articles, articles_archive)
│   ├── xml_reader.py  # MediaWiki <page> element extractor
│   ├── pipeline.py    # parse_dump() — bz2 stream → SQLite (full initial parse)
│   ├── refresh.py     # refresh_dump() — incremental update of existing database
│   ├── verify.py      # Database integrity check
│   └── cli.py         # `python -m parse.cli` entry point
├── rag/
│   ├── schema.py      # RAG DB schema + connect_rag() (loads sqlite-vec extension)
│   ├── chunker.py     # chunk_article(), extract_categories(), is_redirect()
│   ├── embedder.py    # embed_text() via Ollama; pack/unpack_embedding()
│   ├── embed.py       # `python -m rag.embed` CLI + embed_one() used by embed_worker
│   ├── links.py       # extract_article_links() — wikilink extraction for embed-links
│   └── retriever.py   # retrieve() — dense + sparse + RRF hybrid
├── scripts/
│   └── calibrate_chunks.py  # Sample articles and measure real nomic-embed-text token counts
├── tests/             # Pytest suite
│   └── conftest.py    # Shared fixtures (fixture DB builder, TestClient wiring)
├── templates/         # Jinja2 templates
│   ├── _nav.html              # Shared nav-btn-group partial
│   ├── active_embedding*.html # Active-embedding page + polled panel
│   └── ...                    # base, index, article, wikitext, embed_manager, chunks, …
├── static/            # CSS + vendored KaTeX
├── dumps/             # Downloaded files + parsed databases + jobs.db
│                      # also: {wiki}_rag.db (RAG chunks + vectors, gitignored)
├── pyproject.toml     # Project metadata + ruff config
├── requirements.txt
└── README.md
```

## Performance

**simplewiki (360MB compressed, 394K articles):**
- Download: ~2-5 minutes (depending on network)
- Parse: ~77 seconds
- Database size: ~1.27 GB
- Average article size: ~2.6 KB
- Parsing speed: ~7,200 pages/second

## License

This project is provided as-is for educational and personal use.
