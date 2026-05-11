"""Inline text-level wikitext converters: bold/italic and wikilinks."""
import html
import re
from urllib.parse import quote

from db import normalize_title


def convert_bold_italic(text: str) -> str:
    """Convert '''bold''' and ''italic'' to HTML."""
    # Bold+italic must come before bold and italic individually.
    text = re.sub(r"'''''(.+?)'''''", r"<strong><em>\1</em></strong>", text)
    text = re.sub(r"'''(.+?)'''", r"<strong>\1</strong>", text)
    text = re.sub(r"''(.+?)''", r"<em>\1</em>", text)
    return text


def _render_link(target: str, label: str, escape_label: bool) -> str:
    title, _, anchor = target.partition("#")
    title = title.strip()
    if title:
        title = normalize_title(title)
    href = f"/article/{quote(title)}"
    hx_url = href
    if anchor:
        href = f"{href}#{quote(anchor)}"
    href_attr = html.escape(href, quote=True)
    hx_attr = html.escape(hx_url, quote=True)
    rendered_label = html.escape(label) if escape_label else label
    return (
        f'<a href="{href_attr}" '
        f'hx-get="{hx_attr}" '
        f'hx-target="#article" '
        f'hx-swap="innerHTML">{rendered_label}</a>'
    )


def convert_links(text: str) -> str:
    """Convert [[Page]] and [[Page|Label]] to local /article/{title} links."""
    # [[Page|Label]] — labels may contain inline HTML (e.g. <code>); don't escape.
    text = re.sub(
        r"\[\[([^\]|]+)\|([^\]]+)\]\]",
        lambda m: _render_link(m.group(1), m.group(2), escape_label=False),
        text,
    )
    # [[Page]] — page name doubles as visible label; plain text.
    text = re.sub(
        r"\[\[([^\]|]+)\]\]",
        lambda m: _render_link(m.group(1), m.group(1), escape_label=True),
        text,
    )
    return text
