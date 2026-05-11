# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Wikipedia dump downloader, parser, and browser-based reader that:
1. Downloads and SHA-1-verifies multistream dump files from Wikimedia
2. Parses compressed XML dumps and extracts articles into SQLite
3. Serves a FastAPI + HTMX web UI for searching and reading articles

The web app (`app.py`) reads from the SQLite database and renders articles via the **wikitext → HTML** pipeline in `render/`.

## Layout

```
local_wikipedia/
  app.py            FastAPI app (routes + redirect/search/refresh helpers)
  paths.py          BASE_DIR, DUMPS_DIR, DEFAULT_WIKI, JOBS_DB, KNOWN_WIKIS
  db.py             connect(path) -> sqlite3.Connection (Row factory)
  jobs.py           CRUD helpers for refresh_jobs table in dumps/jobs.db
  worker.py         background subprocess: download → refresh → FTS rebuild
  render/           wikitext → HTML converter (package)
    __init__.py     public API: convert_wikitext_to_html
    pipeline.py     orchestrator — ordered stage list
    data.py         static data tables (LANG_NAMES, INDICATORS, …)
    templates.py    wikicode-level template handlers (infobox, cite, lang, math, …)
    tables.py       wikitext {| ... |} table → HTML
    blocks.py       lists, headings, paragraph wrapping
    inline.py       bold/italic, wikilinks
    protect.py      syntaxhighlight + math block extraction/restore
    strip.py        strip templates, refs, comments, categories
  download/
    download.py     dump downloader + SHA-1 verifier
  parse/
    schema.py       articles + articles_archive + parse_metadata DDL, PRAGMAs
    xml_reader.py   parse_page_element, MediaWiki namespace constants
    pipeline.py     parse_dump, _batch_insert_articles, _record_metadata
    refresh.py      refresh_dump — incremental update of an existing database
    verify.py       verify_database
    cli.py          argparse main(), _find_latest_dump
  tests/            pytest suite (mirrors source layout)
  templates/, static/
  dumps/            (gitignored) downloaded .xml.bz2 + parsed .db + jobs.db
```

## Setup and Dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Dependencies: `httpx`, `tqdm`, `pytest`, `respx`, `mwparserfromhell`, `fastapi`, `uvicorn[standard]`, `jinja2`.

## Common Commands

```bash
# Download dumps (defaults to enwiki)
python -m download.download
python -m download.download --wiki simplewiki

# Parse dump into SQLite (full initial parse)
python -m parse.cli --wiki enwiki

# Verify database integrity
python -m parse.cli --verify-only --database dumps/enwiki.db

# Add FTS5 to an existing database without re-parsing
python -m parse.cli --rebuild-fts --database dumps/enwiki.db

# Run web app (open http://127.0.0.1:8000)
uvicorn app:app --reload
WIKI_DB=dumps/enwiki.db uvicorn app:app --reload

# Tests
pytest
pytest tests/test_render.py -v
pytest tests/test_download.py::TestHashFile::test_known_content -v
```

## Architecture

### Data flow

```
Wikimedia → download.py → dumps/*.xml.bz2
                       → parse.py → dumps/*.db (SQLite)
                                  → app.py → browser
```

The web app is a single-page UI: `GET /` serves the shell, HTMX drives `GET /search?q=` and `GET /article/{title:path}` as fragment swaps into `#results` and `#article` — no JS build step.

### Wikitext converter (`render/`)

Entry point: `render.convert_wikitext_to_html`. The pipeline is implemented in `render/pipeline.py`; each stage lives in its own module.

Pipeline order matters — stages are applied in sequence:

1. `mwparserfromhell.parse()` — structured parse of wikicode
2. **Wikicode-level template handlers** (`templates.py`) — infobox, math, code, lang, indicator, section-link, citation, ref collection, reflist. Must run before stripping so output is preserved.
3. **Stripping** (`strip.py`) — `strip_templates / strip_refs / strip_comments / strip_categories` remove any remaining noise.
4. Flatten to string.
5. **Protect** (`protect.py`) — replace `<syntaxhighlight>` and `<math>` blocks with `<div>` placeholders so subsequent converters don't mangle code/LaTeX content (e.g. `#` becoming a list item).
6. **Block-level converters** (`tables.py`, `blocks.py`) — tables, lists, headings.
7. **Inline converters** (`inline.py`) — bold/italic, wikilinks.
8. **Paragraph wrapping** (`blocks.py`) — wraps bare text in `<p>`; recognises all block-level tags so they're never double-wrapped.
9. **Restore** (`protect.py`) — swap placeholders back to `<pre><code>` and KaTeX delimiters.
10. **Cleanup** (`pipeline.py`) — collapse blank lines, trim trailing whitespace.

**Non-obvious decisions**:
- Wikilinks point at the local `/article/{title}` endpoint and carry HTMX attributes (`hx-get`, `hx-target="#article"`, `hx-swap="innerHTML"`) so clicks load fragments in-place rather than full-page navigating
- Link target titles are first-letter-capitalised (MediaWiki convention: `[[python]]` resolves to `Python`); `#anchor` is split off the lookup target and re-attached as a URL fragment
- Link labels (`[[Page|Label]]`) are **not** HTML-escaped so that inline `<code>` and other tags in the label survive
- List item content is **not** HTML-escaped for the same reason
- The syntaxhighlight placeholder is a `<div data-codeblock="n">` so `wrap_paragraphs` treats it as block-level and doesn't wrap it in `<p>`
- All tables default to `class="wikitable"` when no class is specified in the wikitext
- `_render_lang(code, text)` in `templates.py` is the single source for `{{lang}}`/`{{langx}}` rendering — used by both the infobox value renderer and the body-text template handler
- `_render_ref_body(contents)` in `templates.py` is the single source for ref-content rendering — used by both `collect_inline_refs` and `convert_reflist_template`

### Parse module (`parse/`)

- `pipeline.parse_dump` streams bz2-compressed XML with `iterparse()` and clears elements after use to stay memory-efficient on multi-GB dumps
- Batch inserts (1000 articles/batch) with WAL-mode SQLite (configured in `schema.create_schema`)
- After all batches are committed, `parse_dump` issues `INSERT INTO articles_fts(articles_fts) VALUES('rebuild')` to bulk-populate the FTS5 index in one pass
- Atomic database writes: parsed to `.db.tmp` then renamed; `try/finally` ensures the tmp file is removed on any failure including `KeyboardInterrupt`
- Namespace 0 only by default (main articles)
- `cli.main` is the CLI entry; `cli._find_latest_dump` reads `cli.DUMPS_DIR` at call time so tests can monkeypatch it
- `--rebuild-fts` flag adds/rebuilds the FTS5 index on an existing database without re-parsing

### FTS5 title search (`articles_fts`)

- Virtual table: `CREATE VIRTUAL TABLE articles_fts USING fts5(title, content=articles, content_rowid=page_id, tokenize='trigram')`
- `content=articles` makes it a content table — no data duplication, FTS index references the backing `articles` table
- `tokenize='trigram'` supports both prefix and substring matches (e.g. searching "ril" finds "April") in a single indexed query
- Results are ordered by BM25 relevance rank rather than alphabetically
- Queries shorter than 3 characters fall back to an indexed prefix LIKE on `idx_articles_title` (trigram requires ≥3 chars)

### Web app (`app.py`)

- Per-request SQLite connections (avoids cross-thread issues with FastAPI's threadpool)
- `WIKI_DB` env var overrides the DB path — tests use `monkeypatch.setenv` to point at a fixture DB without restarting the app
- `DEFAULT_DB` is derived from `paths.db_path_for(DEFAULT_WIKI)` so the default database path is never built inline twice
- `{title:path}` route converter so titles with slashes round-trip cleanly
- `|safe` in `article.html` is intentional: the HTML comes from our own converter, not user input
- `_search_titles` uses FTS5 MATCH via `_escape_fts5` (wraps input in phrase quotes); short queries (<3 chars) fall back to prefix LIKE
- `_fetch_article()` follows `#REDIRECT [[Target]]` chains up to `REDIRECT_MAX_HOPS` (5) and returns the original title as `redirected_from` so the template can show a "Redirected from X" note. A cycle guard surfaces a 404 rather than hanging.
- `GET /wikitext/{title}` returns raw wikitext in a `<pre>` block — toggled from the article view
- `GET /switch-wiki?to=` sets a `wiki_pref` cookie (1-year max-age) and redirects to `/`; `_active_wiki()` reads it on every request
- `POST /refresh/{wiki}` creates a job row (inside `BEGIN IMMEDIATE` to avoid duplicate active jobs), then spawns `worker.py` as a detached subprocess (`start_new_session=True`) so it outlives the HTTP connection
- `GET /refresh/status/{wiki}` returns the latest job row as the `refresh_panel.html` partial — polled by HTMX while a job is active

### Refresh job system (`jobs.py`, `worker.py`, `parse/refresh.py`)

- `jobs.db` in `dumps/` stores `refresh_jobs` rows; separate from wiki databases so it's never overwritten by a re-parse
- Job lifecycle statuses: `pending` → `downloading` → `parsing` → `rebuilding` → `complete` / `failed`
- `worker.py` runs as a standalone script (`python worker.py --wiki X --job-id N`); redirects stdout/stderr to `dumps/{wiki}_refresh.log` (line-buffered)
- `refresh_dump` (in `parse/refresh.py`) operates in-place on the existing database — it does **not** atomically replace it. For each article in the dump it skips (same `revision_id`), archives-then-updates (changed revision), or inserts (new `page_id`). The archive step writes the old row to `articles_archive` before overwriting so a crash leaves old data intact.
- Batch size matches `parse/pipeline.py`'s `BATCH_SIZE` (1000). After each batch, `jobs.update_job` writes running totals to `jobs.db` so the status panel can show live progress.
- FTS rebuild happens in `worker.py` after `refresh_dump` returns — same `INSERT INTO articles_fts(articles_fts) VALUES('rebuild')` used by the initial parse.

### Test organisation

| File | Scope |
|---|---|
| `tests/test_download.py` | `respx`-mocked HTTP; filesystem isolated to `tmp_path` |
| `tests/test_pipeline.py` | XML dump generation helpers; namespace filtering, atomic writes |
| `tests/test_render.py` | All wikitext → HTML converter stages |
| `tests/test_app.py` | `TestClient` against a hermetic fixture DB |
