# Embed Wikipedia tables and infoboxes into RAG chunks

## Context

The RAG chunker (`rag/chunker.py:_strip_wikitext`) calls `mwparserfromhell.strip_code()`, which silently discards:

1. `{| … |}` **wikitables** — completely lost (mwparserfromhell does not model these as structured nodes; `strip_code()` returns empty for them).
2. `{{Infobox …}}` **templates** — completely lost (`strip_code()` removes all templates).

These two structures hold the single highest-density factual content in Wikipedia: capitals, populations, dates, comparative data, taxonomic info, etc. Queries like "what is the capital of France" can fail today because the answer only lives in the infobox.

### Decisions reached during planning

- **Scope:** Both `{| … |}` wikitables AND `{{Infobox …}}` templates. List templates (`flatlist`, `plainlist`, `ubl`, `hlist`) and citations are out of scope for v1.
- **Metadata:** Add a `chunk_type TEXT NOT NULL DEFAULT 'prose'` column to `chunks`. Values: `'prose' | 'table' | 'infobox'`. Existing rows default to `'prose'`.
- **Backfill:** No new UI. The existing per-row "Re-embed" action in the embed-manager 3-dot dropdown (`templates/embed_manager.html:91-94` → `POST /embed/reembed/{wiki}/{title}` at `app/routes/embeddings.py:364`) already triggers full re-embedding, which will pick up table/infobox chunks automatically once the chunker emits them.
- **Edge cases:** Pragmatic v1. Handle simple tables (single header row, single-line cells) and flat infoboxes well. Skip or coarsely flatten: nested tables, multi-row headers, heavy colspan/rowspan, sub-templates inside infobox values beyond the most common ones.
- **Format:** Row-level serialization. Tables → "Header1: Value1 | Header2: Value2" lines per row, prefixed with a caption line. Infoboxes → "Field: Value" lines, prefixed with the article title. Small tables fit in a single chunk; large tables split at row boundaries with the header line repeated per part.

## Files to modify / create

| File | Action |
|---|---|
| `rag/schema.py` | Add `chunk_type` column to `chunks` DDL + migration |
| `rag/tables.py` | **New** — table & infobox extractors |
| `rag/chunker.py` | Integrate extractors; emit typed chunks |
| `rag/embed.py` | `_insert_chunk` writes `chunk_type` |
| `rag/retriever.py` | `Chunk` dataclass + SELECT include `chunk_type` |
| `app/routes/article.py` | `/chunks/{title}` SELECT include `chunk_type` (verify; see step 5) |
| `templates/chunks.html` | Render a small chip showing chunk_type |
| `tests/test_rag_schema.py` | Assert new column + migration idempotence |
| `tests/test_rag_chunker.py` | Add assertions that prose chunks default to `chunk_type='prose'` |
| `tests/test_rag_tables.py` | **New** — extractor unit tests + chunker integration tests for tables/infoboxes |

## Implementation steps

### 1. Schema migration (`rag/schema.py`)

In `create_rag_schema`, change the `chunks` DDL block (currently lines 61-68) to:

```sql
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id      INTEGER NOT NULL REFERENCES articles_meta(page_id),
    section      TEXT,
    chunk_index  INTEGER NOT NULL DEFAULT 0,
    text         TEXT    NOT NULL,
    text_length  INTEGER NOT NULL,
    chunk_type   TEXT    NOT NULL DEFAULT 'prose'
);
```

Then in the `_col_migrations` list (line 87), append the migration for existing databases:

```python
("chunk_type", "ALTER TABLE chunks ADD COLUMN chunk_type TEXT NOT NULL DEFAULT 'prose'"),
```

The `try / except sqlite3.OperationalError` block already handles the "column already exists" case — no other changes needed. Existing rows will read `chunk_type='prose'` thanks to the column default.

### 2. New module: `rag/tables.py`

Create a new file with two pure functions and supporting helpers.

#### Constants

```python
MAX_TABLE_CHARS = 1600   # mirror chunker.MAX_CHUNK_CHARS
INFOBOX_PREFIX = "infobox"  # matched case-insensitively against template name
```

#### `extract_infoboxes(wikitext: str, article_title: str) -> list[dict]`

Implementation:

1. `parsed = mwparserfromhell.parse(wikitext)`.
2. `for tpl in parsed.filter_templates(recursive=False)` — iterate top-level templates only. (Nested infoboxes are rare and would double-emit.)
3. For each template where `str(tpl.name).strip().lower().startswith("infobox")`:
   - Capture the template name suffix (after "Infobox ") as `infobox_kind` (e.g. "country", "person"). May be empty.
   - For each `param` in `tpl.params`:
     - `field = str(param.name).strip()`, `raw_value = str(param.value).strip()`.
     - Skip empty values.
     - Skip image-bearing fields. Reuse `render/templates.py:_is_image_field` if it can be imported without a dependency cycle; otherwise inline a short prefix check against `("image", "img", "logo", "flag", "photo", "sound", "audio", "video", "map")`. (Plan: import from `render.data` if a constant exists there; the Explore agent reported `IMAGE_FIELD_PREFIXES` lives in `render/data.py`.)
     - Render the value to plain text: `mwparserfromhell.parse(raw_value).strip_code().strip()`. This is the "pragmatic" path — it loses some nested template detail but is robust and fast. Replace internal newlines with `" / "` so each field stays on one line.
     - Skip if the rendered value is empty after stripping.
4. Build the chunk text:

   ```
   Infobox: {article_title}[ — {infobox_kind}]
   {Field}: {Value}
   {Field}: {Value}
   …
   ```
   Use the article title (not the template name) as the subject, since infobox templates are anonymous — `{{Infobox country}}` belongs to whatever article it's transcluded into.

5. If the joined text exceeds `MAX_TABLE_CHARS`, split at field boundaries with the header line repeated per part; bump `chunk_index` per part.
6. Return list of `dict(section=None, chunk_index=i, text=..., chunk_type='infobox')`. (Infoboxes always live in the lead, so `section=None` matches the chunker's convention.)

Returns `[]` if no infobox templates found.

#### `extract_tables(wikitext: str, section: str | None) -> tuple[list[dict], str]`

Returns `(chunks, wikitext_with_tables_removed)`. Two-output form is intentional so the chunker can hand the cleaned text to `_strip_wikitext` without re-finding the tables.

Implementation:

1. Scan line-by-line using the same `_TABLE_OPEN_RE = re.compile(r"^[:\s]*\{\|")`, `_TABLE_INNER_OPEN_RE`, `_TABLE_INNER_CLOSE_RE` regexes as `render/tables.py:8-10` (copy them; do not import from `render/tables.py` since this is a different concern and we want `rag/` to stay independent of `render/`).
2. Track nesting depth so nested tables stay attached to their outer block. Per the pragmatic-v1 decision: when serializing, **drop** the contents of nested inner tables (leave a `[nested table]` marker in the parent cell). The line-scan already groups inner content into the parent block; the serializer just needs to skip lines while `nested_depth > 0`.
3. For each top-level table block (a list of lines from the `{|` opener through the matching `|}`):
   - Walk the lines; classify each as caption (`|+`), row separator (`|-`), header row (`!`), data row (`|`), or continuation. This mirrors `render/tables.py:_table_to_html` (lines 131-180) but produces serialized text instead of HTML.
   - For each cell, strip attributes (the `attrs | content` form) the same way `render/tables.py:parse_cell` (lines 67-92) does: find the first `|` outside a `[[…]]`, discard the prefix. Then `mwparserfromhell.parse(cell).strip_code().strip()` to plain text. Replace newlines with spaces.
   - Collapse the first row to `headers: list[str]`. If the table has explicit `!` header rows, use the last header row encountered before the first body row (most multi-row headers benefit from this; we explicitly accept losing nested header structure as a pragmatic-v1 cost). Otherwise promote the first body row to header (matches `_table_to_html` lines 183-184).
   - For each body row, build a line: `" | ".join(f"{h}: {c}" for h, c in zip(headers, cells))`. Pad/truncate `headers` to match `len(cells)` (use `f"col{i+1}"` for missing names; drop trailing extras).
4. Build chunk text:

   ```
   Table: {caption_or_"untitled"}
   {row 1 serialized}
   {row 2 serialized}
   …
   ```
5. If serialized text exceeds `MAX_TABLE_CHARS`, split at row boundaries; repeat the header/caption line per part. Bump `chunk_index` per part.
6. Each chunk: `dict(section=section, chunk_index=i, text=..., chunk_type='table')`.
7. Build `wikitext_with_tables_removed` by replacing the consumed line ranges with empty lines (preserves overall line indexing).

Return `(chunks, cleaned_wikitext)`. If no tables, return `([], wikitext)`.

### 3. Chunker integration (`rag/chunker.py`)

In `chunk_article` (line 107), change the flow as follows. **Read the existing function carefully** — only the two integration points marked below are new; everything else (section splitting, `_split_long_text`, redirect skip, file-image stripping) stays as-is.

```python
def chunk_article(title, wikitext, max_chars=MAX_CHUNK_CHARS):
    if is_redirect(wikitext):
        return []

    # NEW: pull infoboxes from the whole article first (they live pre-section).
    infobox_chunks = extract_infoboxes(wikitext, title)

    # …existing section splitting (lines 130-143)…

    chunks: list[dict] = []
    chunks.extend(infobox_chunks)  # NEW: infoboxes first

    for section, fragment in segments:
        # NEW: pull tables out of this section before stripping.
        table_chunks, fragment = extract_tables(fragment, section)
        chunks.extend(table_chunks)

        plain = _strip_wikitext(fragment)
        if not plain:
            continue
        parts = _split_long_text(plain, max_chars)
        for idx, part in enumerate(parts):
            if part:
                chunks.append({
                    "section": section,
                    "chunk_index": idx,
                    "text": part,
                    "chunk_type": "prose",   # NEW
                })
    return chunks
```

Add the import at the top: `from rag.tables import extract_tables, extract_infoboxes`.

**Edge: when `_strip_wikitext` sees the cleaned fragment with table lines removed, paragraph spacing may collapse.** Test that `\n\n`-separated paragraphs around tables still produce clean prose chunks. If empty lines accumulate, add a `re.sub(r"\n{3,}", "\n\n", plain)` after stripping (cheap insurance).

### 4. Insert path (`rag/embed.py`)

Update `_insert_chunk` (line 84). Change the SQL and tuple:

```python
cur = rag_conn.execute(
    "INSERT INTO chunks (page_id, section, chunk_index, text, text_length, chunk_type) "
    "VALUES (?, ?, ?, ?, ?, ?)",
    (
        page_id,
        chunk["section"],
        chunk["chunk_index"],
        chunk["text"],
        len(chunk["text"]),
        chunk.get("chunk_type", "prose"),  # tolerate older test fixtures
    ),
)
```

No other change needed in `embed.py`. `embedder.format_document(title, section, text)` (called at line 167 and 235) already wraps each chunk's text — the table/infobox serialization itself already carries a "Table: …" / "Infobox: …" line that gives the embedder context, so we deliberately do **not** branch behavior in `format_document`.

### 5. Retrieval (`rag/retriever.py` + `app/routes/article.py`)

`rag/retriever.py`:
- Add `chunk_type: str` field to the `Chunk` dataclass (line 17-39).
- Update `_fetch_chunks` SELECT (line 210) to include `c.chunk_type` and pass it into the `Chunk(...)` constructor (line 216-225).

`app/routes/article.py` (read separately; not shown above): the `/chunks/{title}` route does its own SELECT from `chunks` to build the `chunks.html` page. Grep for `FROM chunks` in `app/routes/`; add `chunk_type` to that SELECT and pass it through to the template context. (If the route currently does `SELECT *`, the column will surface automatically thanks to `Row` factory.)

No `retrieve()` API change in v1: we do not filter by `chunk_type`. All types compete in the same RRF pool. (Future work can add a filter parameter if retrieval analytics show tables crowding out prose or vice versa.)

### 6. UI surface (`templates/chunks.html`)

Add a small chip beside the section label so users browsing chunks can see the type at a glance:

```jinja
<span class="meta-text">&middot; {{ chunk.section if chunk.section else "Introduction" }}{% if chunk.chunk_index > 0 %} (part {{ chunk.chunk_index + 1 }}){% endif %}</span>
{% if chunk.chunk_type and chunk.chunk_type != 'prose' %}
<span class="chip chip--{{ chunk.chunk_type }}">{{ chunk.chunk_type }}</span>
{% endif %}
```

No CSS additions are required for v1; the existing `.chip` class will render an unstyled chip. If desired later, add `.chip--table` / `.chip--infobox` variants to `static/styles.css`.

No changes to `templates/embed_manager.html` per user decision.

### 7. Tests

#### `tests/test_rag_schema.py` (extend)

Add a test that, given a connection on a fresh DB, the `chunks` table has a `chunk_type` column. Also add a migration test: create a DB with the **old** `chunks` schema (no `chunk_type`), call `create_rag_schema` on it, and assert the column is now present with the right default.

#### `tests/test_rag_chunker.py` (extend)

For every existing test, the produced chunks should now also include `chunk_type='prose'`. Add one assertion to `test_returns_dicts_with_required_keys` (line 105) checking `"chunk_type" in chunk` and `chunk["chunk_type"] == "prose"` for plain-text input.

#### `tests/test_rag_tables.py` (new)

Test cases (use plain `assert` style matching existing tests):

**Tables:**
1. Simple table: `{| class="wikitable" |- ! Country !! Capital |- | France || Paris |- | Germany || Berlin |}` → one chunk, chunk_type='table', section=None (or given), contains `Country: France | Capital: Paris`.
2. Caption preserved: `|+ My caption` → first line of chunk text is `Table: My caption`.
3. Table inside `== Section ==` → chunk's `section` field is `"Section"`.
4. Header inferred when no `!` row: first `|` row promoted to headers.
5. Long table splits at row boundaries with caption repeated per part. Pass a small `MAX_TABLE_CHARS` to force the split.
6. Cell attribute stripping: `| colspan="2" | Wide cell` → `Wide cell` in output, no `colspan` artifact.
7. Cell with wikilink: `| [[Paris]]` → serialized as `Paris`.
8. Nested table is collapsed/skipped (assert outer table still renders; inner content does not appear).
9. Unclosed table (no `|}`): assert no chunk emitted, no exception raised, source wikitext returned unchanged in the `cleaned_wikitext` second return value.

**Infoboxes:**
10. `{{Infobox country | name = France | capital = Paris | population = 68M}}` → one chunk, chunk_type='infobox', text contains `Capital: Paris` and `Population: 68M`.
11. Image fields skipped: `{{Infobox country | flag_image = Flag.svg | capital = Paris}}` → `Flag.svg` absent from chunk text; `Paris` present.
12. Empty values skipped: `{{Infobox … | capital = | population = 68M}}` → no blank `Capital:` line.
13. Nested template in value: `{{Infobox … | birthdate = {{birth date|1980|1|1}}}}` → value is non-empty and contains digit text (the date), even if not perfectly formatted (pragmatic v1).
14. Article title appears in chunk header: chunk text starts with `Infobox: {title}`.
15. No infobox → returns `[]`.

**Integration via `chunk_article`:**
16. Article with prose + table + infobox produces chunks of all three types, in the order `infobox` first, then per-section `table` before `prose`. Filter by `chunk_type` to assert each set is non-empty.
17. Article with only an infobox in the lead and no body text: only an infobox chunk is emitted (no empty prose chunk).

## Verification

1. **Unit tests:** `pytest tests/test_rag_tables.py tests/test_rag_chunker.py tests/test_rag_schema.py -v` — all pass.
2. **Full suite:** `pytest -q` — no regressions.
3. **Lint:** `ruff check rag/ tests/` and `ruff format --check rag/ tests/`.
4. **Schema migration on an existing DB:**
   ```bash
   sqlite3 dumps/simplewiki_rag.db "PRAGMA table_info(chunks)"
   # Start the app once to trigger create_rag_schema:
   uvicorn app:app --reload   # Ctrl-C after startup banner
   sqlite3 dumps/simplewiki_rag.db "PRAGMA table_info(chunks)"
   # Expect: chunk_type column present, default 'prose'.
   sqlite3 dumps/simplewiki_rag.db "SELECT DISTINCT chunk_type FROM chunks"
   # Expect: only 'prose' (existing data unchanged).
   ```
5. **End-to-end on a single article with both structures:** Pick simplewiki "France" (or any article you know contains an infobox and a table).
   - In the running app, open `/embed-manager`, open the row's 3-dot menu, click **Re-embed**.
   - Wait for the active-embedding job to finish.
   - Navigate to `/chunks/France` and confirm at least one chunk shows the "infobox" chip and at least one shows "table". Click into the chunk text and verify it reads as serialized key-value / row lines.
6. **Retrieval check:**
   ```python
   from rag.schema import connect_rag
   from rag.retriever import retrieve
   from paths import rag_db_path_for
   conn = connect_rag(rag_db_path_for("simplewiki"))
   res = retrieve("what is the capital of France", conn)
   for h in res.hits:
       print(h.chunk_type, h.title, h.section, h.text[:120])
   ```
   Expect at least one `infobox` hit for the France article in the top results — that was impossible before this change.

## Out of scope (explicitly deferred)

- List templates (`{{flatlist}}`, `{{plainlist}}`, `{{ubl}}`, `{{hlist}}`).
- Citation templates.
- `chunk_type` filter parameter on `retrieve()`.
- Per-row "Re-embed" promotion out of the dropdown.
- Toolbar-level "Re-embed all" bulk action.
- CSS styling for the new chunk-type chip.
- Recalibrating `MAX_CHUNK_CHARS` for table-density text (current 1600 is conservative; if retrieval suffers, run `scripts/calibrate_chunks.py` on table-only chunks later).
- Multi-row header join, colspan/rowspan replication, deeply nested templates inside infobox values.
