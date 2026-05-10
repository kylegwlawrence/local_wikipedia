# Wikipedia Dump Downloader & Parser

A Python tool for downloading, verifying, and parsing Wikipedia dump files from Wikimedia. Downloads dumps with SHA-1 verification, then extracts articles into a local SQLite database for easy querying.

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
- Indexes for fast title/namespace/timestamp queries

### Wikitext Converter
- Converts Wikipedia wikitext to clean, readable HTML
- Automatic conversion when displaying articles
- Handles bold, italic, headings, links, lists, tables, code blocks
- Renders mathematical formulas using KaTeX (vendored locally — no internet required)
- Strips templates, references, and HTML markup

### Web App
- FastAPI + Jinja2 + HTMX single-page UI for browsing the parsed database
- Search bar with debounced as-you-type title lookup (prefix match, with substring fallback)
- Articles rendered server-side: wikitext → Markdown → HTML
- No JavaScript build step; all logic stays in Python

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

## Usage

### Step 1: Download Wikipedia Dumps

Download dumps for Simple English Wikipedia (default):
```bash
python -m download.download
```

Download dumps for a specific wiki:
```bash
python -m download.download --wiki enwiki
```

Downloaded files are saved to the `dumps/` directory.

### Step 2: Parse Dumps into SQLite

Parse the downloaded dump into a SQLite database:
```bash
python -m parse.cli
```

Parse a specific wiki:
```bash
python -m parse.cli --wiki simplewiki
```

Verify an existing database:
```bash
python -m parse.cli --verify-only --database dumps/simplewiki.db
```

### Step 3: Query the Database

**Option A: Web App**

Launch the FastAPI app for a browser-based search UI:

```bash
uvicorn app:app --reload
```

Then open `http://127.0.0.1:8000`. Type into the search box; matching titles appear below as you type, and clicking one renders the article as Markdown-formatted HTML.

To point the app at a different database file, set `WIKI_DB`:

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
6. Creates indexes for fast querying
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

- **httpx** (0.28.1) - Async HTTP client for downloads
- **tqdm** (4.67.3) - Terminal progress bars
- **pytest** (9.0.3) - Testing framework
- **respx** (0.23.1) - HTTP mocking for tests
- **mwparserfromhell** (0.6+) - MediaWiki wikitext parser
- **fastapi** (0.115+) - Web framework for the browser UI
- **uvicorn** (0.32+) - ASGI server for FastAPI
- **jinja2** (3.1+) - HTML templating

## Project Structure

```
.
├── app.py             # FastAPI web app (routes)
├── paths.py           # Project paths (BASE_DIR, DUMPS_DIR)
├── db.py              # sqlite3 connect() helper
├── render.py          # Wikitext → HTML converter
├── download/
│   └── download.py    # Dump downloader + SHA-1 verifier
├── parse/
│   ├── schema.py      # SQLite schema + PRAGMAs
│   ├── xml_reader.py  # MediaWiki <page> element extractor
│   ├── pipeline.py    # parse_dump() — bz2 stream → SQLite
│   ├── verify.py      # Database integrity check
│   └── cli.py         # `python -m parse.cli` entry point
├── tests/             # Pytest suite
├── templates/         # Jinja2 templates
├── static/            # CSS
├── dumps/             # Downloaded files + parsed databases
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
