# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Wikipedia dump downloader and parser that:
1. Downloads and SHA-1-verifies multistream dump files from Wikimedia
2. Parses compressed XML dumps and extracts articles
3. Stores articles in a local SQLite database for querying

Downloads both the article dump (`.xml.bz2`) and its index (`.txt.bz2`) to the `dumps/` directory, then parses the XML to create a searchable SQLite database.

## Setup and Dependencies

The project uses a Python virtual environment (`.venv`) with these dependencies:
- `httpx` (0.28.1) - HTTP client for downloading
- `tqdm` (4.67.3) - Progress bar display
- `pytest` (9.0.3) - Testing framework
- `respx` (0.23.1) - HTTP mocking for tests

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
sqlite3 dumps/simplewiki.db "SELECT COUNT(*) FROM articles;"
sqlite3 dumps/simplewiki.db "SELECT title FROM articles WHERE title LIKE 'Python%';"
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
- `_create_schema()` - Creates database tables with indexes
- `_parse_page_element()` - Extracts article data from XML `<page>` elements
- `_batch_insert_articles()` - Batch inserts for performance (1000 articles per batch)
- `verify_database()` - Checks database integrity and returns statistics
- `main()` - CLI entry point with --wiki, --dump, --database, --verify-only flags

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

**File organization**:
- Source code in `download/` and `parse/` directories
- Downloads and databases in `dumps/` (created automatically)
- Virtual environment in `.venv/` (gitignored)
- Import pattern: `from parse.parse import ...` for parse module tests
