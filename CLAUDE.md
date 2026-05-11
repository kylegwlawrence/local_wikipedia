# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Wikipedia dump downloader, parser, browser-based reader, and RAG pipeline that:
1. Downloads and SHA-1-verifies multistream dump files from Wikimedia
2. Parses compressed XML dumps and extracts articles into SQLite
3. Serves a FastAPI + HTMX web UI for searching and reading articles
4. Chunks and embeds articles for semantic search via a local Ollama model

The web app (`app.py`) reads from the SQLite database and renders articles via the **wikitext → HTML** pipeline in `render/`. The `rag/` package handles offline embedding and retrieval; web UI integration for AI queries is not yet built.

## Layout

```
local_wikipedia/
  app.py            FastAPI app (routes + redirect/search/refresh helpers)
  paths.py          BASE_DIR, DUMPS_DIR, DEFAULT_WIKI, JOBS_DB, KNOWN_WIKIS, rag_db_path_for
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
  rag/              RAG pipeline (offline embedding + retrieval)
    __init__.py
    schema.py       connect_rag(), create_rag_schema() — chunks + vec + FTS tables
    chunker.py      chunk_article(), extract_categories(), is_redirect()
    embedder.py     embed_text() sync/async via Ollama; pack/unpack_embedding()
    embed.py        CLI entry point: python -m rag.embed --wiki X
    retriever.py    retrieve() — dense (sqlite-vec) + sparse (FTS5) + RRF merge
    generator.py    build_prompt(), stream_response() — Ollama chat streaming
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
                    also stores {wiki}_rag.db (RAG chunks + vectors)
```

## Setup and Dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Dependencies: `httpx`, `tqdm`, `pytest`, `respx`, `mwparserfromhell`, `fastapi`, `uvicorn[standard]`, `jinja2`, `sqlite-vec`.

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

# Build RAG index (requires Ollama running with nomic-embed-text pulled)
python -m rag.embed --wiki simplewiki            # full run
python -m rag.embed --wiki simplewiki --limit 100 # smoke test (first 100 articles)
python -m rag.embed --wiki simplewiki --reset     # re-embed from scratch

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
                                  → rag/embed.py → dumps/*_rag.db (chunks + vectors)
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

### RAG pipeline (`rag/`)

Offline embedding pipeline that chunks articles into sections and embeds them using a local Ollama model. Results are stored in `dumps/{wiki}_rag.db` — **separate from the wiki DB** so refresh jobs don't clobber embeddings.

**Chunking** (`rag/chunker.py`):
- Splits raw wikitext on `== Section ==` regex boundaries (20–50× faster than `mwparserfromhell.get_sections()`)
- Strips wikitext markup with `mwparserfromhell.strip_code()` per section to produce plain text
- Max 1,600 chars/chunk (~400 tokens); long sections split at paragraph boundaries first
- Skips redirect articles entirely
- Extracts `[[Category:...]]` names via regex before stripping

**RAG DB schema** (in `dumps/{wiki}_rag.db`):
- `articles_meta` — mirrors `page_id`, `title`, `revision_id`, `categories` from the wiki DB; enables incremental re-embedding keyed on `revision_id`
- `chunks` — one row per text chunk: `page_id`, `section`, `chunk_index`, `text`, `text_length`
- `chunks_fts` — FTS5 virtual table with `tokenize='porter ascii'` (porter stemming suits natural-language queries better than the trigram used for title search)
- `chunks_vec` — sqlite-vec `vec0` virtual table storing 768-dim float32 embeddings

**Retrieval** (`rag/retriever.py`):
- Dense: sqlite-vec ANN search (`SELECT chunk_id, distance FROM chunks_vec WHERE embedding MATCH ? AND k = ?`)
- Sparse: FTS5 BM25 with per-word quoting (not phrase quoting — avoids verbatim-match failures on questions)
- Merge: Reciprocal Rank Fusion (`score = Σ 1/(k + rank)` across both lists, k=60)
- Falls back to sparse-only if Ollama is unreachable

**Generation** (`rag/generator.py`):
- `build_prompt()` formats top-K chunks as numbered context blocks (capped at ~6,000 chars)
- `stream_response()` is an async generator that streams tokens from Ollama `/api/chat`

**Embedding CLI** (`rag/embed.py`):
- Incremental by default: loads `(page_id, revision_id)` from `articles_meta`, skips unchanged articles, deletes + re-embeds if `revision_id` changed
- Commits every `--batch` articles (default 100); FTS5 bulk-rebuild runs at the end
- `--reset` flag wipes all existing data before starting

**Key non-obvious decisions**:
- `rag_db_path_for(wiki)` in `paths.py` returns `dumps/{wiki}_rag.db`; wiki DBs are atomically replaced on refresh (`os.replace`), so RAG data must be separate
- `connect_rag(path)` loads the sqlite-vec extension on every connection — never use plain `sqlite3.connect()` for the RAG DB
- FTS5 uses `tokenize='porter ascii'` (not trigram): trigram is ideal for partial-title substring search, porter stemming improves recall on free-text questions ("running" matches "run")
- Sparse search quotes each word individually (`"word1" "word2"`) so FTS5 applies AND-of-terms, not phrase-match — phrase quoting would require verbatim adjacent sequences

### Test organisation

| File | Scope |
|---|---|
| `tests/test_download.py` | `respx`-mocked HTTP; filesystem isolated to `tmp_path` |
| `tests/test_pipeline.py` | XML dump generation helpers; namespace filtering, atomic writes |
| `tests/test_render.py` | All wikitext → HTML converter stages |
| `tests/test_app.py` | `TestClient` against a hermetic fixture DB |
| `tests/test_rag_schema.py` | RAG schema creation and idempotence |
| `tests/test_rag_chunker.py` | Chunker unit tests — no external deps required |
| `tests/test_rag_retriever.py` | Retrieval tests against fixture RAG DB; Ollama not required |
