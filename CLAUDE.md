# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Wikipedia dump downloader that fetches and SHA-1-verifies the latest multistream dump files from Wikimedia. Downloads both the article dump (`.xml.bz2`) and its index (`.txt.bz2`) to the `dumps/` directory.

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

**Run all tests**:
```bash
pytest download/test_download.py
```

**Run a specific test class**:
```bash
pytest download/test_download.py::TestDownloadWithVerify
```

**Run a single test**:
```bash
pytest download/test_download.py::TestHashFile::test_known_content -v
```

## Architecture

**Main module** (`download/download.py`):
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

**Test coverage** (`download/test_download.py`):
- Uses `respx` to mock HTTP calls for deterministic testing
- Tests all public functions with happy path and error cases
- Monkeypatching used in `TestMain` to isolate filesystem operations to `tmp_path`
- Import statement at top: `from download import ...` assumes running from project root

**File organization**:
- All source code in `download/` directory
- Downloads go to `dumps/` (created automatically)
- Virtual environment in `.venv/` (gitignored)
