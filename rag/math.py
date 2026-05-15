"""Pre-process wikitext math and chemistry constructs so they survive
``mwparserfromhell.strip_code()``.

Without this pass, both ``<math>…</math>`` / ``<chem>…</chem>`` / ``<ce>…</ce>``
HTML tags AND the ``{{math|…}}`` / ``{{tmath|…}}`` / ``{{mvar|…}}`` /
``{{bigmath|…}}`` / ``{{chem|…}}`` / ``{{ce|…}}`` template families are dropped
wholesale during chunking, leaving the RAG index unable to retrieve
equation- or formula-bearing passages.

Strategy:
  1. Reuse ``render.templates.replace_math_templates`` for the math template
     family — it already handles the LaTeX-brace closer edge case
     (``\\frac{a}{b}}}``) that defeats mwparserfromhell's greedy ``}}`` matcher.
  2. Strip ``<math>`` tag wrappers — ``strip_code()`` would otherwise drop their
     bodies entirely.
  3. Substitute the chem template family into concatenated positional args
     (``{{chem|H|2|O}}`` → ``H2O``).
  4. Strip ``<chem>`` / ``<ce>`` tag wrappers, keeping their mhchem bodies.

The ``<span class="texhtml">`` wrappers emitted by step 1 for HTML-flavoured math
templates do not need stripping — ``strip_code()`` preserves their inner text.
"""

import re

from render.templates import replace_math_templates

_MATH_TAG_RE = re.compile(r"<math\b[^>]*>(.*?)</math>", re.DOTALL | re.IGNORECASE)
_CHEM_TAG_RE = re.compile(r"<(chem|ce)\b[^>]*>(.*?)</\1>", re.DOTALL | re.IGNORECASE)

_CHEM_TEMPLATE_NAMES = ("chem", "chem2", "mhchem", "ce")
_CHEM_TEMPLATE_OPEN_RE = re.compile(
    r"\{\{\s*(" + "|".join(re.escape(n) for n in _CHEM_TEMPLATE_NAMES) + r")\s*([|}])",
    re.IGNORECASE,
)


def _find_template_close(text: str, content_start: int) -> int | None:
    """Find the matching ``}}`` for a template whose body starts at ``content_start``.

    Tracks unmatched inner single ``{`` braces so bodies like ``\\frac{a}{b}}}``
    close on the *outer* ``}}`` rather than gobbling the body's last ``}``.
    Returns the index of the first ``}`` of the closing pair, or ``None`` if
    unbalanced. Mirrors the helper in ``render/templates.py``.
    """
    n = len(text)
    inner = 0
    i = content_start
    while i < n - 1:
        c = text[i]
        if c == "{":
            inner += 1
            i += 1
        elif c == "}":
            if inner == 0 and text[i + 1] == "}":
                return i
            if inner > 0:
                inner -= 1
            i += 1
        else:
            i += 1
    return None


def _chem_positional_args(body: str) -> list[str]:
    """Split a chem-template body into positional args at top-level ``|``.

    Brace-nested ``|`` (e.g. inside a nested ``{{val|5}}``) is skipped.
    Named params (``key=value``) are dropped — for chem templates these are
    styling/state flags that would add noise to embeddings.
    """
    parts: list[str] = []
    inner = 0
    last = 0
    for i, c in enumerate(body):
        if c == "{":
            inner += 1
        elif c == "}" and inner > 0:
            inner -= 1
        elif c == "|" and inner == 0:
            parts.append(body[last:i])
            last = i + 1
    parts.append(body[last:])
    return [p for p in parts if "=" not in p]


def _replace_chem_templates(text: str) -> str:
    """Replace chem-family templates with the concatenation of positional args."""
    out: list[str] = []
    pos = 0
    while True:
        m = _CHEM_TEMPLATE_OPEN_RE.search(text, pos)
        if not m:
            out.append(text[pos:])
            break
        out.append(text[pos : m.start()])
        if m.group(2) == "}":
            # ``{{name}}`` with no body — drop and resume after the ``}}``.
            close = m.end() - 1
            if close + 1 < len(text) and text[close + 1] == "}":
                pos = close + 2
                continue
            # Malformed (single trailing ``}``) — leave source untouched.
            out.append(text[m.start() : m.end()])
            pos = m.end()
            continue
        # group(2) == "|": content starts after the ``|``
        content_start = m.end()
        close = _find_template_close(text, content_start)
        if close is None:
            out.append(text[m.start() : m.end()])
            pos = m.end()
            continue
        body = text[content_start:close]
        out.append("".join(_chem_positional_args(body)))
        pos = close + 2
    return "".join(out)


def normalize_math(wikitext: str) -> str:
    """Rewrite math/chem constructs to plain text so ``strip_code()`` preserves them.

    Args:
        wikitext: Raw wikitext for an article (or fragment).

    Returns:
        Wikitext with math/chem template bodies and ``<math>`` / ``<chem>`` /
        ``<ce>`` tag bodies inlined as plain text. All other markup is left
        untouched for downstream stages.
    """
    text = replace_math_templates(wikitext)
    text = _MATH_TAG_RE.sub(lambda m: m.group(1).strip(), text)
    text = _replace_chem_templates(text)
    text = _CHEM_TAG_RE.sub(lambda m: m.group(2).strip(), text)
    return text
