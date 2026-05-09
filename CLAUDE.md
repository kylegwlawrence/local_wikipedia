# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Wikipedia dump downloader, parser, and browser-based reader that:
1. Downloads and SHA-1-verifies multistream dump files from Wikimedia
2. Parses compressed XML dumps and extracts articles into SQLite
3. Serves a FastAPI + HTMX web UI for searching and reading articles

The web app (`app.py`) reads from the SQLite database and renders articles via the **wikitext → HTML** pipeline in `parse/wikitext_to_markdown.py`.

## Setup and Dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Dependencies: `httpx`, `tqdm`, `pytest`, `respx`, `mwparserfromhell`, `fastapi`, `uvicorn[standard]`, `jinja2`.

## Common Commands

```bash
# Download dumps (defaults to simplewiki)
python download/download.py
python download/download.py --wiki enwiki

# Parse dump into SQLite
python parse/parse.py --wiki simplewiki

# Verify database integrity
python parse/parse.py --verify-only --database dumps/simplewiki.db

# Run web app (open http://127.0.0.1:8000)
uvicorn app:app --reload
WIKI_DB=dumps/enwiki.db uvicorn app:app --reload

# Tests
pytest
pytest parse/test_wikitext_to_markdown.py -v
pytest download/test_download.py::TestHashFile::test_known_content -v
```

## Architecture

### Data flow

```
Wikimedia → download.py → dumps/*.xml.bz2
                       → parse.py → dumps/*.db (SQLite)
                                  → app.py → browser
```

The web app is a single-page UI: `GET /` serves the shell, HTMX drives `GET /search?q=` and `GET /article/{title:path}` as fragment swaps into `#results` and `#article` — no JS build step.

### Wikitext converter (`parse/wikitext_to_markdown.py`)

The converter outputs HTML directly (not Markdown). `convert_wikitext_to_markdown` is a backward-compat alias for `convert_wikitext_to_html`.

Pipeline order matters — stages are applied in sequence:

1. `mwparserfromhell.parse()` — structured parse of wikicode
2. `_convert_code_templates()` — converts `{{code}}`, `{{codes}}`, `{{tt}}` etc. to `<code>` tags **before** `_strip_templates()` removes all other templates
3. `_strip_templates / _strip_refs / _strip_comments / _strip_categories` — remove noise
4. `_extract_syntaxhighlight()` — replaces `<syntaxhighlight>` blocks with `<div>` placeholders so subsequent converters don't process the code content (e.g. `#` comments becoming lists)
5. `_convert_tables / _convert_lists / _convert_headings / _convert_bold_italic / _convert_links`
6. `_wrap_paragraphs()` — wraps bare text in `<p>` tags; recognises block-level tags including all table elements so they're never double-wrapped
7. `_restore_code_blocks()` — swaps placeholders back to `<pre><code>` blocks
8. `_clean_extra_markup()`

**Non-obvious decisions**:
- Link labels (`[[Page|Label]]`) are **not** HTML-escaped so that inline `<code>` and other tags in the label survive
- List item content is **not** HTML-escaped for the same reason
- The syntaxhighlight placeholder is a `<div data-codeblock="n">` so `_wrap_paragraphs` treats it as block-level and doesn't wrap it in `<p>`
- All tables default to `class="wikitable"` when no class is specified in the wikitext

### Parse module (`parse/parse.py`)

- Streams bz2-compressed XML with `iterparse()` and clears elements after use to stay memory-efficient on multi-GB dumps
- Batch inserts (1000 articles/batch) with WAL-mode SQLite
- Atomic database writes: parsed to `.db.tmp` then renamed
- Namespace 0 only by default (main articles)

### Web app (`app.py`)

- Per-request SQLite connections (avoids cross-thread issues with FastAPI's threadpool)
- `WIKI_DB` env var overrides the DB path — tests use `monkeypatch.setenv` to point at a fixture DB without restarting the app
- `{title:path}` route converter so titles with slashes round-trip cleanly
- `|safe` in `article.html` is intentional: the HTML comes from our own converter, not user input

### Test organisation

| File | Scope |
|---|---|
| `download/test_download.py` | `respx`-mocked HTTP; filesystem isolated to `tmp_path` |
| `parse/test_parse.py` | XML dump generation helpers; namespace filtering, atomic writes |
| `parse/test_wikitext_to_markdown.py` | All converter stages; 60+ tests |
| `test_app.py` | `TestClient` against a hermetic fixture DB; 17 tests |
