# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Wikipedia dump downloader, parser, browser-based reader, and RAG pipeline that:
1. Downloads and SHA-1-verifies multistream dump files from Wikimedia
2. Parses compressed XML dumps and extracts articles into SQLite
3. Serves a FastAPI + HTMX web UI for searching and reading articles
4. Chunks and embeds articles for semantic search via a local Ollama model

The web app (`app/`) reads from the SQLite database and renders articles via the **wikitext → HTML** pipeline in `render/`. The `rag/` package handles offline embedding and retrieval; web UI integration for AI queries is not yet built.

## Layout

```
local_wikipedia/
  app/              FastAPI web app (package)
    __init__.py     factory: creates FastAPI instance, mounts /static, registers routers
    config.py       module-level constants (WIKI_LABELS, SEARCH_LIMIT, …) + Jinja2 templates
    deps.py         per-request helpers: active_wiki, db_path, connect, rag_connect
    helpers.py      pure helpers: format_*, escape_fts5, search_titles, fetch_article, spawn_worker, htmx_redirect, wiki_label
    lifespan.py     recover_from_crash() + FastAPI lifespan hook
    panels.py       template-data builders for refresh/active-embedding panels
    routes/         route modules grouped by feature
      __init__.py
      home.py            /, /search, /switch-wiki
      article.py         /article, /wikitext, /chunks
      refresh.py         POST /refresh, GET /refresh/status
      embeddings.py      /embed-manager, /embed-status, /embed-article, /embed-links, /embed/{wiki}/{title}, /embed-all, /embed/reembed
      active_embedding.py /active-embedding (+ /jobs, /panel, /cancel)
  paths.py          BASE_DIR, DUMPS_DIR, DEFAULT_WIKI, JOBS_DB, KNOWN_WIKIS, rag_db_path_for
  db.py             connect(), redirect_target(), resolve_redirect() — shared by app + embed pipeline
  jobs/             job-queue CRUD package
    __init__.py
    refresh.py      CRUD helpers for refresh_jobs table in dumps/jobs.db
    embed.py        CRUD helpers for embed_jobs / embed_job_items tables in dumps/jobs.db
  workers/          background-subprocess package
    __init__.py
    runner.py       shared harness (log redirect, exception capture, mark-failed)
    refresh.py      download → refresh → FTS rebuild
    embed.py        drains embed_job_items queue via rag.embed.embed_one
  start.sh          tmux helper — start/stop/attach the uvicorn server in a persistent session
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
    embedder.py     embed_text() sync/async + embed_texts_batch() via Ollama; pack/unpack_embedding()
    embed.py        CLI entry point + embed_one() used by embed_worker
    links.py        extract_article_links() — parse wikilinks from raw wikitext
    retriever.py    retrieve() — dense (sqlite-vec) + sparse (FTS5) + RRF merge
  download/
    download.py         dump downloader + SHA-1 verifier
    download_katex.py   one-time script to fetch vendored KaTeX for offline math rendering
  parse/
    schema.py       articles + articles_archive + parse_metadata + db_metadata DDL, PRAGMAs
    xml_reader.py   parse_page_element, MediaWiki namespace constants
    pipeline.py     parse_dump, _batch_insert_articles, _record_metadata
    refresh.py      refresh_dump — incremental update of an existing database
    verify.py       verify_database
    cli.py          argparse main(), _find_latest_dump
  scripts/
    calibrate_chunks.py   sample articles and measure real nomic-embed-text token counts; suggests MAX_CHUNK_CHARS
  tests/            pytest suite (mirrors source layout)
  templates/        Jinja2 templates (includes active_embedding.html + active_embedding_panel.html)
  static/
  dumps/            (gitignored) downloaded .xml.bz2 + parsed .db + jobs.db
                    also stores {wiki}_rag.db (RAG chunks + vectors)
                    also stores {wiki}_embed.log (embed worker output)
```

## Setup and Dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python download/download_katex.py   # one-time: vendors KaTeX for offline math rendering
```

Dependencies (pinned in `requirements.txt` and mirrored in `pyproject.toml`): `httpx`, `tqdm`, `pytest`, `respx`, `mwparserfromhell`, `fastapi`, `uvicorn[standard]`, `jinja2`, `sqlite-vec`.

Dev tooling: install `ruff` (`pip install ruff`) to lint and format. Config lives in `pyproject.toml` (`[tool.ruff]`). Run `ruff check .` and `ruff format .` from the project root.

For the RAG pipeline, [Ollama](https://ollama.com) must be running locally with the embedding model pulled (`ollama pull nomic-embed-text`) and optionally a chat model (`ollama pull llama3`).

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

# Run persistently on a remote server (survives SSH disconnect)
./start.sh             # start in a tmux session named "wikipedia"
./start.sh attach      # re-attach to see logs
./start.sh stop        # kill the session

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
                                  → app/ → browser
                                  → rag/embed.py → dumps/*_rag.db (chunks + vectors)
```

The web app is a single-page UI: `GET /` serves the shell, HTMX drives `GET /search?q=` and `GET /article/{title:path}` as fragment swaps into `#results` and `#article` — no JS build step. Secondary pages (`/embed-manager`, `/active-embedding`, `/chunks/{title}`) are full page-loads that extend `base.html`.

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

### Web app (`app/` package)

- **Factory** (`app/__init__.py`): creates the FastAPI instance, mounts `/static`, then imports each `app.routes.*` submodule and calls `app.include_router(module.router)`. Route handlers live in their per-feature submodules, not the package root.
- **Startup crash recovery** (`app/lifespan.py:recover_from_crash` via FastAPI `lifespan` hook): on every startup, marks orphaned refresh and embed jobs as `failed` so the UI doesn't show perpetual progress; also rebuilds FTS indexes for any wiki whose `fts_dirty` flag was set by a crashed refresh worker (synchronously, before serving requests).
- **Patchable constants** (`paths.JOBS_DB`, `paths.BASE_DIR`, `paths.db_path_for`, `paths.rag_db_path_for`): all app modules reference these via the `paths` module (e.g. `paths.JOBS_DB`) rather than `from paths import JOBS_DB`, so tests can monkeypatch in one place (`monkeypatch.setattr(paths, "JOBS_DB", …)`) and every consumer sees the patched value.
- Per-request SQLite connections (avoids cross-thread issues with FastAPI's threadpool)
- `WIKI_DB` env var overrides the DB path — tests use `monkeypatch.setenv` to point at a fixture DB without restarting the app
- `db_metadata` table (key/value) in each wiki DB caches `article_count` so the home page avoids a full `COUNT(*)` on every request; populated lazily on first home-page load
- `{title:path}` route converter so titles with slashes round-trip cleanly
- `|safe` in `article.html` is intentional: the HTML comes from our own converter, not user input
- `app.helpers.search_titles` uses FTS5 MATCH via `escape_fts5` (wraps input in phrase quotes); short queries (<3 chars) fall back to prefix LIKE
- `app.helpers.fetch_article()` follows `#REDIRECT [[Target]]` chains up to `REDIRECT_MAX_HOPS` (5) and returns the original title as `redirected_from` so the template can show a "Redirected from X" note. A cycle guard surfaces a 404 rather than hanging.
- `GET /wikitext/{title}` returns raw wikitext in a `<pre>` block — toggled from the article view
- `GET /switch-wiki?to=` sets a `wiki_pref` cookie (1-year max-age) and redirects: `article` param → `/?wiki=&article=` (pre-loads article in new wiki), `return_to` param → that URL (used by embed manager / active-embedding badges), otherwise → `/?wiki=`; `app.deps.active_wiki()` reads the cookie on every request
- Wiki-switching badges appear in all page headers: home page has them in `<div class="wiki-badges">` below the `<h1>`; embed manager and active-embedding have them inline inside the `<h1>`; article and wikitext views have them inline in the article `<h2>`. The active wiki renders as a `<span>` (disabled); the other renders as `<a>` with `wiki-badge--switch` (only if that wiki's DB exists).
- All full-page templates include `<div class="nav-btn-group">` with Home / Embeddings / Processes links, positioned below the page header. Routes pass `current_page` (`"home"` / `"embeddings"` / `"processes"` / `""`) to control which button renders as active (`nav-btn--active` span) vs a plain link. Chunks passes `""` — no item is highlighted.
- `index()` gates `other_wiki` on DB existence before passing to template (consistent with `embed_manager` and `render_active_embedding_panel`)
- Common patterns are factored into `app.helpers`: `spawn_worker(module, wiki, job_id, log_suffix)` dedupes the subprocess.Popen boilerplate used by `/refresh`, `/embed-links`, `/embed/reembed`; `htmx_redirect(url, request)` dedupes the "204+HX-Redirect for HTMX, 303 RedirectResponse otherwise" pattern.
- `POST /refresh/{wiki}` creates a job row (inside `BEGIN IMMEDIATE` to avoid duplicate active jobs), then spawns `python -m workers.refresh` as a detached subprocess (`start_new_session=True`) so it outlives the HTTP connection
- `GET /refresh/status/{wiki}` returns the latest job row as the `refresh_panel.html` partial — polled by HTMX while a job is active
- `POST /embed-links/{title}` extracts wikilinks from the article, resolves redirects, and enqueues source + linked articles for batch embedding; spawns `python -m workers.embed` if no job is active, otherwise appends to the running job
- `GET /active-embedding` and `GET /active-embedding/panel` render the batch embedding status page; the panel is polled by HTMX every 3s while a job is running
- `POST /active-embedding/cancel/{job_id}` sets `cancel_requested` on the job; the worker checks this flag between items
- `GET /article/{title}` and `GET /wikitext/{title}` return embed-status context including `links_embedded` (bool) — the article header shows a "links embedded" badge when the article has been processed through the embed-links pipeline
- After every HTMX article swap, `base.html`'s inline JS scrolls to the top of the page (or to the anchor section if the link included a `#fragment`)

### Refresh job system (`jobs/refresh.py`, `workers/refresh.py`, `parse/refresh.py`)

- `jobs.db` in `dumps/` stores `refresh_jobs` rows; separate from wiki databases so it's never overwritten by a re-parse
- Job lifecycle statuses: `pending` → `downloading` → `parsing` → `rebuilding` → `complete` / `failed`
- `workers/refresh.py` runs as a module entry point (`python -m workers.refresh --wiki X --job-id N`); uses `workers/runner.py`'s `run_worker` harness for log redirection and exception capture
- `refresh_dump` (in `parse/refresh.py`) operates in-place on the existing database — it does **not** atomically replace it. For each article in the dump it skips (same `revision_id`), archives-then-updates (changed revision), or inserts (new `page_id`). The archive step writes the old row to `articles_archive` before overwriting so a crash leaves old data intact.
- Batch size matches `parse/pipeline.py`'s `BATCH_SIZE` (1000). After each batch, `jobs.update_job` writes running totals to `jobs.db` so the status panel can show live progress.
- FTS rebuild happens in `workers/refresh.py` after `refresh_dump` returns — same `INSERT INTO articles_fts(articles_fts) VALUES('rebuild')` used by the initial parse.
- `jobs/refresh.py` tracks an `fts_dirty` flag per wiki in the `wiki_state` table. The flag is set before the FTS rebuild begins and cleared after it completes; if the worker crashes mid-rebuild, `_recover_from_crash` picks it up on next app startup. `clear_orphaned_jobs` resets any jobs still in non-terminal status at startup.

### Embed-links pipeline (`jobs/embed.py`, `workers/embed.py`, `rag/links.py`)

UI-triggered batch embedding: clicking "Embed + links" on an article enqueues the article and all its wikilink targets for embedding via `POST /embed-links/{title}`.

- **Link extraction** (`rag/links.py`): `extract_article_links(wikitext, source_title)` parses raw wikitext with a regex (not mwparserfromhell) to extract `[[Target]]` wikilinks, filters out non-article namespaces (File:, Category:, etc.), capitalises first letters, and deduplicates. Source article always leads the queue.
- **Redirect resolution**: each link target is resolved through the wiki DB's `#REDIRECT` chains (via `db.resolve_redirect`) before enqueueing so the queue stores canonical titles. Unresolvable targets are kept and surface as `not_found` items.
- **Job model**: `embed_jobs` table holds one job per wiki; items in `embed_job_items` are deduped by `(job_id, title)` so multiple "Embed + links" clicks on different articles append to the same active job without redundant rows. Job status: `running` → `complete` / `cancelled` / `failed`.
- **Worker** (`workers/embed.py`): drains the queue serially via `rag.embed.embed_one`; checks `cancel_requested` between items for clean cancellation; writes output to `dumps/{wiki}_embed.log`.
- **Shared DB**: embed jobs share `dumps/jobs.db` with refresh jobs but use separate tables (`embed_jobs`, `embed_job_items`) — both are created idempotently in `embed_jobs.ensure_embed_schema`.
- `db.redirect_target()` and `db.resolve_redirect()` live in `db.py` (not `app/`) so the embed pipeline can use them without importing the FastAPI app.

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
| `tests/test_app.py` | `TestClient` against a hermetic fixture DB; includes `TestEmbedLinks` and `TestActiveEmbedding` |
| `tests/test_embed_jobs.py` | CRUD helpers for embed_jobs / embed_job_items tables |
| `tests/test_links.py` | `extract_article_links` — namespace filtering, dedup, redirect stubs |
| `tests/test_rag_schema.py` | RAG schema creation and idempotence |
| `tests/test_rag_chunker.py` | Chunker unit tests — no external deps required |
| `tests/test_rag_embedder.py` | `embed_text`, `embed_texts_batch`, `pack/unpack_embedding` — Ollama calls mocked with respx |
| `tests/test_rag_retriever.py` | Retrieval tests against fixture RAG DB; Ollama not required |
