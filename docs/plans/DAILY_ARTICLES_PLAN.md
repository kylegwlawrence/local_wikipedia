# Daily Random Articles on Home Page

## Context

The home page (`templates/index.html`) currently shows the title, wiki chip, search bar, and an empty results dropdown. There is no discovery surface — a visitor who doesn't know what to search for has nothing to click.

This change adds two "daily random article" cards below the search bar, each showing the article title and a one-sentence intro. Picks are stable for the calendar day and rotate at the next day's first home-page load. Cards are plain `<a href="/article/{title}">` links matching the existing search-results pattern (full-page nav, not HTMX swap).

Layout: two side-by-side cards (CSS grid, 580px max-width matching the search bar), collapsing to stacked under ~600px. No section label.

## Approach

### Daily cache in `db_metadata`

The wiki DB already has a `db_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)` table used to cache `article_count` (see `app/routes/home.py:33-43`). Reuse it:

- `daily_articles_date` → `YYYY-MM-DD` (server-local date)
- `daily_articles_payload` → JSON list `[{"title": "...", "snippet": "..."}, ...]`

On each home request, read both keys. If `daily_articles_date` matches today, render the cached payload. Otherwise pick fresh articles, overwrite both keys in a single transaction, then render.

### Random-article picking

`ORDER BY RANDOM()` scans all 19M rows on the full enwiki DB. Use a `page_id`-offset approach instead — `page_id` is the primary key (B-tree), so it's O(log n):

1. `SELECT MAX(page_id) FROM articles WHERE namespace = 0` (cache this in memory per request — single call).
2. Pick `random.randint(1, max_id)`, fetch `WHERE namespace = 0 AND page_id >= ? ORDER BY page_id LIMIT 1`.
3. Reject if `db.redirect_target(text_content)` is not None.
4. Reject if the extracted snippet is empty.
5. Retry up to ~20 times until 2 valid picks (different `page_id`s) are accumulated.

`namespace = 0` filter (main articles only) matches existing patterns in `app/helpers.py:search_titles`.

### First-sentence extraction

Reuse `rag.chunker._strip_wikitext` to convert wikitext → plain text via `mwparserfromhell.strip_code()`. Steps:

1. Slice the lead — everything before the first `==` heading (split on `_SECTION_RE` from `rag/chunker.py:15`, or equivalent inline regex).
2. Run `_strip_wikitext(lead)` to get clean plain text.
3. Smart sentence split: find the first `.` that is followed by whitespace + an uppercase letter (handles `Mr.`, `U.S.`, `Inc.` reasonably). Regex: `r"\.(?=\s+[A-Z])"`.
4. If no such break exists, fall back to truncating at the nearest word boundary near 240 chars with `…`.
5. If the resulting snippet is < ~10 chars, treat as empty so the picker retries.

## Files to modify

### `app/helpers.py` (extend)

Add `daily_random_articles(request) -> list[dict]` that returns `[{"title": str, "snippet": str}, ...]` (length 0–2). Internals:

- `_today_iso()` — `date.today().isoformat()`, factored so tests can monkeypatch.
- `_extract_first_sentence(wikitext: str) -> str` — lead-slice + strip + smart-split as above.
- `_pick_random_articles(conn, n=2, max_attempts=20) -> list[dict]` — offset-based picker.
- `daily_random_articles(request)` — reads `db_metadata`, returns cached payload if `daily_articles_date == today`, else picks fresh + writes both keys in one `BEGIN IMMEDIATE` transaction and returns.

Import `from rag.chunker import _strip_wikitext` (already-tested helper; underscore is a Python convention, not enforced). Import `db.redirect_target` (already used elsewhere in the app layer).

### `app/routes/home.py` (extend `index()`)

After the existing `article_count` block, call `daily_random_articles(request)` and pass the result under context key `daily_articles`. The helper opens its own connection — do not pass `conn` in. Keep the existing connection block as-is.

### `templates/index.html` (extend)

Insert a new section **after** the existing `<section id="results">` so search results still appear directly under the search bar when the user is typing:

```html
{% if daily_articles %}
<section class="daily-articles" aria-label="Today's featured articles">
  {% for item in daily_articles %}
    <a class="daily-card" href="/article/{{ item.title|urlencode }}">
      <h2 class="daily-card__title">{{ item.title }}</h2>
      <p class="daily-card__snippet">{{ item.snippet }}</p>
    </a>
  {% endfor %}
</section>
{% endif %}
```

### `static/style.css` (extend)

Reuse the existing design tokens — no new variables. Add at the bottom of the file:

```css
.daily-articles {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
  width: 100%;
  max-width: 580px;
  margin: var(--space-6) auto 0;
}

.daily-card {
  display: block;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: var(--space-4) var(--space-5);
  text-decoration: none;
  color: inherit;
  box-shadow: var(--elev-1);
  transition: box-shadow var(--motion-fast), border-color var(--motion-fast);
}

.daily-card:hover {
  border-color: var(--color-border-strong);
  box-shadow: var(--elev-2);
}

.daily-card__title {
  margin: 0 0 var(--space-2) 0;
  font-size: var(--text-h3);
  font-weight: 500;
  color: var(--color-primary);
}

.daily-card__snippet {
  margin: 0;
  font-size: var(--text-small);
  line-height: var(--line-height-normal);
  color: var(--color-text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

@media (max-width: 600px) {
  .daily-articles {
    grid-template-columns: 1fr;
  }
}
```

### `tests/test_app.py` (extend)

Add a `TestDailyArticles` class:

- **renders two cards on home** — `client.get("/")`, assert `.daily-card` appears twice in HTML, each containing a title and a non-empty snippet.
- **cards persist within the day** — call `/` twice, assert the rendered titles are identical (cache hit).
- **cards rotate on a new day** — monkeypatch `app.helpers._today_iso` to return tomorrow's date, hit `/`, assert payload was rewritten (date key changed; titles may or may not differ since it's random, so check the `daily_articles_date` row instead).
- **redirects are excluded** — extend `build_fixture_db` to include a redirect row; assert no card title equals the redirect title across many forced picks.
- **snippet extraction unit test** — `_extract_first_sentence("'''Foo''' is a bar. Other sentence.")` returns `"Foo is a bar."`.

The `build_fixture_db` fixture in `tests/conftest.py` already creates the `articles` + FTS schema; it'll need a small extension to also create `db_metadata` (matching `parse/schema.py`). Seed enough article rows that the picker can find 2 non-redirect leads.

## Critical files

- `app/routes/home.py` — wire helper into `index()` context (around line 44).
- `app/helpers.py` — new helper + extraction utilities.
- `templates/index.html` — new section after `#results`.
- `static/style.css` — new component styles.
- `tests/test_app.py` — new test class.
- `tests/conftest.py` — extend `build_fixture_db` if `db_metadata` isn't already created there.

## Reuse map

| Need | Reuse |
|---|---|
| Wikitext → plain text | `rag.chunker._strip_wikitext` |
| Redirect detection | `db.redirect_target` |
| DB connection per request | `db.connect` / `app.deps.connect` pattern |
| Metadata key/value cache | existing `db_metadata` table |
| Card-style design tokens | `--color-surface`, `--color-border`, `--elev-1/2`, `--radius-md`, `--space-*` |

## Edge cases

- **Fewer than 2 articles found** (only realistic in tiny test DBs): helper returns shorter list; template iterates whatever it has. The `{% if daily_articles %}` guard hides the section entirely if empty.
- **All retries hit redirects**: helper returns shorter list (graceful).
- **Snippet starts with the article title** (typical: `'''Title''' is ...`): leave as-is — that's the natural lead form on Wikipedia and reads well.
- **Cache row exists but JSON is malformed**: catch `json.JSONDecodeError`, treat as miss, re-pick.
- **`page_id` gaps from deleted articles**: the `page_id >= ? LIMIT 1` query naturally skips gaps — no special handling needed.
- **Wiki switch**: each wiki DB has its own `db_metadata`, so switching wikis naturally shows that wiki's own daily picks.

## Verification

1. **Unit & integration tests**: `pytest tests/test_app.py::TestDailyArticles -v` and `pytest tests/test_app.py -v` (full home/search suite must still pass).
2. **Manual smoke (simplewiki)**: `uvicorn app:app --reload`, open `http://127.0.0.1:8000/`, confirm two cards appear side-by-side below the search bar; click each card → article loads; reload home → same two articles (date hasn't changed).
3. **Manual smoke (enwiki, large DB)**: switch to enwiki via badge; confirm cards appear within a reasonable first-load time (the offset-based picker should keep this sub-100ms even on 19M rows).
4. **Daily rotation**: in a Python REPL, manually overwrite `daily_articles_date` in `dumps/simplewiki.db`'s `db_metadata` to yesterday's date, reload home, confirm new picks and a refreshed date row.
5. **Search interaction**: type in the search box, confirm search results dropdown appears between the search bar and the daily cards (because `#results` is templated above `.daily-articles`).
6. **Responsive**: narrow the browser to <600px, confirm the two cards stack vertically.
