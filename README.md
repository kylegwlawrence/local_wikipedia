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

## Usage

### Step 1: Download Wikipedia Dumps

Download dumps for Simple English Wikipedia (default):
```bash
python download/download.py
```

Download dumps for a specific wiki:
```bash
python download/download.py --wiki enwiki
```

Downloaded files are saved to the `dumps/` directory.

### Step 2: Parse Dumps into SQLite

Parse the downloaded dump into a SQLite database:
```bash
python parse/parse.py
```

Parse a specific wiki:
```bash
python parse/parse.py --wiki simplewiki
```

Verify an existing database:
```bash
python parse/parse.py --verify-only --database dumps/simplewiki.db
```

### Step 3: Query the Database

**Option A: Python Function (Recommended)**

Use the `query_database()` function in your Python scripts:

```python
from parse.parse import query_database

# Table format (default) - formatted for terminal display
result = query_database(
    "SELECT title, text_bytes FROM articles WHERE title LIKE 'Python%' LIMIT 5"
)
print(result)

# JSON format - for programmatic use
result = query_database(
    "SELECT title, page_id FROM articles LIMIT 3",
    format="json"
)
# Returns: [{"title": "April", "page_id": 1}, ...]

# Auto-discovers database from wiki name
result = query_database(
    "SELECT COUNT(*) FROM articles",
    wiki="simplewiki"
)
```

Run the example script:
```bash
python example_query.py
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
pytest download/test_download.py -v
```

Run parse tests only:
```bash
pytest parse/test_parse.py -v
```

Run a specific test class:
```bash
pytest download/test_download.py::TestDownloadWithVerify
```

## Dependencies

- **httpx** (0.28.1) - Async HTTP client for downloads
- **tqdm** (4.67.3) - Terminal progress bars
- **pytest** (9.0.3) - Testing framework
- **respx** (0.23.1) - HTTP mocking for tests

## Project Structure

```
.
├── download/
│   ├── download.py       # Main downloader module
│   └── test_download.py  # Download test suite
├── parse/
│   ├── parse.py         # Article parser and SQLite storage
│   └── test_parse.py    # Parse test suite
├── dumps/               # Downloaded files and databases
├── requirements.txt     # Python dependencies
├── PLAN.md             # Implementation plan
└── README.md           # This file
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
