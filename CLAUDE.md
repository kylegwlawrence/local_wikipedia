# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Wikipedia dump downloader, parser, and browser-based reader that:
1. Downloads and SHA-1-verifies multistream dump files from Wikimedia
2. Parses compressed XML dumps and extracts articles
3. Stores articles in a local SQLite database for querying
4. Serves a FastAPI + HTMX web UI for searching and reading articles as Markdown

Downloads both the article dump (`.xml.bz2`) and its index (`.txt.bz2`) to the `dumps/` directory, then parses the XML to create a searchable SQLite database. The web app (`app.py`) reads from that database and renders articles via the wikitext → Markdown → HTML pipeline.

## Setup and Dependencies

The project uses a Python virtual environment (`.venv`) with these dependencies:
- `httpx` (0.28.1) - HTTP client for downloading
- `tqdm` (4.67.3) - Progress bar display
- `pytest` (9.0.3) - Testing framework
- `respx` (0.23.1) - HTTP mocking for tests
- `mwparserfromhell` (0.6+) - MediaWiki wikitext parser for Markdown conversion
- `fastapi` (0.115+) - Web framework for the browser UI
- `uvicorn[standard]` (0.32+) - ASGI server for FastAPI
- `jinja2` (3.1+) - HTML templating
- `markdown` (3.7+) - Markdown to HTML rendering

To set up from scratch:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Common Commands

**Download Wikipedia dumps** (defaults to simplewiki):
```bash
python download/download.py
```

**Download a specific wiki**:
```bash
python download/download.py --wiki enwiki
```

**Parse dump into SQLite**:
```bash
python parse/parse.py
python parse/parse.py --wiki simplewiki
```

**Verify database**:
```bash
python parse/parse.py --verify-only --database dumps/simplewiki.db
```

**Query the database**:
```bash
# Using Python function
python3 -c "from parse.parse import query_database; print(query_database('SELECT COUNT(*) FROM articles'))"

# Using example script
python example_query.py

# Get article with Markdown formatting
python get_article.py "Python (programming language)"

# Using SQLite CLI
sqlite3 dumps/simplewiki.db "SELECT COUNT(*) FROM articles;"
sqlite3 dumps/simplewiki.db "SELECT title FROM articles WHERE title LIKE 'Python%';"
```

**Run web app**:
```bash
uvicorn app:app --reload
# then open http://127.0.0.1:8000

# Override the database path:
WIKI_DB=dumps/enwiki.db uvicorn app:app --reload
```

**Run all tests**:
```bash
pytest
```

**Run download tests**:
```bash
pytest download/test_download.py -v
```

**Run parse tests**:
```bash
pytest parse/test_parse.py -v
pytest parse/test_wikitext_to_markdown.py -v
```

**Run web app tests**:
```bash
pytest test_app.py -v
```

**Run a specific test**:
```bash
pytest download/test_download.py::TestHashFile::test_known_content -v
pytest parse/test_parse.py::TestParseDump::test_happy_path -v
```

## Architecture

### Download Module (`download/download.py`)
- `fetch_sha1sums()` - Fetches and parses Wikimedia's SHA-1 manifest, filtering for target files
- `verify_existing()` - Checks if a file already exists and matches expected SHA-1
- `download_with_verify()` - Streams download with progress bar, writes to `.tmp` first, verifies SHA-1, then atomically renames to prevent partial files
- `_hash_file()` - Computes SHA-1 in chunks to handle large files without loading into memory
- `main()` - CLI entry point that orchestrates fetch → verify/download → summary

**Key design patterns**:
- Atomic writes via temp files (`.tmp` suffix) to prevent corrupt state on interruption
- Streaming downloads with chunked hashing to handle multi-GB files
- Skip-if-verified to avoid re-downloading already-correct files
- Two-space-separated manifest parsing for Wikimedia's sha1sums format

### Parse Module (`parse/parse.py`)
- `parse_dump()` - Main parser that extracts articles from compressed XML dumps into SQLite
- `query_database()` - Execute SQL queries with table or JSON output format
- `_create_schema()` - Creates database tables with indexes
- `_parse_page_element()` - Extracts article data from XML `<page>` elements
- `_batch_insert_articles()` - Batch inserts for performance (1000 articles per batch)
- `_format_table()` - Formats query results as ASCII table
- `verify_database()` - Checks database integrity and returns statistics
- `main()` - CLI entry point with --wiki, --dump, --database, --verify-only flags

### Wikitext Converter (`parse/wikitext_to_markdown.py`)
- `convert_wikitext_to_markdown()` - Converts Wikipedia wikitext to clean Markdown
- `_convert_bold_italic()` - Converts '''bold''' and ''italic'' to Markdown
- `_convert_headings()` - Converts == Heading == to ## Heading
- `_convert_links()` - Converts [[Page]] to Markdown links
- `_convert_lists()` - Converts * and # lists to Markdown format
- `_strip_templates()` - Removes {{template}} syntax
- `_strip_refs()` - Removes <ref> citation tags
- `_clean_extra_markup()` - Removes HTML and extra whitespace

**Conversion features**:
- Automatic wikitext to Markdown conversion for readable output
- Handles bold, italic, headings, links, lists
- Strips templates, references, comments, categories
- Graceful fallback for malformed wikitext
- Uses mwparserfromhell for robust parsing

**Key design patterns**:
- Memory-efficient streaming with `xml.etree.ElementTree.iterparse()`
- Decompresses bz2 on-the-fly without creating intermediate files
- Element clearing (`elem.clear()`) to prevent memory buildup
- Batch commits for optimal database performance
- Atomic database writes (`.db.tmp` → rename)
- Namespace filtering (default: namespace=0 for main articles only)

**Database schema**:
- `articles` table with page_id, title, namespace, revision_id, timestamp, contributor info, text_content
- Indexes on title, namespace, timestamp for fast queries
- `parse_metadata` table tracks parse runs (wiki, source file, counts, duration)
- SQLite tuning: WAL mode, page_size=4096, synchronous=NORMAL

### Web App (`app.py`)
- `index()` — `GET /`, renders the single-page UI shell (`index.html`)
- `search()` — `GET /search?q=`, returns the `search_results.html` fragment
- `article()` — `GET /article/{title:path}`, returns the `article.html` fragment
- `_search_titles()` — prefix-first title lookup with substring fallback (LIKE-based)
- `_fetch_article()` — exact-title row lookup
- `_connect()` — per-request SQLite connection; raises 503 if the DB file is missing
- `_db_path()` — resolves the DB path, honoring the `WIKI_DB` env var

**Stack**:
- FastAPI for the HTTP layer
- Jinja2 templates in `templates/` (`base.html`, `index.html`, `search_results.html`, `article.html`)
- HTMX (loaded from a CDN in `base.html`) for live search and click-to-load behavior — no JS build step
- `markdown` library to convert the output of `convert_wikitext_to_markdown` to HTML
- Static assets in `static/` (just `style.css`)

**Key design patterns**:
- Per-request SQLite connections (cheap; avoids cross-thread issues with FastAPI's threadpool)
- `WIKI_DB` env var indirection so tests can swap the database without restarting
- Fragment-only responses for `/search` and `/article` so HTMX can swap them directly into the page
- Search uses two passes: indexed prefix LIKE first, substring LIKE only as a fallback
- Trusted-HTML rendering: the `|safe` filter in `article.html` is sound because the HTML comes from our own converter, not user input
- `{title:path}` route converter so titles containing slashes round-trip cleanly

### Test Coverage
**download/test_download.py**:
- Uses `respx` to mock HTTP calls for deterministic testing
- Tests all public functions with happy path and error cases
- Monkeypatching used in `TestMain` to isolate filesystem operations to `tmp_path`

**parse/test_parse.py**:
- Helper functions to generate test XML dumps
- Tests schema creation, XML parsing, batch inserts, and full parse workflow
- Verifies namespace filtering, atomic writes, and batch commit behavior
- Uses `tmp_path` for filesystem isolation

**parse/test_wikitext_to_markdown.py**:
- Tests all wikitext conversion functions
- Tests bold, italic, headings, links, lists conversion
- Tests template/reference stripping
- Integration tests with real article structures
- 32 comprehensive tests

**test_app.py**:
- Uses FastAPI's `TestClient` against a hermetic in-tmp-path SQLite fixture
- `WIKI_DB` env var is set per-test via `monkeypatch.setenv` so tests do not require a real Wikipedia dump
- Covers index render, empty/prefix/substring/no-match search, article rendering with HTML output, URL-encoded titles, 404 on missing article, and 503 when the DB file is absent
- 12 tests

**File organization**:
- Source code in `download/` and `parse/` directories
- Web app at the repo root: `app.py`, `templates/`, `static/`, `test_app.py`
- Downloads and databases in `dumps/` (created automatically)
- Helper scripts: `get_article.py`, `example_query.py` for querying
- Virtual environment in `.venv/` (gitignored)
- Import pattern: `from parse.parse import ...` for parse module tests; `import app as web_app` in `test_app.py`

**Article display**:
- `get_article.py` - Retrieves and displays articles in clean Markdown format
- Automatically converts wikitext to readable Markdown
- Shows article title, size, last edit timestamp, and formatted content
