# Math/Equation Embedding for RAG

## Context

`rag.chunker.chunk_article` runs each section through `mwparserfromhell.strip_code()`, which discards `<math>…</math>` tags and the `{{math|…}}` / `{{tmath|…}}` / `{{mvar|…}}` / `{{bigmath|…}}` template families *entirely*. `<chem>` / `<ce>` tags and `{{chem|…}}` / `{{chem2|…}}` / `{{mhchem|…}}` / `{{ce|…}}` template forms are stripped the same way. The render pipeline (`render/protect.py` + `render/templates.replace_math_templates`) preserves these for KaTeX, but **none of that preprocessing is reused by the RAG pipeline**.

Verified empirically via `mwparserfromhell.parse(...).strip_code()`:

| Wikitext | After `strip_code()` |
|---|---|
| `<math>E = mc^2</math>` | `''` |
| `<math display="block">…</math>` | `''` |
| `{{math\|E=mc^2}}` | `''` |
| `{{mvar\|x}}` | `''` |
| `{{tmath\|\frac{a}{b}}}` | `'}'` (mangled) |
| `{{chem\|H\|2\|O}}` | `''` |
| `The formula {{math\|E=mc^2}} is famous, while <math>a^2+b^2=c^2</math> is older.` | `'The formula  is famous, while  is older.'` |

Practical consequence: embeddings for equation-bearing articles (Mass–energy equivalence, Pythagorean theorem, Schrödinger equation, stoichiometry, …) carry only the surrounding prose. Neither the dense (sqlite-vec) nor sparse (FTS5 porter) path can match equation-content queries.

This change adds a pre-pass that converts math/chem constructs to plain-text bodies that survive `strip_code()`, so equations are embedded **inline** within their containing prose / table / infobox chunk. No schema, retrieval, or UI changes; `chunk_type` stays `'prose'` / `'table'` / `'infobox'`.

## Approach

One new module `rag/math.py`, one new call site in `rag/chunker.py:chunk_article`.

### `rag/math.py` (new)

Public function `normalize_math(wikitext: str) -> str` that returns wikitext with every math/chem construct rewritten to plain text. Pipeline:

1. **`replace_math_templates(wikitext)`** — reused verbatim from `render/templates.py:408`. Handles the `{{math|…}}` / `{{tmath|…}}` / `{{mvar|…}}` / `{{bigmath|…}}` / `{{math block|…}}` / `{{tmath block|…}}` family. Already implements the brace-aware closer scan (`_find_template_close` at `render/templates.py:327`) needed for LaTeX bodies like `\frac{a}{b}}}` that defeat mwparserfromhell's greedy `}}` matcher. Output contains `<math>…</math>` wrappers (for tmath / tmath block) or `<span class="texhtml">…</span>` wrappers (for math / bigmath / mvar).
2. **Strip `<math>` wrappers**: regex `<math\b[^>]*>(.*?)</math>` (DOTALL, IGNORECASE) → inner body. (Pattern mirrors `render/protect.py:19,23`.)
3. **Strip `<chem>` / `<ce>` wrappers**: regex `<(chem|ce)\b[^>]*>(.*?)</\1>` → inner body. (Pattern mirrors `render/protect.py:30`.) Bodies are mhchem syntax — plain ASCII, survives `strip_code()` unchanged.
4. **Substitute chem template family**: replace `{{chem|…}}` / `{{chem2|…}}` / `{{mhchem|…}}` / `{{ce|…}}` with the concatenation of positional args (e.g. `{{chem|H|2|O}}` → `H2O`). Implemented as a small local helper that reuses `_find_template_close` and a positional-split routine analogous to `_math_first_positional`. ~30 lines.

The `<span class="texhtml">` wrappers from step 1 do not need stripping — `strip_code()` preserves their inner text content (verified).

### `rag/chunker.py` change

In `chunk_article` (`rag/chunker.py:109`), insert one line after the redirect check and before `extract_infoboxes`:

```python
wikitext = normalize_math(wikitext)
```

That's the entire integration point. `extract_infoboxes` and `extract_tables` both call `mwparserfromhell.strip_code()` via `_strip_cell`/`_parse_cell_content` (`rag/tables.py:22,33`), so math/chem inside an infobox value or table cell is also preserved.

### What is *not* changing

- **Schema** (`rag/schema.py`): no migration.
- **Retrieval** (`rag/retriever.py`): no change.
- **Chunks UI** (`templates/chunks.html`): no new chip — equations live inline within their `'prose'` / `'table'` / `'infobox'` chunk.
- **`MAX_CHUNK_CHARS = 1600`**: leave as is. Math-heavy articles may push a small fraction of chunks past the ~512-token sweet spot for nomic-embed-text; if `scripts/calibrate_chunks.py` shows real degradation post-change, lower in a follow-up.

### Re-embedding

No automation. enwiki has no RAG data yet (per `project_enwiki_full` memory). For wikis already embedded (e.g. simplewiki) the change applies only on next embed run; affected articles need `python -m rag.embed --wiki simplewiki --reset`. Note this in the PR description.

## Files

- **New**: `rag/math.py` — the normalizer.
- **Edit**: `rag/chunker.py` — one import, one line in `chunk_article`.
- **New**: `tests/test_rag_math.py` — unit + integration tests.
- **Edit**: `tests/test_rag_chunker.py` — one regression test asserting `<math>` content survives in chunks.
- **Edit**: `CLAUDE.md` — short bullet under "Chunking" noting math/chem preservation.

## Reused existing code

- `render.templates.replace_math_templates` (`render/templates.py:408`) — covers math template family, brace-aware.
- `render.templates._find_template_close` (`render/templates.py:327`) — promote to module-public or copy for the chem-template scan.
- `render.templates._math_first_positional` (`render/templates.py:355`) — pattern for first-`|` positional, adapted for chem positional concatenation.
- `render.data.MATH_TEMPLATE_NAMES` / `LATEX_MATH_TEMPLATE_NAMES` / `MVAR_TEMPLATE_NAMES` (`render/data.py:68-74`) — already consumed by `replace_math_templates`; no direct import needed.

## Tests

### `tests/test_rag_math.py` (new)

Unit tests for `normalize_math`:
- `<math>E=mc^2</math>` → contains `E=mc^2`.
- `<math display="block">x</math>` → contains `x`.
- `{{math|x^2}}` → contains `x^2`.
- `{{mvar|x}}` → contains `x`.
- `{{tmath|\frac{a}{b}}}` → contains `\frac{a}{b}` (LaTeX-brace edge case).
- `{{bigmath|…}}`, `{{math block|…}}`, `{{tmath block|…}}` — coverage parity with `MATH_TEMPLATE_NAMES`.
- `<chem>H2O</chem>` and `<ce>2H2 + O2 -> 2H2O</ce>` — bodies preserved.
- `{{chem|H|2|O}}` → contains `H2O`.
- `{{chem2|…}}`, `{{mhchem|…}}`, `{{ce|…}}` template forms.
- Malformed (unclosed tag/template) — source left intact.
- HTML-math escape: `{{math|a {{=}} b}}` → contains `a = b`.

Integration tests through `chunk_article`:
- `chunk_article("Pythagorean theorem", "The theorem states <math>a^2 + b^2 = c^2</math>.")` — exactly one prose chunk whose `text` contains `a^2 + b^2 = c^2`.
- Math inside infobox field value (`mass = {{math|E=mc^2}}`) — the infobox chunk's text contains `E=mc^2`.
- Math inside a table cell — table chunk text contains the body.

### `tests/test_rag_chunker.py` (edit)

Add one regression test `test_math_tag_preserved_in_chunk_text` so the contract is asserted at the chunker layer too (not only in `test_rag_math.py`).

## Verification

```bash
# 1. Tests
pytest tests/test_rag_math.py -v
pytest tests/test_rag_chunker.py tests/test_rag_tables.py -v

# 2. Lint
ruff check rag/math.py rag/chunker.py tests/test_rag_math.py
ruff format --check rag/math.py rag/chunker.py tests/test_rag_math.py

# 3. End-to-end smoke against simplewiki (Ollama running with nomic-embed-text)
python -m rag.embed --wiki simplewiki --reset --limit 200
python -c "
from rag.retriever import retrieve
import pathlib
results = retrieve(pathlib.Path('dumps/simplewiki_rag.db'), 'mass energy equivalence formula', k=5)
for r in results:
    print(r.title, '|', r.text[:120])
"
# Expect: Mass–energy equivalence / E=mc² chunks return with the equation text inside .text.

# 4. Optional: recalibrate token counts post-change
python scripts/calibrate_chunks.py --wiki simplewiki --sample 200
# If p95 token count drifts well past 400, lower MAX_CHUNK_CHARS in a follow-up.
```
