# Frontend Redesign — Implementation Plan

This plan rebuilds the Local Wikipedia frontend in a **Google Cloud Console** visual language: persistent left sidebar, slim top bar with breadcrumb, restyled inline wiki chips, soft cards instead of flat tables, and a proper light/dark theme via design tokens. Articles get their own full-page route. A new top-level "Refresh" page absorbs the data-management UI that currently clutters the homepage.

**Audience:** Sonnet 4.6 (high effort), executing without conversation context. Follow the phases in order — each is commit-sized and independently verifiable. Do not skip the verification step at the end of each phase.

---

## 1. Locked decisions

| Area | Choice |
|---|---|
| Visual style | GCP / Google Cloud Console |
| Top-level nav | Persistent left sidebar, **always expanded** (~220px) |
| Wiki switcher | Inline chips per page header (restyled, consistent placement) |
| Refresh-controls location | New dedicated page, sidebar label **"Refresh"** |
| Color mode | Light **and** dark, follow `prefers-color-scheme` + manual toggle |
| Article URLs | Real `/article/{title}` route, **full page navigation** (no HTMX swap from homepage) |
| Icons | Inline SVG (Lucide), via Jinja macros |
| Homepage content | Search-only, minimal (hero search + brand) |
| Mobile | Out of scope. Desktop-first. Sidebar stays at 220px at all viewports. |

---

## 2. What stays untouched

- The wikitext → HTML render pipeline (`render/`)
- The database schemas (`parse/schema.py`, `rag/schema.py`)
- The job system (`jobs/`, `workers/`)
- All backend route URLs except where this plan explicitly changes them
- HTMX itself — we keep HTMX for: the live-typing search dropdown on the homepage, the embed-status fragment on the article page, the active-embedding panel polling, the refresh status panel polling
- The KaTeX vendoring + math rendering JS in `base.html`

---

## 3. Design tokens (the foundation of every later phase)

Drop these as `:root` and `[data-theme="dark"]` blocks at the top of the rewritten `static/style.css`. Light is the default; dark activates via `prefers-color-scheme: dark` **or** an explicit `data-theme="dark"` on `<html>`.

### 3.1 Color tokens

**Light theme** (`:root` and `[data-theme="light"]`):

```css
--color-bg:                #ffffff;
--color-surface:           #ffffff;
--color-surface-variant:   #f8f9fa;   /* sidebar bg, page subtle areas */
--color-surface-sunken:    #f1f3f4;   /* code blocks, raw wikitext */
--color-border:            #dadce0;
--color-border-strong:     #bdc1c6;
--color-divider:           #e8eaed;

--color-text:              #202124;
--color-text-secondary:    #5f6368;
--color-text-tertiary:     #80868b;
--color-text-on-primary:   #ffffff;

--color-primary:           #1a73e8;   /* Google Blue */
--color-primary-hover:     #1765cc;
--color-primary-container: #e8f0fe;   /* light blue chip bg */
--color-primary-on-container: #174ea6;

--color-success:           #137333;
--color-success-container: #e6f4ea;
--color-success-on-container: #0d652d;
--color-warning:           #b06000;
--color-warning-container: #fef7e0;
--color-warning-on-container: #663c00;
--color-error:             #c5221f;
--color-error-container:   #fce8e6;
--color-error-on-container: #a50e0e;
--color-info:              #1967d2;
--color-info-container:    #e8f0fe;
--color-info-on-container: #174ea6;

/* Wiki-specific tinted chips */
--color-wiki-enwiki-container:       #fef0e0;
--color-wiki-enwiki-on-container:    #7a4200;
--color-wiki-enwiki-border:          #f5a623;
--color-wiki-simplewiki-container:   #e1eefc;
--color-wiki-simplewiki-on-container:#0d4d8a;
--color-wiki-simplewiki-border:      #5aabf0;
```

**Dark theme** (`[data-theme="dark"]` and `@media (prefers-color-scheme: dark) :root:not([data-theme="light"])`):

```css
--color-bg:                #1f1f1f;
--color-surface:           #2d2e30;
--color-surface-variant:   #28292c;   /* sidebar bg */
--color-surface-sunken:    #202124;
--color-border:            #3c4043;
--color-border-strong:     #5f6368;
--color-divider:           #3c4043;

--color-text:              #e8eaed;
--color-text-secondary:    #9aa0a6;
--color-text-tertiary:     #80868b;
--color-text-on-primary:   #202124;

--color-primary:           #8ab4f8;
--color-primary-hover:     #aecbfa;
--color-primary-container: #1f3a60;
--color-primary-on-container: #d2e3fc;

--color-success:           #81c995;
--color-success-container: #0f3c1f;
--color-success-on-container: #ceead6;
--color-warning:           #fdd663;
--color-warning-container: #4f3500;
--color-warning-on-container: #fef0c3;
--color-error:             #f28b82;
--color-error-container:   #4c1414;
--color-error-on-container: #fad2cf;
--color-info:              #8ab4f8;
--color-info-container:    #1f3a60;
--color-info-on-container: #d2e3fc;

--color-wiki-enwiki-container:       #4a2c00;
--color-wiki-enwiki-on-container:    #ffd6a0;
--color-wiki-enwiki-border:          #c97f1a;
--color-wiki-simplewiki-container:   #0d2d52;
--color-wiki-simplewiki-on-container:#bcd9f9;
--color-wiki-simplewiki-border:      #4080c0;
```

### 3.2 Type, spacing, radii, elevation tokens

```css
/* Typography */
--font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
             "Helvetica Neue", Arial, sans-serif;
--font-mono: ui-monospace, "SF Mono", Menlo, Consolas, "Roboto Mono", monospace;
--font-display: var(--font-sans);

--text-display: 1.875rem;  /* 30px — homepage brand */
--text-h1: 1.5rem;         /* 24px — page titles */
--text-h2: 1.25rem;        /* 20px — section headings */
--text-h3: 1rem;           /* 16px — card titles */
--text-body: 0.9375rem;    /* 15px — body */
--text-small: 0.8125rem;   /* 13px — meta */
--text-micro: 0.6875rem;   /* 11px — uppercase labels */

--weight-regular: 400;
--weight-medium: 500;
--weight-semibold: 600;

--leading-tight: 1.3;
--leading-normal: 1.5;
--leading-relaxed: 1.65;

/* Spacing (8px / 4px grid) */
--space-1: 4px;
--space-2: 8px;
--space-3: 12px;
--space-4: 16px;
--space-5: 20px;
--space-6: 24px;
--space-8: 32px;
--space-10: 40px;
--space-12: 48px;
--space-16: 64px;

/* Radii */
--radius-sm: 4px;     /* chips, status badges */
--radius-md: 8px;     /* cards, inputs, secondary buttons */
--radius-lg: 12px;
--radius-pill: 999px; /* hero search, primary buttons */

/* Elevation (light mode) */
--elev-1: 0 1px 2px rgba(60,64,67,.08), 0 1px 3px rgba(60,64,67,.06);
--elev-2: 0 1px 3px rgba(60,64,67,.12), 0 4px 8px rgba(60,64,67,.06);
--elev-3: 0 2px 8px rgba(60,64,67,.10), 0 8px 24px rgba(60,64,67,.08);

/* Layout */
--sidebar-width: 220px;
--topbar-height: 56px;
--content-max-width: 1200px;   /* admin pages */
--reading-max-width: 760px;    /* article body */
```

In the dark block, override `--elev-*` to use `rgba(0,0,0,.4)` / `.6` for the larger shadow.

### 3.3 Motion tokens

```css
--motion-fast: 120ms;
--motion-base: 180ms;
--motion-slow: 240ms;
--ease-standard: cubic-bezier(0.4, 0.0, 0.2, 1);
--ease-emphasized: cubic-bezier(0.2, 0.0, 0, 1);
```

---

## 4. Lucide icons to vendor

All icons go into `templates/_icons.html` as Jinja macros. Each macro takes an optional `size` (default 20) and a `class_` (default empty) so it can be used like `{{ icon.home(size=18, class_='sidebar-icon') }}`.

Pull these 17 SVGs from https://lucide.dev (verbatim copy of the `<svg>` markup):

| Macro name | Lucide icon | Used in |
|---|---|---|
| `home` | `home` | sidebar nav |
| `database` | `database` | sidebar Embeddings |
| `activity` | `activity` | sidebar Processes |
| `refresh_cw` | `refresh-cw` | sidebar Refresh |
| `search` | `search` | hero search input |
| `sun` | `sun` | theme toggle |
| `moon` | `moon` | theme toggle |
| `monitor` | `monitor` | theme toggle (auto/system) |
| `arrow_right` | `arrow-right` | wiki chip switch indicator |
| `chevron_right` | `chevron-right` | breadcrumb separator |
| `check` | `check` | embedded badge |
| `circle_alert` | `circle-alert` | error notice |
| `loader` | `loader-circle` | running indicator (animate-spin) |
| `more_vertical` | `more-vertical` | embed manager actions menu |
| `trash` | `trash-2` | delete actions |
| `link2` | `link-2` | "Embed + links" buttons |
| `file_text` | `file-text` | "View wikitext" button |
| `eye` | `eye` | "View rendered" button |
| `x` | `x` | dismiss / close |
| `external_link` | `external-link` | optional, future use |

**Macro template** (apply to every icon — replace path content per icon):

```jinja
{% macro home(size=20, class_='') %}
<svg xmlns="http://www.w3.org/2000/svg" width="{{ size }}" height="{{ size }}"
     viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
     stroke-linecap="round" stroke-linejoin="round"
     class="lucide lucide-home {{ class_ }}" aria-hidden="true">
  <path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
  <polyline points="9 22 9 12 15 12 15 22"/>
</svg>
{%- endmacro %}
```

(The `currentColor` stroke is critical — it lets the icon inherit the text colour of its container so dark mode just works.)

---

## 5. Visual primitives (reference)

These are the building blocks every later phase reuses. Define them as CSS classes; templates compose them.

### 5.1 Sidebar shell

```
┌──────────────┬───────────────────────────────────────────────┐
│  Local Wiki  │  ← top bar (breadcrumb + theme toggle)        │
│              ├───────────────────────────────────────────────┤
│  🏠 Home     │                                               │
│  🧠 Embed... │  page content (max-width: 1200px, centred)    │
│  ⚡ Procs    │                                               │
│  ↻ Refresh   │                                               │
│              │                                               │
│  ────────    │                                               │
│  ☼ Theme     │                                               │
└──────────────┴───────────────────────────────────────────────┘
```

CSS grid for `<body>`:

```css
body {
  display: grid;
  grid-template-columns: var(--sidebar-width) 1fr;
  grid-template-rows: var(--topbar-height) 1fr;
  grid-template-areas:
    "sidebar topbar"
    "sidebar main";
  min-height: 100vh;
}
.app-sidebar { grid-area: sidebar; }
.app-topbar  { grid-area: topbar; }
.app-main    { grid-area: main; }
```

### 5.2 Sidebar item

```html
<a class="sidebar-item" href="/">
  {{ icon.home(size=20) }}
  <span class="sidebar-item__label">Home</span>
</a>
<a class="sidebar-item sidebar-item--active" href="/embed-manager"> ... </a>
```

Styling:
- 40px tall, full-width minus 8px side padding, 8px corner radius
- Default: transparent bg, text `--color-text-secondary`, icon inherits colour
- Hover: bg `--color-surface-variant` darkened (use `color-mix(in srgb, var(--color-text) 6%, transparent)` or a hardcoded `rgba(60,64,67,.08)`)
- Active: bg `--color-primary-container`, text `--color-primary-on-container`, font-weight `--weight-medium`. A 3px-wide rounded indicator pill on the left, full item height minus 8px, using `--color-primary`

### 5.3 Top bar

```html
<header class="app-topbar">
  <nav class="breadcrumb">
    <a href="/">Home</a>
    {{ icon.chevron_right(size=14) }}
    <span>Search</span>
  </nav>
  <div class="topbar-actions">
    <button class="theme-toggle" aria-label="Toggle theme">
      {{ icon.sun() }}
    </button>
  </div>
</header>
```

Style:
- 56px tall, sticky `top: 0`, bg `--color-surface`, border-bottom `1px solid --color-divider`
- Flex row, padding `0 var(--space-6)`, items vertically centred
- Breadcrumb: `font-size: var(--text-small)`, `color: var(--color-text-secondary)`; the last segment is a `<span>` (current page, not a link) and gets `color: var(--color-text)`

### 5.4 Page header

Used inside `<main>` at the top of each page (Home is special — see §10.1).

```html
<div class="page-header">
  <div class="page-header__title-row">
    <h1 class="page-title">Embed manager</h1>
    {% include "_wiki_chip.html" %}
  </div>
  <p class="page-header__subtitle">5,021 articles embedded</p>
</div>
```

Style:
- `padding: var(--space-8) var(--space-6) var(--space-4)`
- Title is `--text-h1`, `--weight-medium`, color `--color-text`, line-height `--leading-tight`
- Title row: flex, `gap: var(--space-3)`, `align-items: center`, `flex-wrap: wrap`
- Subtitle: `--text-small`, `--color-text-secondary`, margin `var(--space-1) 0 0`

### 5.5 Card

```html
<section class="card">
  <header class="card__header">
    <h2 class="card__title">Section</h2>
    <div class="card__actions">...</div>
  </header>
  <div class="card__body">...</div>
</section>
```

Style:
- `background: var(--color-surface);`
- `border: 1px solid var(--color-border);`
- `border-radius: var(--radius-md);`
- `box-shadow: var(--elev-1);`
- `padding: 0` (sections control their own internal padding)
- Header is flex, `padding: var(--space-4) var(--space-5)`, border-bottom `1px solid --color-divider`
- Body padding `var(--space-5)`. Tables inside have negative side margins so rows extend to card edges.

### 5.6 Button variants

Three variants. All share `border-radius: var(--radius-md)` (8px). Heights: `--btn-h-sm: 32px`, `--btn-h-md: 36px`, `--btn-h-lg: 44px`.

**Filled (primary action):**
```css
.btn-filled {
  background: var(--color-primary);
  color: var(--color-text-on-primary);
  border: 1px solid transparent;
  height: var(--btn-h-md);
  padding: 0 var(--space-4);
  font: var(--weight-medium) var(--text-small) var(--font-sans);
}
.btn-filled:hover { background: var(--color-primary-hover); }
```

**Tonal (secondary action, GCP default for most buttons):**
```css
.btn-tonal {
  background: var(--color-primary-container);
  color: var(--color-primary-on-container);
  border: 1px solid transparent;
  ...
}
.btn-tonal:hover {
  background: color-mix(in srgb, var(--color-primary-container) 80%, var(--color-primary) 20%);
}
```

**Outlined (tertiary):**
```css
.btn-outlined {
  background: transparent;
  color: var(--color-text);
  border: 1px solid var(--color-border);
  ...
}
.btn-outlined:hover {
  background: var(--color-surface-variant);
  border-color: var(--color-border-strong);
}
```

**Icon-only button** (e.g. theme toggle, kebab):
- `.btn-icon` — 36×36px, `border-radius: var(--radius-pill)` (round), transparent bg, hover bg `rgba(60,64,67,.08)`.

**Destructive variants** apply `.btn-danger` on top of `.btn-tonal` or `.btn-outlined`:
- swaps `--color-primary*` for `--color-error*`.

### 5.7 Chip (replaces the current wiki badges + status badges)

```html
<span class="chip chip--wiki chip--wiki-enwiki">enwiki</span>
<a class="chip chip--wiki chip--wiki-simplewiki chip--switchable"
   href="/switch-wiki?to=simplewiki">
  simplewiki
  {{ icon.arrow_right(size=14) }}
</a>
```

Style:
- `display: inline-flex; align-items: center; gap: var(--space-1);`
- `padding: 2px var(--space-2);`
- `border-radius: var(--radius-sm);`
- `font: var(--weight-medium) var(--text-small) var(--font-sans);`
- `height: 24px;` (use line-height to enforce, not min-height)
- Wiki variants use the `--color-wiki-{name}-{container,on-container,border}` token group
- `chip--switchable` adds `cursor: pointer`, slight opacity-on-non-hover (`.7`), `text-decoration: none`, hover restores opacity + slight bg darken

**Status chip variants** (replaces `.refresh-badge--*` and embed status badges):
- `.chip--status-running { color: var(--color-info-on-container); background: var(--color-info-container); }`
- Equivalents for `complete`, `failed`, `cancelled`, `pending`, `downloading`, `parsing`, `rebuilding`, `queued`, `in_progress`, `not_found`, `skipped_unchanged`, `skipped_redirect`. Map `downloading`/`parsing`/`rebuilding`/`running`/`in_progress` to info; `complete` to success; `failed` to error; `cancelled`/`pending`/`queued`/`skipped_*` to neutral (`color: var(--color-text-secondary); background: var(--color-surface-variant);`); `not_found` to warning.

### 5.8 Data table

```html
<div class="card">
  <table class="data-table">
    <thead><tr><th>Title</th>...</tr></thead>
    <tbody>...</tbody>
  </table>
</div>
```

Style:
- `width: 100%; border-collapse: collapse;`
- `th`: `text-align: left; padding: var(--space-3) var(--space-4); font: var(--weight-medium) var(--text-micro)/1 var(--font-sans); text-transform: uppercase; letter-spacing: .06em; color: var(--color-text-secondary); border-bottom: 1px solid var(--color-divider); background: var(--color-surface-variant);`
- `td`: `padding: var(--space-3) var(--space-4); border-bottom: 1px solid var(--color-divider); vertical-align: middle;`
- Row hover: `background: var(--color-surface-variant);`
- Last row: no bottom border
- The table sits inside `.card` (no margin). The card supplies the outer border/radius; the table provides internal rules only.

### 5.9 Form input

```html
<input type="search" class="input input--pill" placeholder="Search articles...">
```

Style:
- Base `.input`: `height: var(--btn-h-md);` `border: 1px solid var(--color-border);` `border-radius: var(--radius-md);` `padding: 0 var(--space-3);` `background: var(--color-surface);` `font: var(--text-body) var(--font-sans); color: var(--color-text);`
- Focus: `border-color: var(--color-primary); box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-primary) 24%, transparent);`
- `.input--pill`: `border-radius: var(--radius-pill); padding: 0 var(--space-5); height: 48px; font-size: 1rem;`
- `.input--with-icon` wraps with `<label class="input-with-icon">...icon...input</label>`; icon is absolutely positioned at 16px left, input gets `padding-left: 44px`

---

## 6. Phase 0 — Foundations (commit 1)

**Goal:** Drop new design tokens, the icon macro library, and the sidebar/topbar partials in place. Nothing user-visible yet.

### 6.1 Files to create

- `templates/_icons.html` — Jinja macro file with all 17 Lucide icons from §4
- `templates/_sidebar.html` — sidebar markup (see template below)
- `templates/_topbar.html` — top bar markup (see template below)
- `templates/_wiki_chip.html` — single source of truth for wiki badges (replaces the inline blocks in 5 templates)
- `templates/_page_header.html` — optional reusable page header partial (or just use `{% block page_header %}` in `base.html` — your call; the rest of this plan assumes the partial)

### 6.2 Replace `static/style.css`

This is a full rewrite. The existing file is ~1166 lines of layout + Wikipedia-styled article-body CSS. You will:

1. Keep, verbatim, the article-body styling for tables, infoboxes, hatnotes, indicators, references, gallery captions, plainlists, KaTeX, code blocks, raw wikitext — these are required by the render pipeline output and changing them will break article rendering.
2. Replace everything else (the layout, header, search input, nav buttons, refresh section, embed-manager table, action menu, chunks list, wiki badges, etc.) with the new token-driven primitives.

**Suggested file structure** (top-to-bottom):

```
/* 1. Design tokens (light + dark blocks per §3) */
/* 2. Reset + base elements (body, *, ::selection, focus-visible, etc.) */
/* 3. Layout shell (.app-sidebar, .app-topbar, .app-main, page grid) */
/* 4. Sidebar (.sidebar-*) */
/* 5. Top bar (.app-topbar, .breadcrumb, .theme-toggle) */
/* 6. Page header (.page-header, .page-title) */
/* 7. Cards (.card, .card__*) */
/* 8. Buttons (.btn-filled, .btn-tonal, .btn-outlined, .btn-icon, .btn-danger) */
/* 9. Chips (.chip, .chip--wiki-*, .chip--status-*) */
/* 10. Inputs (.input, .input--pill, .input-with-icon, .input--search-hero) */
/* 11. Data tables (.data-table) */
/* 12. Form layouts */
/* 13. Empty states (.empty-state, .empty-state__icon, .empty-state__text) */
/* 14. Spinners (.spinner, keep the existing keyframes) */
/* 15. Article reader (.article, .article-meta, .article-body — keep article-body internals) */
/* 16. Article body internals (PRESERVE FROM CURRENT FILE):
       - .article-body h2/h3/h4
       - .article-body a, code, pre
       - .article-body ul, ol
       - .article-body ol.references
       - .wikitext-raw
       - All .article-body table.* rules
       - .indicator-* (yes/no/partial/...)
       - .article-body .hatnote
       - .article-body ul.gallery-captions, ul.plainlist
       - .article-body table.infobox
       - .texhtml
       Migrate hardcoded colors to use the new tokens where possible
       (e.g. table border `#a2a9b1` stays — it's the Wikipedia table standard;
       but the table-cell `--bg-soft` should become `var(--color-surface-variant)`). */
/* 17. Chunks page (.chunk-card, .chunk-meta, etc.) — restyled but same structure */
/* 18. Search results dropdown (.result-list) — restyled */
/* 19. Embed status widget (.embed-controls, .embed-btn) — restyled */
/* 20. Action menu / kebab dropdown (.action-menu, .action-dropdown) — restyled with --elev-2 */
/* 21. Pagination (.embed-pagination, .embed-page-btn) — restyled with btn-outlined */
/* 22. Toast / banner (.not-found-banner, #article-load-spinner) — restyled */
/* 23. Utility classes (.u-sr-only, .u-tabular-nums, .u-truncate, .u-mt-*, etc.) */
```

**Hard rule:** all colors come from tokens. No hardcoded hex in component CSS except for the article-body Wikipedia-table rules.

### 6.3 Sidebar partial template (`templates/_sidebar.html`)

```jinja
{% import "_icons.html" as icon %}
<aside class="app-sidebar" aria-label="Primary navigation">
  <div class="sidebar-brand">
    <a href="/" class="sidebar-brand__link">
      <span class="sidebar-brand__logo" aria-hidden="true">📖</span>
      <span class="sidebar-brand__text">Local Wikipedia</span>
    </a>
  </div>
  <nav class="sidebar-nav">
    <a class="sidebar-item {% if current_page == 'home' %}sidebar-item--active{% endif %}" href="/">
      {{ icon.home(size=20, class_='sidebar-item__icon') }}
      <span class="sidebar-item__label">Home</span>
    </a>
    <a class="sidebar-item {% if current_page == 'embeddings' %}sidebar-item--active{% endif %}" href="/embed-manager">
      {{ icon.database(size=20, class_='sidebar-item__icon') }}
      <span class="sidebar-item__label">Embeddings</span>
    </a>
    <a class="sidebar-item {% if current_page == 'processes' %}sidebar-item--active{% endif %}" href="/active-embedding">
      {{ icon.activity(size=20, class_='sidebar-item__icon') }}
      <span class="sidebar-item__label">Processes</span>
    </a>
    <a class="sidebar-item {% if current_page == 'refresh' %}sidebar-item--active{% endif %}" href="/refresh">
      {{ icon.refresh_cw(size=20, class_='sidebar-item__icon') }}
      <span class="sidebar-item__label">Refresh</span>
    </a>
  </nav>
  <div class="sidebar-footer">
    <button type="button" class="theme-toggle btn-icon"
            data-theme-toggle aria-label="Toggle theme">
      {{ icon.monitor(size=18, class_='theme-toggle__icon theme-toggle__icon--auto') }}
      {{ icon.sun(size=18, class_='theme-toggle__icon theme-toggle__icon--light') }}
      {{ icon.moon(size=18, class_='theme-toggle__icon theme-toggle__icon--dark') }}
    </button>
  </div>
</aside>
```

The three theme icons stack; CSS uses `[data-theme-mode="auto|light|dark"]` on `<html>` to show exactly one at a time.

### 6.4 Topbar partial template (`templates/_topbar.html`)

```jinja
{% import "_icons.html" as icon %}
<header class="app-topbar">
  {% block topbar_breadcrumb %}
  <nav class="breadcrumb" aria-label="Breadcrumb">
    {% if current_page == 'home' %}
      <span class="breadcrumb__current">Home</span>
    {% else %}
      <a href="/" class="breadcrumb__link">Home</a>
      {{ icon.chevron_right(size=14, class_='breadcrumb__sep') }}
      <span class="breadcrumb__current">{{ breadcrumb_current or page_title }}</span>
    {% endif %}
  </nav>
  {% endblock %}
  <div class="topbar-actions">
    {% block topbar_actions %}{% endblock %}
  </div>
</header>
```

Routes pass `breadcrumb_current` (string) and/or `page_title` so the partial doesn't need to know per-page details.

### 6.5 Wiki chip partial (`templates/_wiki_chip.html`)

Replaces the 5 copies of the inline `{%- if wiki == "enwiki" %}...{% endif %}` blocks. Inputs:
- `wiki` (current wiki slug)
- `other_wiki` (slug to switch to, or None if its DB doesn't exist)
- `switch_url` (string, default `/switch-wiki?to={other}`) — routes pass a path including `return_to`/`article` args

```jinja
{% import "_icons.html" as icon %}
{% set switch_url = switch_url or ('/switch-wiki?to=' + (other_wiki or '')) %}
<span class="wiki-chip-group" data-current-wiki="{{ wiki }}">
  <span class="chip chip--wiki chip--wiki-{{ wiki }}">{{ wiki }}</span>
  {% if other_wiki %}
  <a class="chip chip--wiki chip--wiki-{{ other_wiki }} chip--switchable"
     href="{{ switch_url }}"
     data-target-wiki="{{ other_wiki }}"
     aria-label="Switch to {{ other_wiki }}">
    {{ icon.arrow_right(size=14) }}
    {{ other_wiki }}
  </a>
  {% endif %}
</span>
```

Note: the `data-target-wiki` attribute and `data-current-wiki` are kept so the existing `wiki-badge--switch` click handler in `index.html` continues to work; you'll port that handler in phase 4.

### 6.6 Verification

- `python -m compileall app render rag jobs workers paths.py db.py` — no Python touched yet, this just sanity-checks imports.
- Open browser, smoke test: every page should still render (the new partials/icons aren't wired in yet; CSS is rewritten so styling will be different but pages must load with no template errors).
- `ruff check . && ruff format --check .` — must pass.

**Commit message:**
`Add GCP-style design tokens, sidebar/topbar partials, Lucide icon macros`

---

## 7. Phase 1 — Base shell (commit 2)

**Goal:** Wire `base.html` into the new shell so every page that extends it inherits the sidebar + top bar. Visible change: every page now has the sidebar.

### 7.1 Rewrite `templates/base.html`

```jinja
<!doctype html>
<html lang="en" data-theme-mode="auto">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{% block title %}Local Wikipedia{% endblock %}</title>
    <link rel="stylesheet" href="{{ url_for('static', path='style.css') }}">
    <script src="https://unpkg.com/htmx.org@2.0.4"></script>
    <link rel="stylesheet" href="{{ url_for('static', path='katex/katex.min.css') }}">
    <script defer src="{{ url_for('static', path='katex/katex.min.js') }}"></script>
    <script defer src="{{ url_for('static', path='katex/contrib/mhchem.min.js') }}"></script>
    <script defer src="{{ url_for('static', path='katex/contrib/auto-render.min.js') }}"
            onload="initMath()"></script>
    <script>
      // Theme: read preference from localStorage on initial paint to avoid FOUC.
      (function () {
        var stored = localStorage.getItem('theme-mode');
        if (stored === 'light' || stored === 'dark' || stored === 'auto') {
          document.documentElement.setAttribute('data-theme-mode', stored);
        }
      })();
    </script>
    <script>
      function renderMath(el) {
        renderMathInElement(el, {
          delimiters: [
            {left: "$$", right: "$$", display: true},
            {left: "\\(", right: "\\)", display: false}
          ],
          throwOnError: false
        });
      }
      function initMath() { renderMath(document.body); }
      // After every HTMX fragment swap, re-run math rendering on the swapped subtree.
      document.addEventListener("htmx:afterSwap", function (e) { renderMath(e.target); });
    </script>
</head>
<body>
  {% include "_sidebar.html" %}
  {% include "_topbar.html" %}
  <main class="app-main">
    {% block content %}{% endblock %}
  </main>
  <script src="{{ url_for('static', path='app.js') }}" defer></script>
</body>
</html>
```

**Removed from base.html** (already implied above):
- The HTMX-anchor `pendingAnchor` capture + `htmx:afterSwap` scroll logic. Articles are now full pages, so the browser handles fragment scrolls natively. Math re-rendering on `afterSwap` stays (still needed for embed-status fragment loads).

### 7.2 Create `static/app.js`

This houses all the per-page glue JS that currently lives in `<script>` tags inside templates:

```js
// Theme toggle: cycles auto → light → dark → auto.
(function () {
  function applyMode(mode) {
    document.documentElement.setAttribute('data-theme-mode', mode);
    if (mode === 'auto') {
      localStorage.removeItem('theme-mode');
      document.documentElement.removeAttribute('data-theme');
    } else {
      localStorage.setItem('theme-mode', mode);
      document.documentElement.setAttribute('data-theme', mode);
    }
  }
  // Apply persisted preference on load.
  var stored = localStorage.getItem('theme-mode');
  if (stored === 'light' || stored === 'dark') {
    document.documentElement.setAttribute('data-theme', stored);
  }

  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-theme-toggle]');
    if (!btn) return;
    var current = document.documentElement.getAttribute('data-theme-mode') || 'auto';
    var next = current === 'auto' ? 'light' : current === 'light' ? 'dark' : 'auto';
    applyMode(next);
  });
})();

// Wiki-chip switch: if the current URL has an ?article= or we're on /article/X,
// preserve the article context across the wiki switch.
(function () {
  document.addEventListener('click', function (e) {
    var chip = e.target.closest('.chip--switchable[data-target-wiki]');
    if (!chip) return;
    // Path-aware: detect /article/X and pass the article through the switch.
    var match = window.location.pathname.match(/^\/article\/(.+)$/);
    if (match) {
      e.preventDefault();
      window.location.href =
        '/switch-wiki?to=' + encodeURIComponent(chip.dataset.targetWiki) +
        '&article=' + encodeURIComponent(decodeURIComponent(match[1]));
      return;
    }
    var article = new URLSearchParams(window.location.search).get('article');
    if (article) {
      e.preventDefault();
      window.location.href =
        '/switch-wiki?to=' + encodeURIComponent(chip.dataset.targetWiki) +
        '&article=' + encodeURIComponent(article);
    }
  });
})();

// Kebab action menu (Embed Manager): toggle on click, dismiss on outside-click.
(function () {
  function closeAll(except) {
    document.querySelectorAll('.action-dropdown').forEach(function (d) {
      if (d !== except) d.classList.remove('open');
    });
  }
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-toggle]');
    if (btn) {
      var el = document.getElementById(btn.getAttribute('data-toggle'));
      if (!el) return;
      var opening = !el.classList.contains('open');
      closeAll(el);
      if (opening) el.classList.add('open');
      e.stopPropagation();
      return;
    }
    closeAll();
  });
})();

// Search results: dismiss the dropdown on outside-click of the search panel.
(function () {
  document.addEventListener('click', function (e) {
    if (e.target.closest('.search-hero') || e.target.closest('.search-results')) return;
    var results = document.getElementById('results');
    if (results) results.innerHTML = '';
  });
})();
```

Move `static/style.css`'s inline-script equivalents out of templates and into here. Subsequent phases of this plan assume `app.js` exists.

### 7.3 Verification

- Every page now shows the sidebar + top bar + main content.
- Click each sidebar nav item, verify the active state highlights correctly (Home, Embeddings, Processes — Refresh page doesn't exist yet, it'll 404; that's fine, fixed in Phase 5).
- Theme toggle cycles through auto/light/dark. Reloading the page preserves the choice. With auto + macOS dark mode, the page is dark.
- `pytest tests/test_app.py -k "test_index"` should still pass (the markup changed but the route is unchanged, and the test likely asserts text content not class names — review and fix any that break).

**Commit message:** `Render sidebar + top bar shell in base.html`

---

## 8. Phase 2 — Wiki chip consolidation (commit 3)

**Goal:** Single source of truth for the wiki-switcher UI. Replace 5 copies of inline wiki-badge HTML with one partial include.

### 8.1 Files to edit

For each of the following templates, **remove** the inline `{%- if wiki == "enwiki" %}<span class="wiki-badge ...">enwiki</span>{% elif other_wiki == "enwiki" %}<a ...>enwiki</a>{% endif %}` blocks (and the simplewiki sibling), **insert** `{% include "_wiki_chip.html" %}` with the appropriate context already in scope, and **remove** the surrounding `<div class="wiki-badges">` where present.

| Template | Context needed | Notes |
|---|---|---|
| `templates/index.html` | `wiki`, `other_wiki` | Was in a `<div class="wiki-badges">` below the `<h1>`. New placement: inside `_page_header.html` title row. |
| `templates/article.html` | `wiki`, `other_wiki`, `switch_url` | Old position: inside the `<h2>`. New: inside page-header title row. Pass `switch_url=f"/switch-wiki?to={other_wiki}&article={title|urlencode}"`. |
| `templates/wikitext.html` | same as article | |
| `templates/embed_manager.html` | `wiki`, `other_wiki` | Pass `switch_url=f"/switch-wiki?to={other_wiki}&return_to=/embed-manager"`. |
| `templates/active_embedding.html` | `wiki`, `other_wiki` | Pass `switch_url=f"/switch-wiki?to={other_wiki}&return_to=/active-embedding"`. |
| `templates/refresh.html` (new in Phase 5) | `wiki`, `other_wiki` | `switch_url=f"/switch-wiki?to={other_wiki}&return_to=/refresh"`. |

To pass `switch_url` cleanly without polluting the route, set it as a local Jinja `{% set %}` before the include:

```jinja
{% set switch_url = '/switch-wiki?to=' + (other_wiki or '') + '&return_to=/embed-manager' %}
{% include "_wiki_chip.html" %}
```

### 8.2 Update `static/style.css`

Remove the `.wiki-badge` and `.wiki-badge--*` classes. They're replaced by `.chip` and `.chip--wiki-*` from §5.7 (already added in Phase 0).

### 8.3 Update routes if necessary

`app/routes/home.py` already passes `other_wiki` gated on DB existence. `app/routes/embeddings.py` and `app/routes/active_embedding.py` also do this. Verify each route passes `other_wiki=None` when the other wiki's DB doesn't exist, so the chip partial knows not to render the switch link.

### 8.4 Verification

- All five pages render exactly one wiki chip + (optionally) one switchable chip pointing at the other wiki.
- Clicking the switchable chip from `/article/X` navigates to `/switch-wiki?to=Y&article=X` and lands at `/article/X` under wiki Y (if X exists there) or at home with a not-found banner otherwise.
- `grep -r "wiki-badge" templates/ static/style.css` should return zero hits.

**Commit message:** `Consolidate wiki badges into a single _wiki_chip.html partial`

---

## 9. Phase 3 — Article routing migration to full nav (commit 4)

**Goal:** Articles get a real page route. Remove the HTMX inline-swap dance. Browser back/forward works. URLs are shareable.

### 9.1 Edit `app/routes/article.py`

- Remove the `HX-Request` handling entirely (both the `articleNotFound` JSON header dance and the `HX-Push-Url` setter — articles are never loaded via HTMX after this phase).
- Both `article()` and `wikitext()` keep their templates; the templates will now extend `base.html`.
- Pass `current_page` so the sidebar nav knows nothing is active (use `current_page=""` for articles — they don't map to a top-level nav item).
- Pass `breadcrumb_current=row["title"]`.

Final shape of `article()`:

```python
@router.get("/article/{title:path}", response_class=HTMLResponse)
def article(request: Request, title: str) -> HTMLResponse:
    row, redirected_from = fetch_article(title, request)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Article not found: {title}")
    html = convert_wikitext_to_html(row["text_content"])
    wiki = active_wiki(request)
    other_wiki = next(w for w in KNOWN_WIKIS if w != wiki)
    other_wiki_for_template = None
    other_wiki_db = paths.db_path_for(other_wiki)
    if other_wiki_db.exists():
        try:
            with wiki_db.connect(other_wiki_db) as ow_conn:
                hit = ow_conn.execute(
                    "SELECT 1 FROM articles WHERE title = ? LIMIT 1",
                    (row["title"],),
                ).fetchone()
                if hit:
                    other_wiki_for_template = other_wiki
        except Exception:
            pass
    return templates.TemplateResponse(
        request,
        "article.html",
        {
            "title": row["title"],
            "html": html,
            "text_bytes": row["text_bytes"],
            "timestamp": row["timestamp"],
            "redirected_from": redirected_from,
            "wiki": wiki,
            "other_wiki": other_wiki_for_template,
            "current_page": "",
            "breadcrumb_current": row["title"],
        },
    )
```

Same shape for `wikitext()`.

### 9.2 Rewrite `templates/article.html` as a full page

```jinja
{% extends "base.html" %}
{% block title %}{{ title }} · Local Wikipedia{% endblock %}

{% block content %}
{% set switch_url = '/switch-wiki?to=' + (other_wiki or '') + '&article=' + (title|urlencode) %}
<div class="page-header page-header--article">
  <div class="page-header__title-row">
    <h1 class="article-title">{{ title }}</h1>
    {% include "_wiki_chip.html" %}
  </div>
  {% if redirected_from %}
  <p class="article-redirect">Redirected from <em>{{ redirected_from }}</em></p>
  {% endif %}
  <p class="article-meta">
    <span class="u-tabular-nums">{{ "{:,}".format(text_bytes) }}</span> bytes
    &middot; last edited {{ timestamp }}
  </p>
  <div class="article-controls">
    <span id="embed-spinner" class="spinner htmx-indicator" aria-hidden="true"></span>
    <span id="embed-widget"
          hx-get="/embed-status/{{ title|urlencode }}"
          hx-trigger="load"
          hx-swap="outerHTML"></span>
    <a class="btn-outlined btn-outlined--sm"
       href="/wikitext/{{ title|urlencode }}">
      {% from "_icons.html" import file_text %}
      {{ file_text(size=16) }}
      View wikitext
    </a>
  </div>
</div>
<article class="article-body reading-column">
  {{ html|safe }}
</article>
{% endblock %}
```

Key changes:
- `View wikitext` is now an `<a href>` (full page nav to `/wikitext/{title}`) — no more HTMX swap. Same for `View rendered` in `wikitext.html`.
- `article-body` gets `reading-column` class which caps width at `--reading-max-width: 760px` and centres within the wider main column.
- The `article-controls` row keeps HTMX **only** for the embed-status fragment (still useful — async load lets the page paint while the RAG DB is queried).

### 9.3 Rewrite `templates/wikitext.html` similarly

Same structure as article.html, but:
- `View rendered` button is `<a href="/article/{title}">`
- Body is `<pre class="wikitext-raw">{{ wikitext }}</pre>` inside `article-body reading-column reading-column--wide` (a slightly wider variant for monospace content — bump to `--space-16` padding-right or so, let it use the full content area; you can choose).

### 9.4 Edit `templates/search_results.html`

Strip all HTMX attributes from the result links — they become plain anchors. Result hover/active behavior comes from CSS only.

```jinja
{% if titles %}
  <ul class="result-list" role="listbox">
    {% for title in titles %}
      <li class="result-list__item">
        <a class="result-list__link" href="/article/{{ title|urlencode }}">
          {{ title }}
        </a>
      </li>
    {% endfor %}
  </ul>
{% elif q %}
  <p class="empty-state">No articles match &ldquo;{{ q }}&rdquo;.</p>
{% endif %}
```

### 9.5 Edit `app/routes/home.py`

Now that articles have their own route, the home route stops accepting `?article=` as a preload param. **However**, `/switch-wiki?to=X&article=Y` still redirects to `/?wiki=X&article=Y` per the current logic. Update `switch_wiki()` so that when `article` is set, it redirects to `/article/{article}?wiki={to}` (or to the new path-based form) instead of the home page:

```python
@router.get("/switch-wiki")
def switch_wiki(to: str, article: str = "", return_to: str = "") -> RedirectResponse:
    if to not in KNOWN_WIKIS:
        raise HTTPException(status_code=400, detail=f"Unknown wiki: {to}")
    if article:
        redirect_url = f"/article/{quote(article)}"
    elif return_to and return_to.startswith("/") and not return_to.startswith("//"):
        redirect_url = return_to
    else:
        redirect_url = "/"
    response = RedirectResponse(redirect_url, status_code=302)
    response.set_cookie("wiki_pref", to, max_age=365 * 24 * 3600)
    return response
```

And remove the `preload_article` context key from `home.index()` since the homepage no longer pre-loads articles. (The homepage redesign in Phase 5 will remove all corresponding markup.)

For users who land on `/?article=X` via old bookmarks, add a one-liner backwards-compat redirect at the top of `home.index()`:

```python
if article:
    return RedirectResponse(f"/article/{quote(article)}", status_code=302)
```

### 9.6 Update `templates/index.html`

Remove from `index.html`:
- The `<article id="article" class="article">` block with `hx-trigger="load"`
- The `not_found` banner block (it was only ever set by the now-removed not-found-HTMX flow; the banner JS handler in `static/app.js` for the `articleNotFound` event is gone — delete that block from `app.js` too)
- The `pendingAnchor` capture (already removed from `base.html` in Phase 1)
- The `articleNotFound` event listener
- The `?article=` URL params handling in the wiki-chip click handler (now handled in `static/app.js` via path-aware logic)

The remainder of the index page redesign happens in Phase 5.

### 9.7 Update tests

Run `pytest tests/test_app.py -x` and fix what breaks. Likely fixes:
- Tests that did `client.get("/article/X", headers={"HX-Request": "true"})` and asserted on `HX-Push-Url` — remove those assertions, or convert the test to assert the plain-page response.
- `TestArticleNotFound` (or similar) test that expected the 200 + `HX-Trigger: articleNotFound` JSON — change it to expect a 404.
- Any test that asserts on the homepage rendering with `?article=` preload — change to assert that `/?article=X` returns a 302 redirect to `/article/X`.
- Tests that scrape result-list `<a>` tags for `hx-get` attributes — change to assert on `href`.

### 9.8 Verification

- `pytest -x` passes.
- Manual smoke test:
  1. `/` shows the new shell with the homepage main column (still has old refresh section etc. — fixed in Phase 5).
  2. Type a query in the search box, get a dropdown of titles, click one → navigate to `/article/X`. URL bar shows the article path. Browser back goes back to the search results page (well, back to homepage; the live-typed results are gone). Browser forward returns to the article.
  3. From an article page, click `View wikitext` → navigate to `/wikitext/X`. Click `View rendered` → back to `/article/X`.
  4. From an article page, click the switchable wiki chip → land on `/article/X` under the other wiki (if it exists there).
  5. Old URL `/?article=X` redirects to `/article/X`.

**Commit message:** `Migrate article routes to full-page navigation`

---

## 10. Phase 4 — Homepage redesign (commit 5)

**Goal:** Strip the homepage down to a single hero-search experience.

### 10.1 Rewrite `templates/index.html`

```jinja
{% extends "base.html" %}
{% block title %}Local Wikipedia{% endblock %}

{% block content %}
<div class="home-hero">
  <h1 class="home-hero__brand">Local Wikipedia</h1>
  <p class="home-hero__sub">{{ wiki_label }} · {{ article_count }} articles</p>

  {% set switch_url = '/switch-wiki?to=' + (other_wiki or '') %}
  {% include "_wiki_chip.html" %}

  <form class="search-hero" onsubmit="return false;" role="search">
    <label class="input-with-icon">
      {% from "_icons.html" import search as search_icon %}
      {{ search_icon(size=20, class_='input-with-icon__icon') }}
      <input
        class="input input--pill"
        type="search"
        name="q"
        placeholder="Search articles by title..."
        autocomplete="off"
        autofocus
        hx-get="/search"
        hx-trigger="keyup changed delay:300ms, search"
        hx-target="#results"
        hx-swap="innerHTML"
        hx-indicator="#search-spinner">
      <span id="search-spinner" class="spinner htmx-indicator input-with-icon__spinner" aria-hidden="true"></span>
    </label>
  </form>

  <section id="results" class="search-results" aria-live="polite"></section>
</div>
{% endblock %}
```

CSS for the hero (add to `style.css`):

```css
.home-hero {
  max-width: 720px;
  margin: 0 auto;
  padding: var(--space-16) var(--space-6) var(--space-12);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-4);
}
.home-hero__brand {
  font-size: var(--text-display);
  font-weight: var(--weight-medium);
  letter-spacing: -0.02em;
  color: var(--color-text);
  margin: 0;
}
.home-hero__sub {
  margin: 0;
  font-size: var(--text-body);
  color: var(--color-text-secondary);
}
.search-hero {
  width: 100%;
  max-width: 580px;
  margin-top: var(--space-4);
}
.input-with-icon {
  position: relative;
  display: block;
}
.input-with-icon__icon {
  position: absolute;
  left: var(--space-4);
  top: 50%;
  transform: translateY(-50%);
  color: var(--color-text-tertiary);
  pointer-events: none;
}
.input--pill {
  width: 100%;
  height: 48px;
  border-radius: var(--radius-pill);
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  font-size: 1rem;
  padding: 0 var(--space-12) 0 calc(var(--space-4) + 20px + var(--space-3));
  transition: border-color var(--motion-fast), box-shadow var(--motion-fast);
}
.input--pill:focus {
  outline: none;
  border-color: var(--color-primary);
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--color-primary) 20%, transparent);
}
.input-with-icon__spinner {
  position: absolute;
  right: var(--space-4);
  top: 50%;
  transform: translateY(-50%);
}
.search-results {
  width: 100%;
  max-width: 580px;
}
```

`.search-results` doesn't need a card frame — the result-list itself looks card-like.

### 10.2 Restyle `.result-list`

```css
.result-list {
  list-style: none;
  margin: var(--space-3) 0 0;
  padding: var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  max-height: 360px;
  overflow-y: auto;
  box-shadow: var(--elev-1);
}
.result-list__item { }
.result-list__link {
  display: block;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  color: var(--color-text);
  text-decoration: none;
  font-size: var(--text-body);
}
.result-list__link:hover,
.result-list__link:focus-visible {
  background: var(--color-primary-container);
  color: var(--color-primary-on-container);
  outline: none;
}
```

### 10.3 Update home route

Already done in Phase 3. Confirm:
- The home route no longer accepts/uses `preload_article`.
- The route still passes `wiki`, `wiki_label`, `other_wiki`, `article_count`, `current_page="home"`.

### 10.4 Verification

- `/` shows only the brand, the wiki chip + switch, the search input, and the results panel.
- Live typing produces a dropdown of titles (HTMX still works).
- Clicking a title navigates to `/article/X`.
- The page contains no refresh section. (That moves in the next phase.)

**Commit message:** `Redesign homepage as a minimal hero-search surface`

---

## 11. Phase 5 — Refresh page (commit 6)

**Goal:** Refresh controls live on their own page now. Both wikis are visible on it.

### 11.1 Add GET /refresh route

Edit `app/routes/refresh.py`:

```python
@router.get("/refresh", response_class=HTMLResponse)
def refresh_page(request: Request) -> HTMLResponse:
    wiki = active_wiki(request)
    other_wiki = next(w for w in KNOWN_WIKIS if w != wiki)
    other_wiki_for_template = other_wiki if paths.db_path_for(other_wiki).exists() else None
    # Pre-fetch latest job for each wiki so the panels render server-side
    # rather than racing the HTMX polling on first paint.
    conn = refresh_jobs.connect_jobs(paths.JOBS_DB)
    try:
        jobs = {w: refresh_jobs.get_latest_job(conn, w) for w in KNOWN_WIKIS}
    finally:
        conn.close()
    return templates.TemplateResponse(
        request,
        "refresh.html",
        {
            "wiki": wiki,
            "other_wiki": other_wiki_for_template,
            "current_page": "refresh",
            "breadcrumb_current": "Refresh",
            "page_title": "Refresh",
            "jobs": jobs,
        },
    )
```

Import `paths`, `KNOWN_WIKIS`, `active_wiki`, `templates` accordingly.

### 11.2 Create `templates/refresh.html`

```jinja
{% extends "base.html" %}
{% block title %}Refresh · Local Wikipedia{% endblock %}

{% block content %}
{% set switch_url = '/switch-wiki?to=' + (other_wiki or '') + '&return_to=/refresh' %}
<div class="page-header">
  <div class="page-header__title-row">
    <h1 class="page-title">Refresh</h1>
    {% include "_wiki_chip.html" %}
  </div>
  <p class="page-header__subtitle">Download the latest Wikimedia dumps and parse incremental updates into each wiki's database.</p>
</div>

<div class="page-grid page-grid--cols-2">
  {% for w in ['simplewiki', 'enwiki'] %}
  <section class="card refresh-card">
    <header class="card__header">
      <h2 class="card__title">{{ w }}</h2>
      <button class="btn-tonal"
              hx-post="/refresh/{{ w }}"
              hx-target="#refresh-status-{{ w }}"
              hx-swap="innerHTML"
              hx-indicator="#spinner-{{ w }}">
        {% from "_icons.html" import refresh_cw %}
        {{ refresh_cw(size=16) }}
        Refresh {{ w }}
      </button>
    </header>
    <div class="card__body">
      <span id="spinner-{{ w }}" class="spinner htmx-indicator" aria-hidden="true"></span>
      <div id="refresh-status-{{ w }}"
           hx-get="/refresh/status/{{ w }}"
           hx-trigger="load"
           hx-swap="innerHTML"></div>
    </div>
  </section>
  {% endfor %}
</div>
{% endblock %}
```

### 11.3 Refresh status panel restyle

Rewrite `templates/refresh_panel.html` to use chip/status primitives:

```jinja
<div id="refresh-status-panel-{{ wiki }}"
     {% if job and job.status in ('pending', 'downloading', 'parsing', 'rebuilding') %}
     hx-get="/refresh/status/{{ wiki }}"
     hx-trigger="every 3s"
     hx-target="this"
     hx-swap="outerHTML"
     {% endif %}>
  {% if not job %}
    <p class="empty-state">No refresh has been run yet.</p>
  {% else %}
    <div class="status-row">
      <span class="chip chip--status-{{ job.status }}">{{ job.status }}</span>
      {% if elapsed %}<span class="meta-text">{{ elapsed }}</span>{% endif %}
      {% if started_at_display %}<span class="meta-text">{{ started_at_display }}</span>{% endif %}
    </div>
    {% if job.status in ('parsing', 'rebuilding', 'complete') %}
    <dl class="stat-grid">
      <div class="stat"><dt>Scanned</dt><dd>{{ "{:,}".format(job.articles_scanned) }}</dd></div>
      <div class="stat"><dt>Skipped</dt><dd>{{ "{:,}".format(job.articles_skipped) }}</dd></div>
      <div class="stat"><dt>Updated</dt><dd>{{ "{:,}".format(job.articles_updated) }}</dd></div>
      <div class="stat"><dt>Inserted</dt><dd>{{ "{:,}".format(job.articles_inserted) }}</dd></div>
      <div class="stat"><dt>Archived</dt><dd>{{ "{:,}".format(job.articles_archived) }}</dd></div>
    </dl>
    {% endif %}
    {% if job.status == 'failed' %}
    <p class="callout callout--error">{{ job.error_message }}</p>
    {% endif %}
  {% endif %}
</div>
```

CSS additions:

```css
.page-grid { display: grid; gap: var(--space-4); }
.page-grid--cols-2 { grid-template-columns: 1fr 1fr; }
@media (max-width: 960px) { .page-grid--cols-2 { grid-template-columns: 1fr; } }

.status-row { display: flex; align-items: center; gap: var(--space-3); flex-wrap: wrap; }
.meta-text { font-size: var(--text-small); color: var(--color-text-secondary); }

.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: var(--space-3) var(--space-4);
  margin: var(--space-4) 0 0;
}
.stat { margin: 0; }
.stat dt {
  font-size: var(--text-micro); text-transform: uppercase; letter-spacing: .06em;
  color: var(--color-text-secondary); font-weight: var(--weight-medium);
}
.stat dd { margin: 2px 0 0; font-variant-numeric: tabular-nums; font-weight: var(--weight-medium); }

.callout { padding: var(--space-3); border-radius: var(--radius-md); font-size: var(--text-small); margin: var(--space-3) 0 0; }
.callout--error { background: var(--color-error-container); color: var(--color-error-on-container); }
.callout--warning { background: var(--color-warning-container); color: var(--color-warning-on-container); }
.callout--info { background: var(--color-info-container); color: var(--color-info-on-container); }
```

### 11.4 Verification

- Sidebar shows "Refresh" as active when on `/refresh`.
- Both wikis render in side-by-side cards. Clicking a refresh button kicks off the job and the status panel polls.
- No refresh section anywhere else (specifically: not on the homepage).
- `pytest tests/test_app.py` — fix any tests that asserted refresh markup on the homepage.

**Commit message:** `Add dedicated /refresh page; remove refresh from homepage`

---

## 12. Phase 6 — Embed Manager redesign (commit 7)

**Goal:** Replace the dense flat table with a card-framed data table; restyle the kebab menu; restyle the empty state and pagination.

### 12.1 Rewrite `templates/embed_manager.html`

```jinja
{% extends "base.html" %}
{% block title %}Embeddings · Local Wikipedia{% endblock %}

{% block content %}
{% set switch_url = '/switch-wiki?to=' + (other_wiki or '') + '&return_to=/embed-manager' %}
<div class="page-header">
  <div class="page-header__title-row">
    <h1 class="page-title">Embeddings</h1>
    {% include "_wiki_chip.html" %}
  </div>
  <p class="page-header__subtitle">
    {% if total_count %}
      {{ "{:,}".format(total_count) }} article{{ "s" if total_count != 1 }} embedded
    {% else %}
      No articles embedded yet
    {% endif %}
  </p>
</div>

{% if total_count == 0 %}
<div class="card empty-state-card">
  <div class="empty-state">
    {% from "_icons.html" import database %}
    {{ database(size=32, class_='empty-state__icon') }}
    <p class="empty-state__text">
      Open any article and click <strong>Embed</strong> to add it to the RAG index.
    </p>
  </div>
</div>
{% else %}

<div class="toolbar">
  <button class="btn-outlined btn-danger"
          hx-delete="/embed-all/{{ wiki }}"
          hx-confirm="Delete all {{ '{:,}'.format(total_count) }} embeddings for {{ wiki }}? This cannot be undone.">
    {% from "_icons.html" import trash %}
    {{ trash(size=16) }}
    Delete all
  </button>
</div>

<section class="card">
  <div class="data-table-wrapper">
    <table class="data-table">
      <thead>
        <tr>
          <th>Title</th>
          <th>Chunks</th>
          <th>Size</th>
          <th>Embedded</th>
          <th>Links</th>
          <th>Categories</th>
          <th class="data-table__col-actions"></th>
        </tr>
      </thead>
      <tbody>
        {% for a in articles %}
        <tr>
          <td><a class="link-primary" href="/article/{{ a.title|urlencode }}">{{ a.title }}</a></td>
          <td class="u-tabular-nums">
            <a class="link-muted" href="/chunks/{{ a.title|urlencode }}">{{ a.chunk_count }}</a>
          </td>
          <td class="u-tabular-nums meta-text">
            {% if a.article_size_bytes is not none %}{{ "{:,}".format(a.article_size_bytes) }}&nbsp;B{% else %}—{% endif %}
          </td>
          <td class="meta-text">{{ a.embedded_at_display }}</td>
          <td>
            {% if a.links_embedded %}
              <span class="chip chip--status-complete">
                {% from "_icons.html" import check %}{{ check(size=14) }}
              </span>
            {% else %}—{% endif %}
          </td>
          <td class="meta-text u-truncate-2">
            {% if a.categories %}{{ a.categories.replace("|", " · ") }}{% endif %}
          </td>
          <td class="data-table__cell-actions">
            <div class="action-menu">
              <button type="button" class="btn-icon" data-toggle="action-{{ loop.index }}" aria-label="Actions">
                {% from "_icons.html" import more_vertical %}{{ more_vertical(size=18) }}
              </button>
              <div class="action-dropdown" id="action-{{ loop.index }}" role="menu">
                <button type="button"
                        class="action-menu__item action-menu__item--danger"
                        hx-delete="/embed/{{ wiki }}/{{ a.title|urlencode }}"
                        hx-target="closest tr"
                        hx-swap="outerHTML"
                        hx-confirm="Delete '{{ a.title }}' from the RAG index?">
                  {% from "_icons.html" import trash %}{{ trash(size=16) }} Delete
                </button>
                <button type="button" class="action-menu__item"
                        hx-post="/embed/reembed/{{ wiki }}/{{ a.title|urlencode }}">
                  {% from "_icons.html" import refresh_cw %}{{ refresh_cw(size=16) }} Re-embed
                </button>
                <button type="button" class="action-menu__item"
                        hx-post="/embed-links/{{ a.title|urlencode }}">
                  {% from "_icons.html" import link2 %}{{ link2(size=16) }} Re-embed + Links
                </button>
              </div>
            </div>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</section>

{% if total_pages > 1 %}
<nav class="pagination" aria-label="Pagination">
  {% if page > 1 %}
    <a class="btn-outlined" href="/embed-manager?page={{ page - 1 }}">← Previous</a>
  {% endif %}
  <span class="meta-text">Page {{ page }} of {{ total_pages }}</span>
  {% if page < total_pages %}
    <a class="btn-outlined" href="/embed-manager?page={{ page + 1 }}">Next →</a>
  {% endif %}
</nav>
{% endif %}
{% endif %}
{% endblock %}
```

### 12.2 Action-menu polish

Add to `style.css` (replaces existing `.action-*` rules):

```css
.action-menu { position: relative; display: inline-block; }
.action-dropdown {
  display: none;
  position: absolute;
  right: 0;
  top: calc(100% + var(--space-1));
  z-index: 200;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--elev-3);
  min-width: 200px;
  padding: var(--space-1);
}
.action-dropdown.open { display: block; }
.action-menu__item {
  display: flex; align-items: center; gap: var(--space-2);
  width: 100%; padding: var(--space-2) var(--space-3);
  background: transparent; border: 0; border-radius: var(--radius-sm);
  font: var(--weight-regular) var(--text-small) var(--font-sans);
  color: var(--color-text); cursor: pointer; text-align: left;
}
.action-menu__item:hover { background: var(--color-surface-variant); }
.action-menu__item--danger { color: var(--color-error); }
.action-menu__item--danger:hover { background: var(--color-error-container); color: var(--color-error-on-container); }
```

### 12.3 Embed-status widget restyle

Rewrite `templates/embed_status_widget.html` so the three states (embedded / error / not embedded) share the same `.embed-controls` row, with button styles updated to use the new primitives. Replace `.embed-btn` with `.btn-outlined.btn-outlined--sm`, replace `.embed-badge` with `.chip.chip--status-complete`, and use Lucide icons inline (`link2`, `check`, `eye`).

Example state (embedded):

```jinja
<span id="embed-widget" class="embed-controls">
  <span class="chip chip--status-complete">
    {% from "_icons.html" import check %}{{ check(size=14) }} embedded
  </span>
  {% if links_embedded %}
    <span class="chip chip--status-complete">
      {% from "_icons.html" import check %}{{ check(size=14) }} links embedded
    </span>
  {% else %}
    <button class="btn-outlined btn-outlined--sm"
            hx-post="/embed-links/{{ title|urlencode }}" hx-swap="none">
      {% from "_icons.html" import link2 %}{{ link2(size=14) }}
      Embed + links{% if link_count_1hop is not none %} ({{ "{:,}".format(link_count_1hop) }}){% endif %}
    </button>
  {% endif %}
  <button class="btn-outlined btn-outlined--sm"
          hx-post="/embed-links-2/{{ title|urlencode }}" hx-swap="none">
    {% from "_icons.html" import link2 %}{{ link2(size=14) }}
    Embed + links²<span class="link-count"
      hx-get="/embed-count-2/{{ title|urlencode }}"
      hx-trigger="load"
      hx-swap="outerHTML"> …</span>
  </button>
  <a href="/chunks/{{ title|urlencode }}" class="btn-outlined btn-outlined--sm">
    {% from "_icons.html" import eye %}{{ eye(size=14) }} View chunks
  </a>
</span>
```

### 12.4 Verification

- Embed Manager renders the new card-wrapped table. Hover states feel like a Google Workspace app.
- Kebab menus open on click, dismiss on outside-click.
- Delete-all button uses the danger style.
- Empty state (try with a wiki that has no embeds) renders the empty-state card with the database icon.
- Pagination shows Previous/Next as outlined buttons.

**Commit message:** `Redesign Embed Manager: card-framed table, polished action menu`

---

## 13. Phase 7 — Processes (Active Embedding) + Chunks (commit 8)

### 13.1 `templates/active_embedding.html`

```jinja
{% extends "base.html" %}
{% block title %}Processes · Local Wikipedia{% endblock %}
{% block content %}
{% set switch_url = '/switch-wiki?to=' + (other_wiki or '') + '&return_to=/active-embedding' %}
<div class="page-header">
  <div class="page-header__title-row">
    <h1 class="page-title">Processes</h1>
    {% include "_wiki_chip.html" %}
  </div>
  <p class="page-header__subtitle">Embedding jobs for the current wiki.</p>
</div>

<section class="card">
  <header class="card__header">
    <h2 class="card__title">Job history</h2>
  </header>
  <div class="card__body card__body--flush">
    {% include "job_list_panel.html" %}
  </div>
</section>

<section class="card" style="margin-top: var(--space-4)">
  <header class="card__header">
    <h2 class="card__title">Active job</h2>
  </header>
  <div class="card__body">
    {% include "active_embedding_panel.html" %}
  </div>
</section>
{% endblock %}
```

### 13.2 `templates/active_embedding_panel.html`

Rewrite to use chips (`chip--status-{status}`) and the same `.stat-grid` / `.status-row` / `.callout` primitives. Source-group sub-tables get `.data-table` styling but without the outer card (they're nested inside the page-level card already).

Snippet for the status row:

```jinja
<div id="active-embedding-panel" {% if job and job.status == 'running' and not job.cancel_requested %}
     hx-get="/active-embedding/panel" hx-trigger="every 3s" hx-target="this" hx-swap="outerHTML" {% endif %}>
  {% if not job %}
    <p class="empty-state">No batch embed has been run yet. Open an article and click <strong>Embed + links</strong> to start one.</p>
  {% else %}
    <div class="status-row">
      <span class="chip chip--status-{{ job.status }}">{{ job.status }}{% if job.cancel_requested and job.status == 'running' %} (cancelling…){% endif %}</span>
      {% if elapsed %}<span class="meta-text">{{ elapsed }}</span>{% endif %}
      {% if started_at_display %}<span class="meta-text">{{ started_at_display }}</span>{% endif %}
      <span class="meta-text">job #{{ job.id }}</span>
      {% if job.triggered_by_title %}
        <span class="meta-text">
          triggered by <a class="link-primary" href="/article/{{ job.triggered_by_title|urlencode }}">{{ job.triggered_by_title }}</a>
        </span>
      {% endif %}
      {% if job.status == 'running' and not job.cancel_requested %}
        <button class="btn-outlined btn-danger btn-outlined--sm"
                hx-post="/active-embedding/cancel/{{ job.id }}"
                hx-target="#active-embedding-panel" hx-swap="outerHTML">
          {% from "_icons.html" import x %}{{ x(size=14) }} Cancel
        </button>
      {% endif %}
    </div>
    {% if counts %}
    <dl class="stat-grid">
      {% for status, n in counts.items() %}
        <div class="stat"><dt>{{ status.replace('_', ' ') }}</dt><dd>{{ "{:,}".format(n) }}</dd></div>
      {% endfor %}
    </dl>
    {% endif %}
    {% if job.error_message %}<p class="callout callout--error">{{ job.error_message }}</p>{% endif %}

    {% for source_title, items in grouped_items %}
    <section class="job-source-group">
      <h3 class="job-source-group__title">
        From <a class="link-primary" href="/article/{{ source_title|urlencode }}">{{ source_title }}</a>
        <span class="meta-text">({{ items|length }} item{{ "s" if items|length != 1 }})</span>
      </h3>
      <table class="data-table data-table--nested">
        <thead><tr><th>Title</th><th>Status</th><th>Chunks</th><th>Note</th></tr></thead>
        <tbody>
          {% for item in items %}
          <tr>
            <td><a class="link-primary" href="/article/{{ item.title|urlencode }}">{{ item.title }}</a></td>
            <td><span class="chip chip--status-{{ item.status }}">{{ item.status.replace('_', ' ') }}</span></td>
            <td class="u-tabular-nums">
              {% if item.chunk_count %}<a class="link-muted" href="/chunks/{{ item.title|urlencode }}">{{ item.chunk_count }}</a>{% endif %}
            </td>
            <td class="meta-text">{% if item.error_message %}{{ item.error_message }}{% endif %}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </section>
    {% endfor %}
  {% endif %}
</div>
```

### 13.3 `templates/job_list_panel.html`

Replace `<input type="search">` with the `.input` class, replace `<table class="embed-table">` with `<table class="data-table">`, replace the existing chip-like badges with `.chip.chip--status-{status}`, replace the pagination anchors with `.btn-outlined`.

### 13.4 `templates/chunks.html`

```jinja
{% extends "base.html" %}
{% block title %}Chunks: {{ title }} · Local Wikipedia{% endblock %}
{% block content %}
<div class="page-header">
  <div class="page-header__title-row">
    <h1 class="page-title">{{ title }}</h1>
  </div>
  <p class="page-header__subtitle">{{ chunks|length }} chunk{{ "s" if chunks|length != 1 }} · <a class="link-primary" href="/article/{{ title|urlencode }}">View article</a></p>
</div>

<div class="chunks-list">
{% for chunk in chunks %}
  <article class="card chunk-card">
    <header class="card__header">
      <div>
        <strong>Chunk {{ loop.index }}</strong>
        <span class="meta-text">· {{ chunk.section if chunk.section else "Introduction" }}{% if chunk.chunk_index > 0 %} (part {{ chunk.chunk_index + 1 }}){% endif %}</span>
      </div>
      <span class="meta-text u-tabular-nums">{{ "{:,}".format(chunk.text_length) }} chars</span>
    </header>
    <div class="card__body chunk-card__text">{{ chunk.text }}</div>
  </article>
{% endfor %}
</div>
{% endblock %}
```

Where:
```css
.chunks-list { display: flex; flex-direction: column; gap: var(--space-3); margin-top: var(--space-4); }
.chunk-card__text {
  font-size: var(--text-body);
  line-height: var(--leading-normal);
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--color-surface-sunken);
  margin: 0; /* card body already has padding */
}
```

### 13.5 Verification

- `/active-embedding` renders two cards (Job history + Active job).
- Status chips use the new tokens; dark mode looks coherent.
- `/chunks/{title}` shows the new chunk-card layout, with a link back to the article.

**Commit message:** `Redesign Processes + Chunks pages with card primitives`

---

## 14. Phase 8 — Article body polish + dark-mode audit (commit 9)

**Goal:** The article reader inside the new shell still feels like a Wikipedia article (preserve the rendering), but the surrounding chrome is polished, and dark mode is coherent everywhere.

### 14.1 Article body widths and typography

```css
.reading-column { max-width: var(--reading-max-width); margin: 0 auto; }
.reading-column--wide { max-width: 980px; }
.article-body {
  font-size: 1rem;
  line-height: var(--leading-relaxed);
  color: var(--color-text);
}
.article-body h2 { font-size: 1.5rem; margin-top: var(--space-8); font-weight: var(--weight-medium); }
.article-body h3 { font-size: 1.2rem; margin-top: var(--space-6); font-weight: var(--weight-medium); }
.article-body h4 { font-size: 1.05rem; margin-top: var(--space-5); font-weight: var(--weight-medium); }
.article-body a { color: var(--color-primary); }
.article-body a:visited { color: var(--color-primary); } /* drop the dim-blue visited */
.article-body code, .article-body pre {
  background: var(--color-surface-sunken);
}
.article-body pre { border: 1px solid var(--color-divider); border-radius: var(--radius-md); }
```

### 14.2 Wikipedia table + infobox dark-mode

The Wikipedia tables use hardcoded blues/greys. Override them in `[data-theme="dark"]` and inside `@media (prefers-color-scheme: dark)`:

```css
[data-theme="dark"] .article-body table.wikitable,
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) .article-body table.wikitable,
  :root:not([data-theme="light"]) .article-body table.wikitable th,
  :root:not([data-theme="light"]) .article-body table.wikitable td {
    color: var(--color-text);
    border-color: var(--color-border-strong);
    background: var(--color-surface);
  }
  /* etc. — same for infobox, hatnote, indicator-* etc. */
}
```

You don't have to make tables look identical to the light version — just legible. Aim for: borders use `--color-border-strong`, header backgrounds use `--color-surface-variant`, cell backgrounds use `--color-surface`, text uses `--color-text`. Indicator colors (green/red/yellow) can stay the same in both modes (their containers already use sufficiently low-saturation tones to read against either bg).

### 14.3 Audit checklist

For every page in light mode and dark mode, click through and verify:

- [ ] Sidebar + topbar render coherently
- [ ] Wiki chips legible against page bg
- [ ] Buttons readable, hover states differentiate
- [ ] Status chips use semantic tokens (not hardcoded hex)
- [ ] Cards have visible elevation/border in both modes
- [ ] Code blocks + raw wikitext readable
- [ ] Infoboxes + Wikipedia tables readable
- [ ] Form inputs (search) readable
- [ ] HTMX spinners visible
- [ ] Action menu shadow visible in dark mode

For anything that fails, add a `[data-theme="dark"]` or `prefers-color-scheme` override.

### 14.4 Verification

- Manually toggle the theme on every page (home, article, wikitext, embed manager, processes, refresh, chunks). No illegible text.
- `pytest` still green.

**Commit message:** `Polish article body + audit dark mode across all pages`

---

## 15. Phase 9 — Test updates + cleanup (commit 10)

### 15.1 Test fixes you may already have done

The earlier phases should have surfaced and fixed most test breakages. Re-run `pytest -x` once more and squash any remaining issues. Common failure shapes:

- Tests asserting that the homepage contains the string `Refresh SimpleWiki` — move them into a new `tests/test_app.py::TestRefreshPage` class that hits `/refresh`.
- Tests asserting on `hx-get="/article/X"` in result-list links — change to `href="/article/X"`.
- Tests asserting on `wiki-badge` classes — change to `chip chip--wiki-*`.
- Tests asserting the article-fragment response was 200 with the `HX-Trigger` header on not-found — change to expect a 404 instead.

### 15.2 New tests to add

```python
class TestThemeToggle:
    def test_theme_persists_via_localstorage(self, client):
        # Smoke: the theme-toggle button exists in the rendered HTML
        resp = client.get("/")
        assert 'data-theme-toggle' in resp.text

class TestRefreshPage:
    def test_refresh_page_renders(self, client):
        resp = client.get("/refresh")
        assert resp.status_code == 200
        assert "Refresh" in resp.text
        # Both wikis appear
        assert "simplewiki" in resp.text and "enwiki" in resp.text

class TestArticleFullPage:
    def test_article_renders_full_shell(self, client):
        resp = client.get("/article/Apple")  # assuming fixture has "Apple"
        assert resp.status_code == 200
        # Shell is present
        assert 'app-sidebar' in resp.text
        # Article body is present
        assert 'reading-column' in resp.text

    def test_old_home_article_param_redirects(self, client):
        resp = client.get("/?article=Apple", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/article/Apple"

    def test_article_not_found_returns_404(self, client):
        resp = client.get("/article/DoesNotExist")
        assert resp.status_code == 404
```

### 15.3 Remove dead code

After Phase 3 the homepage no longer needs `articleNotFound`, `pendingAnchor`, or the not-found-banner. Verify:

- `static/app.js` has no `articleNotFound` listener
- `app/routes/article.py` has no `HX-Request` / `HX-Push-Url` / `articleNotFound` JSON header logic
- `templates/index.html` has no `not-found-banner` block, no `pendingAnchor` capture, no `articleNotFound` listener

Delete unused CSS classes from `static/style.css`:
- `.wiki-badge`, `.wiki-badge--*`, `.wiki-badge--switch`
- `.refresh-section`, `.refresh-heading`, `.refresh-grid`, `.refresh-col`, `.refresh-btn-row`, `.refresh-btn`, `.refresh-status-area`, `.refresh-panel`, `.refresh-badge--*`, `.refresh-status-row`, `.refresh-elapsed`, `.refresh-counts`, `.refresh-error`, `.refresh-idle`, `.refresh-btn-row` — replaced by `.chip`, `.callout`, `.stat-grid`, `.status-row`, `.meta-text`, `.empty-state`
- `.nav-btn`, `.nav-btn-group`, `.nav-btn--active`, `.nav-links`, `.nav-link` — replaced by sidebar
- `.embed-count-row`, `.embed-count` — replaced by page-header subtitle
- `.embed-table`, `.embed-table-wrapper`, `.embed-chunks`, `.embed-size`, `.embed-timestamp`, `.embed-links-col`, `.embed-categories`, `.embed-actions-col`, `.embed-pagination`, `.embed-page-btn`, `.embed-page-info` — replaced by `.data-table`, `.pagination`
- `.embed-controls`, `.embed-btn`, `.embed-badge`, `.embed-badge--done`, `.embed-error-note`, `.links-badge`, `.embed-source-group`, `.embed-source-heading`, `.embed-source-count`, `.embed-recent-jobs` — replaced by `.btn-outlined`, `.chip`, `.callout`, `.job-source-group`
- `.view-toggle` — replaced by `.btn-outlined.btn-outlined--sm`
- `.action-btn` — replaced by `.btn-icon`

Delete `templates/_nav.html` — replaced by the sidebar partial.

### 15.4 Update `CLAUDE.md`

In the "Web app (`app/` package)" section, update the bullet about the `_nav.html` partial — replace it with a note about the sidebar + topbar partials and how `current_page` now drives sidebar-item highlighting.

### 15.5 Run formatter / linter

```bash
ruff check . --fix
ruff format .
pytest
```

**Commit message:** `Remove deprecated CSS + dead JS; update tests for new shell`

---

## 16. Sidebar nav active-state mapping

Routes must pass `current_page` matching one of:
- `"home"` → home
- `"embeddings"` → embed-manager
- `"processes"` → active-embedding
- `"refresh"` → refresh
- `""` → article / wikitext / chunks (nothing highlighted)

The chunks page currently passes `current_page=""` — that stays. Article + wikitext routes were not previously passing `current_page` to a sidebar; in the new shell they pass `current_page=""` for the same reason.

---

## 17. Out of scope (do not implement)

- Responsive / mobile design — desktop-first, sidebar always 220px
- Vendoring Google Sans or Roboto — use the system stack
- A persistent global search input in the top bar — homepage is the search surface
- A wiki switcher in the sidebar — switching happens via the inline chips per page
- Animation of sidebar item transitions beyond the existing CSS `transition` properties — keep it subtle
- Internationalisation of UI strings — the app is single-locale
- Storybook / component playground

---

## 18. Final verification (run before declaring done)

Manual smoke test, both light and dark modes:

1. **Home** — type "App" → see live result dropdown → click → land on `/article/Apple` (or whatever).
2. **Article** — wiki chips render; "View wikitext" link works; "embed-status" widget loads asynchronously; switching wiki via chip preserves the article context.
3. **Wikitext** — raw wikitext renders inside the reading column; "View rendered" navigates back.
4. **Embed Manager** — table is card-framed; kebab menus open/close on click; Delete All confirms; pagination works.
5. **Processes** — both cards render; status chips coloured by status; cancelling a running job works.
6. **Refresh** — both wiki cards render; clicking refresh starts a job; status panel polls every 3s.
7. **Chunks** — chunk cards render; "View article" link works.
8. **Browser back/forward** — works at every transition.
9. **Theme toggle** — auto / light / dark cycle persists across reloads.
10. **`pytest`** — green.
11. **`ruff check . && ruff format --check .`** — clean.

---

## 19. Estimated effort

| Phase | Lines changed | Complexity |
|---|---|---|
| 0. Tokens + partials | ~700 (new CSS + new HTML partials) | Medium — lots of typing, low decision-density |
| 1. Base shell | ~50 | Low |
| 2. Wiki chips | ~40 | Low — find-and-replace |
| 3. Article routing | ~150 | Medium — touches routes + tests |
| 4. Homepage redesign | ~100 | Low |
| 5. Refresh page | ~100 (new) | Low |
| 6. Embed manager | ~150 | Medium |
| 7. Processes + Chunks | ~150 | Medium |
| 8. Article polish + dark audit | ~100 | Medium — touches many overrides |
| 9. Tests + cleanup | ~150 (tests, deletions) | Medium |
| **Total** | **~1,700** | — |

Plan completes in a single working session at Sonnet 4.6 high effort. Commit at every phase boundary — do not bundle.
