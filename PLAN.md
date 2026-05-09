# Project Status: Local Wikipedia

Complete implementation of a Wikipedia dump downloader, parser, query system with Markdown formatting, and a FastAPI + HTMX web UI for browsing articles in the database.

## Implemented Features

### 1. Download Module ✅
**Status:** Complete  
**Files:** `download/download.py`, `download/test_download.py`

Downloads and verifies Wikipedia dump files from Wikimedia:
- SHA-1 verification against official checksums
- Atomic writes with `.tmp` files
- Progress bars with `tqdm`
- Streaming downloads for multi-GB files
- Smart resume (skips already-verified files)

**Performance:**
- Downloads: 2-5 minutes (network dependent)
- Handles multi-GB compressed dumps

### 2. Parse Module ✅
**Status:** Complete  
**Files:** `parse/parse.py`, `parse/test_parse.py`

Extracts articles from compressed Wikipedia XML dumps into SQLite:
- Memory-efficient streaming XML parser
- Batch inserts (1000 articles per batch)
- Namespace filtering (defaults to main articles only)
- SQLite database with full metadata
- Atomic database writes

**Performance (simplewiki):**
- Parse time: ~77 seconds
- Articles: 394,559 inserted from 553,618 pages
- Database size: 1.27 GB
- Throughput: ~7,200 pages/second

**Database Schema:**
```sql
articles (
    page_id, title, namespace, revision_id,
    timestamp, contributor_username, contributor_id,
    comment, text_bytes, text_content
)

parse_metadata (
    wiki, source_file, total_pages, articles_count,
    parse_started_at, parse_completed_at, parse_duration_seconds
)
```

### 3. Query Module ✅
**Status:** Complete  
**Files:** `parse/parse.py` (query_database function)

Execute SQL queries with formatted output:
- Table format (ASCII tables for terminal)
- JSON format (for programmatic use)
- Auto-discovery of database from wiki name
- Error handling and validation

**Example Usage:**
```python
from parse.parse import query_database

# Table format
result = query_database("SELECT title FROM articles LIMIT 5")
print(result)

# JSON format
result = query_database("SELECT * FROM articles LIMIT 1", format="json")
```

### 4. Wikitext to Markdown Converter ✅
**Status:** Complete  
**Files:** `parse/wikitext_to_markdown.py`, `parse/test_wikitext_to_markdown.py`

Converts Wikipedia wikitext to clean, readable Markdown:
- Bold/italic formatting: `'''bold'''` → `**bold**`
- Headings: `== Title ==` → `## Title`
- Links: `[[Page]]` → `[Page](https://simple.wikipedia.org/wiki/Page)`
- Lists: `* Item` → `- Item`
- Strips templates, references, HTML comments
- Uses `mwparserfromhell` for robust parsing

**Coverage:** ~80% of articles render cleanly

**Example:**
```wikitext
'''Python''' is a [[programming language]].
== Features ==
* Easy to learn
```

Converts to:
```markdown
**Python** is a [programming language](https://simple.wikipedia.org/wiki/programming_language).
## Features
- Easy to learn
```

### 5. Helper Scripts ✅
**Status:** Complete

**get_article.py:**
- Retrieves articles by title
- Automatically converts to Markdown
- Shows metadata (title, size, timestamp)

**example_query.py:**
- Demonstrates various query patterns
- Shows both table and JSON output
- Includes search, statistics, and content queries

### 6. Web App ✅
**Status:** Complete
**Files:** `app.py`, `templates/`, `static/style.css`, `test_app.py`

FastAPI + Jinja2 + HTMX single-page app for browsing the parsed database:
- Search bar with debounced as-you-type lookup (prefix match, substring fallback)
- Search results stay visible above the article so users can keep navigating
- Articles rendered server-side via `convert_wikitext_to_markdown` → `markdown.markdown`
- No JavaScript build step; HTMX is loaded from a CDN
- `WIKI_DB` env var overrides the database path (used by tests)

**Routes:**
- `GET /` — single-page UI shell
- `GET /search?q=` — HTML fragment of matching titles (`hx-target="#results"`)
- `GET /article/{title:path}` — HTML fragment with rendered article (`hx-target="#article"`)
- `GET /static/*` — static files (CSS)

**Run:**
```bash
uvicorn app:app --reload
# open http://127.0.0.1:8000
```

**Test coverage:** 12 tests using FastAPI's `TestClient` against a hermetic
in-tmp-path SQLite fixture (no real dump required).

## Usage

### Complete Workflow

```bash
# 1. Download Wikipedia dump
python download/download.py --wiki simplewiki

# 2. Parse into SQLite
python parse/parse.py --wiki simplewiki

# 3a. Query articles from the CLI
python get_article.py "Python (programming language)"
python example_query.py

# 3b. Or browse via the web app
uvicorn app:app --reload
# then open http://127.0.0.1:8000

# 4. Direct SQL queries
sqlite3 dumps/simplewiki.db "SELECT COUNT(*) FROM articles;"
```

## Test Coverage

**Download tests:** 17 tests passing
**Parse tests:** 28 tests passing
**Converter tests:** 32 tests passing (1 skipped)
**Web app tests:** 12 tests passing
**Total:** 89 tests passing, 1 skipped

```bash
# Run all tests
pytest

# Run specific module tests
pytest download/test_download.py -v
pytest parse/test_parse.py -v
pytest parse/test_wikitext_to_markdown.py -v
```

## Dependencies

```
httpx>=0.28.1            # HTTP client
tqdm>=4.67.3             # Progress bars
pytest>=9.0.3            # Testing
respx>=0.23.1            # HTTP mocking
mwparserfromhell>=0.6    # Wikitext parser
fastapi>=0.115           # Web framework
uvicorn[standard]>=0.32  # ASGI server
jinja2>=3.1              # HTML templating
markdown>=3.7            # Markdown -> HTML rendering
```

## Architecture

### Data Flow
```
Wikipedia → download.py → dumps/*.xml.bz2 → parse.py → dumps/*.db
                                                          │
                          ┌───────────────────────────────┼───────────────────────────────┐
                          ▼                               ▼                               ▼
                 query_database()                 get_article.py                    app.py (FastAPI)
                 (SQL → table/JSON)               (CLI Markdown output)             (browser UI: search + render)
```

### Module Organization
```
download/           # Download and verify dumps
├── download.py
└── test_download.py

parse/              # Parse, query, and convert
├── parse.py                    # XML → SQLite
├── wikitext_to_markdown.py     # Wikitext → Markdown
├── test_parse.py
└── test_wikitext_to_markdown.py

dumps/              # Data directory
├── *.xml.bz2       # Downloaded dumps
├── *.txt.bz2       # Index files
└── *.db            # SQLite databases

app.py              # FastAPI web app (search + article view)
templates/          # Jinja2 templates
├── base.html
├── index.html
├── search_results.html
└── article.html
static/
└── style.css       # Web app styling
test_app.py         # Web app test suite

get_article.py      # CLI article viewer
example_query.py    # Query examples
```

## Performance Summary

### simplewiki (360 MB compressed)
- **Download:** ~2-5 minutes
- **Parse:** ~77 seconds
- **Database:** 1.27 GB
- **Articles:** 394,559
- **Query speed:** Instant (indexed)
- **Conversion:** ~1,000 articles/second

## Future Enhancements (Not Implemented)

Potential improvements:
- Full-text search (FTS5) — current search is LIKE-based prefix + substring
- Incremental updates
- Link graph analysis
- Category extraction
- Image download
- Multi-wiki support in single database
- Web app: wiki picker, recent-articles list, article history, dark mode

## Project History

1. **Initial commit:** Download module with SHA-1 verification
2. **Second commit:** Parse module with SQLite storage
3. **Third commit:** Query function with table/JSON output
4. **Fourth commit:** Wikitext to Markdown converter
5. **Fifth change:** FastAPI + HTMX web app (search + article view)

---

**Last Updated:** 2026-05-08
**Status:** Production ready
**Test Coverage:** 89/90 passing (1 skipped)
