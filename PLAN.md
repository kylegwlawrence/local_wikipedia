# Implementation Plan: Wikipedia Dump Article Extraction to SQLite

## Context

The project currently downloads Wikipedia dump files from Wikimedia and verifies their integrity via SHA-1 checksums. This enhancement adds article extraction capability to parse the downloaded XML dumps and store structured article data in a SQLite database for local querying and analysis.

**User requirements:**
- Extract articles from multistream Wikipedia dumps (currently have simplewiki-20260501, 360MB compressed)
- Store in SQLite database with full metadata (title, IDs, timestamps, contributors, text)
- Focus on main articles only (namespace 0, excludes talk pages and other non-article pages)
- Basic indexes for title/namespace queries (no full-text search needed)

## Recommended Approach

### Module Structure
Create new `parse/` directory alongside existing `download/` module:
- `parse/parse.py` - Main parsing and database logic
- `parse/test_parse.py` - Test suite following existing patterns

**Rationale:** Maintains separation of concerns. Download and parse are distinct operations that can be used independently.

### SQLite Schema

```sql
CREATE TABLE articles (
    page_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    namespace INTEGER NOT NULL DEFAULT 0,
    revision_id INTEGER NOT NULL,
    parent_revision_id INTEGER,
    timestamp TEXT NOT NULL,
    contributor_username TEXT,
    contributor_id INTEGER,
    comment TEXT,
    text_bytes INTEGER,
    text_content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_articles_title ON articles(title);
CREATE INDEX idx_articles_namespace ON articles(namespace);
CREATE INDEX idx_articles_timestamp ON articles(timestamp);

CREATE TABLE parse_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wiki TEXT NOT NULL,
    source_file TEXT NOT NULL,
    total_pages INTEGER NOT NULL,
    articles_count INTEGER NOT NULL,
    parse_started_at TEXT NOT NULL,
    parse_completed_at TEXT NOT NULL,
    parse_duration_seconds REAL NOT NULL
);
```

**Design decisions:**
- `page_id` as PRIMARY KEY (natural Wikipedia identifier)
- Store namespace field but filter to namespace=0 during parsing
- Single revision per article (multistream dumps contain latest revision only)
- Text stored as raw wikitext (no preprocessing)
- Metadata table tracks parse runs for verification

### XML Parser: xml.etree.ElementTree.iterparse()

**Rationale:** 
- Standard library (no new dependencies)
- Memory-efficient streaming parser (critical for multi-GB files)
- `iterparse()` yields events incrementally, allowing element clearing to prevent memory buildup
- Handles bz2-compressed streams directly via `bz2.open()`

### Core Functions (parse/parse.py)

```python
def parse_dump(
    dump_path: pathlib.Path,
    db_path: pathlib.Path,
    namespace_filter: int = 0,
) -> tuple[int, int]:
    """Parse Wikipedia XML dump and insert into SQLite.
    
    Returns: (total_pages_parsed, articles_inserted)
    Raises: RuntimeError on XML/database errors
    """
```

**Implementation approach:**
1. Open bz2-compressed XML file with streaming decompression
2. Use `ET.iterparse(f, events=('end',))` for memory-efficient parsing
3. For each `<page>` element: extract fields, filter by namespace
4. Batch insert articles (1000 per batch) for performance
5. Clear processed elements to free memory: `elem.clear()`
6. Write to temporary database (`.db.tmp`), then atomic `os.replace()`
7. Display progress with `tqdm` (following existing patterns)

**Other functions:**
- `_create_schema(conn)` - Create tables and indexes
- `_parse_page_element(page_elem)` - Extract article dict from XML element
- `_batch_insert_articles(conn, articles)` - Executemany for batch inserts
- `verify_database(db_path)` - Check integrity, return statistics
- `main(argv)` - CLI entry point with argparse

### CLI Interface

```bash
# Basic usage - parse latest simplewiki dump
python parse/parse.py

# Specify wiki name (auto-discovers dump file)
python parse/parse.py --wiki simplewiki

# Explicit file paths
python parse/parse.py --dump dumps/simplewiki-20260501-pages-articles-multistream.xml.bz2 \
                      --database dumps/simplewiki.db

# Verify existing database
python parse/parse.py --verify-only --database dumps/simplewiki.db
```

**Arguments:**
- `--wiki` - Wiki name for auto-discovery (default: simplewiki)
- `--dump` - Explicit dump file path (optional, auto-discovers if omitted)
- `--database` - Output DB path (default: `dumps/{wiki}.db`)
- `--verify-only` - Verify existing database without parsing

**Auto-discovery logic:** Find latest `{wiki}-*-pages-articles-multistream.xml.bz2` in `dumps/`

### Error Handling Strategy

Follow existing patterns from `download/download.py`:
- Raise `RuntimeError` for business logic errors (XML parsing, database operations)
- Validate file existence before processing
- Use try/finally to cleanup temp files on failure
- Commit in batches; rollback on error
- Skip corrupt pages, log page_id, continue parsing
- Return Unix exit codes: 0=success, 1=failure

### Performance Optimizations

1. **Batch inserts:** Use `executemany()` with `BATCH_SIZE=1000`
2. **Transaction management:** Commit per batch, not per article
3. **Memory management:** Clear elements after processing (`elem.clear()`)
4. **SQLite tuning:**
   - `PRAGMA journal_mode=WAL` (better concurrency)
   - `PRAGMA page_size=4096` (standard page size)
   - `PRAGMA synchronous=NORMAL` (safe with WAL mode)

**Expected performance (simplewiki ~240K articles):**
- Parse time: ~10-15 minutes
- Database size: ~2-3GB
- Memory usage: <100MB (streaming + batch buffer)
- Throughput: ~200-300 articles/second

### Testing Strategy (parse/test_parse.py)

Follow existing test patterns from `test_download.py`:
- Use `pytest` with `tmp_path` fixture for filesystem isolation
- Helper function to generate minimal valid Wikipedia XML test data
- Test classes grouped by function:
  - `TestCreateSchema` - Verify table/index creation
  - `TestParsePageElement` - Test XML → dict extraction
  - `TestBatchInsertArticles` - Test database inserts
  - `TestParseDump` - Integration tests with full parse workflow
  - `TestMain` - CLI argument handling with monkeypatch

**Key test cases:**
- Happy path: valid dump → database created with correct article count
- Namespace filtering: only namespace=0 articles included
- Atomic writes: temp file pattern used, atomic rename on success
- Batch commits: verify batching with >2000 article test dumps
- Error handling: corrupt XML, missing files, database errors
- Memory efficiency: large dump doesn't cause memory issues

### Reused Patterns from Existing Codebase

From `download/download.py`:
- `pathlib.Path` for cross-platform paths
- Atomic writes: `.tmp` suffix + `os.replace()`
- `tqdm` progress bars with `unit` and `desc`
- `argparse` CLI structure with `--wiki` flag
- Type hints throughout
- `RuntimeError` for business logic errors
- Unix exit codes (0/1)
- Constants for magic numbers (`BATCH_SIZE`, similar to `CHUNK_BYTES`)

From `test_download.py`:
- `tmp_path` fixture for filesystem isolation
- `monkeypatch` for patching globals (auto-discovery logic)
- Helper functions to reduce test boilerplate
- Test class organization by function
- Both unit tests (individual functions) and integration tests (main)

## Critical Files

**New files to create:**
- `parse/parse.py` - Main implementation
- `parse/test_parse.py` - Test suite
- `parse/__init__.py` - Empty module marker

**Reference files (patterns to follow):**
- `download/download.py` - Code patterns
- `download/test_download.py` - Test patterns

**Data files:**
- `dumps/simplewiki-20260501-pages-articles-multistream.xml.bz2` - Source dump (360MB)
- `dumps/simplewiki.db` - Target database (will be created)

**Configuration:**
- `requirements.txt` - No changes needed (stdlib-only)

## Verification Steps

After implementation:

1. **Run tests:**
   ```bash
   pytest parse/test_parse.py -v
   ```

2. **Parse simplewiki dump:**
   ```bash
   python parse/parse.py --wiki simplewiki
   ```
   - Should complete in ~10-15 minutes
   - Progress bar should display page count
   - Database should be created at `dumps/simplewiki.db`

3. **Verify database:**
   ```bash
   python parse/parse.py --verify-only --database dumps/simplewiki.db
   ```
   - Should print statistics (article count, sample titles)

4. **Manual SQLite checks:**
   ```bash
   sqlite3 dumps/simplewiki.db
   ```
   ```sql
   SELECT COUNT(*) FROM articles;  -- Should be ~240K for simplewiki
   SELECT title, LENGTH(text_content) FROM articles LIMIT 5;
   SELECT * FROM parse_metadata;  -- Verify metadata recorded
   ```

5. **Memory test with large wiki (optional):**
   - Download enwiki dump (much larger)
   - Verify memory usage stays <500MB during parse
   - Check that progress bar updates smoothly

## Dependencies

No new dependencies required - uses Python standard library only:
- `xml.etree.ElementTree` (XML parsing)
- `bz2` (decompression)
- `sqlite3` (database)
- `pathlib` (paths)
- `argparse` (CLI)
- Existing dependencies: `tqdm` (progress), `pytest` (testing)
