"""Extract <syntaxhighlight> and <math> blocks behind placeholders so other
string-level converters (lists, paragraphs, bold/italic) don't mangle their
contents. Placeholders are restored to final HTML at the end of the pipeline.

Both placeholders are block-shaped (``<div ...></div>``) when they need to
survive paragraph wrapping; inline math gets a ``<span>`` placeholder.
"""

import html
import re

_SYNTAX_RE = re.compile(
    r"<syntaxhighlight[^>]*>(.*?)</syntaxhighlight>",
    re.DOTALL | re.IGNORECASE,
)

_MATH_BLOCK_RE = re.compile(
    r'<math\b[^>]*\bdisplay\s*=\s*["\']block["\'][^>]*>(.*?)</math>',
    re.DOTALL | re.IGNORECASE,
)
_MATH_INLINE_RE = re.compile(
    r"<math(?:\s[^>]*)?>(.*?)</math>",
    re.DOTALL | re.IGNORECASE,
)


def extract_syntaxhighlight(text: str) -> tuple[str, dict[str, str]]:
    """Replace <syntaxhighlight> blocks with placeholders pointing at <pre><code>."""
    blocks: dict[str, str] = {}
    counter = 0

    def replace(m: re.Match) -> str:
        nonlocal counter
        escaped = html.escape(m.group(1).strip())
        placeholder = f'<div data-codeblock="{counter}"></div>'
        blocks[placeholder] = f"<pre><code>{escaped}</code></pre>"
        counter += 1
        return placeholder

    return _SYNTAX_RE.sub(replace, text), blocks


def restore_code_blocks(text: str, blocks: dict[str, str]) -> str:
    for placeholder, code_html in blocks.items():
        text = text.replace(placeholder, code_html)
    return text


def extract_math_tags(text: str) -> tuple[str, dict[str, str]]:
    """Replace <math> tags with placeholders pointing at KaTeX delimiters.

    Block math (display="block") yields a <div> placeholder; inline math yields
    a <span> placeholder.
    """
    blocks: dict[str, str] = {}

    def replace_block(m: re.Match) -> str:
        idx = len(blocks)
        placeholder = f'<div data-mathblock="{idx}"></div>'
        blocks[placeholder] = f'<div class="math-display">$$\n{m.group(1).strip()}\n$$</div>'
        return placeholder

    def replace_inline(m: re.Match) -> str:
        idx = len(blocks)
        placeholder = f'<span data-mathinline="{idx}"></span>'
        blocks[placeholder] = f"\\({m.group(1).strip()}\\)"
        return placeholder

    text = _MATH_BLOCK_RE.sub(replace_block, text)
    text = _MATH_INLINE_RE.sub(replace_inline, text)
    return text, blocks


def restore_math_tags(text: str, blocks: dict[str, str]) -> str:
    for placeholder, rendered in blocks.items():
        text = text.replace(placeholder, rendered)
    return text
