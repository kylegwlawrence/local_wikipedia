"""Wikicode-level template handlers.

These run before any string-level conversion so the structured representation
of templates is preserved. Each ``convert_*_templates`` function walks the
wikicode and replaces matching templates with HTML or wikilink strings.

Anything not handled here is removed wholesale by ``strip.strip_templates``
in the next pipeline stage.
"""
import html
import re

import mwparserfromhell

from render.data import (
    CITE_TEMPLATE_PREFIXES,
    IMAGE_FIELD_PREFIXES,
    IMAGE_VALUE_RE,
    INDICATORS,
    MATH_TEMPLATE_NAMES,
    MONTH_NAMES,
    lang_code_to_name,
)
from render.inline import convert_bold_italic, convert_links


# Matches <ref name="X">content</ref>, with name in any of three quoting styles.
_REF_TAG_RE = re.compile(
    r'<ref\s+name\s*=\s*(?:"([^"]+)"|\'([^\']+)\'|([^>\s]+))\s*>(.*?)</ref>',
    re.DOTALL | re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _render_lang(code: str, text: str) -> str:
    """Format a {{lang}} / {{langx}} pair as 'Language: <em>text</em>'."""
    return f'{lang_code_to_name(code)}: <em>{text}</em>'


def _render_ref_body(contents: str) -> str:
    """Render the inside of a <ref>...</ref>: cite templates → formatted strings,
    fallback to escaped plaintext (with bare {{...}} stripped).
    """
    sub = mwparserfromhell.parse(contents)
    cite_parts = []
    for tmpl in sub.filter_templates():
        name = str(tmpl.name).strip().lower()
        if any(name.startswith(p) for p in CITE_TEMPLATE_PREFIXES):
            formatted = format_cite_template(tmpl)
            if formatted:
                cite_parts.append(formatted)
    if cite_parts:
        return ' '.join(cite_parts)
    return html.escape(re.sub(r'\{\{[^}]*\}\}', '', contents).strip())


# ---------------------------------------------------------------------------
# Citation template formatting
# ---------------------------------------------------------------------------


def format_cite_template(template) -> str:
    """Render a {{cite ...}} / {{citation}} template as a flat HTML string."""
    fields: dict[str, str] = {}
    for param in template.params:
        key = str(param.name).strip().lower()
        val = str(param.value).strip()
        if val:
            fields[key] = val

    parts: list[str] = []

    # Author handling: 'author' wins; else last/first or last1/first1, last2/...
    author = fields.get('author')
    if not author:
        if 'last' in fields:
            author = (fields.get('last', '') + ', ' + fields.get('first', '')).strip(', ')
        else:
            authors: list[str] = []
            for i in range(1, 10):
                last = fields.get(f'last{i}')
                first = fields.get(f'first{i}')
                if last:
                    authors.append((last + ', ' + first).strip(', ') if first else last)
                elif not authors:
                    break
            author = '; '.join(authors) if authors else ''
    if author:
        parts.append(html.escape(author))

    title = fields.get('title', '')
    url = fields.get('url', '')
    if title and url:
        parts.append(
            f'<a href="{html.escape(url, quote=True)}" rel="noopener noreferrer" '
            f'target="_blank">{html.escape(title)}</a>'
        )
    elif title:
        parts.append(f'<em>{html.escape(title)}</em>')
    elif url:
        parts.append(
            f'<a href="{html.escape(url, quote=True)}" rel="noopener noreferrer" '
            f'target="_blank">{html.escape(url)}</a>'
        )

    for field in ('work', 'website', 'journal', 'newspaper', 'magazine', 'publisher'):
        if field in fields:
            parts.append(html.escape(fields[field]))
            break

    if 'date' in fields:
        parts.append(html.escape(fields['date']))

    return '. '.join(parts)


def convert_citation_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Replace top-level {{cite ...}} templates in body text with formatted HTML.

    ``recursive=False`` keeps cites nested inside <ref> tags untouched — those
    are handled by ``collect_inline_refs``.
    """
    for template in wikicode.filter_templates(recursive=False):
        name = str(template.name).strip().lower()
        if any(name.startswith(p) for p in CITE_TEMPLATE_PREFIXES):
            formatted = format_cite_template(template)
            if formatted:
                try:
                    wikicode.replace(template, formatted)
                except ValueError:
                    pass


# ---------------------------------------------------------------------------
# Reference / Reflist
# ---------------------------------------------------------------------------


def collect_inline_refs(
    wikicode: mwparserfromhell.wikicode.Wikicode,
) -> list[tuple[str | None, str]]:
    """Collect inline <ref> tags from the article body, in citation order.

    Self-closing back-refs (<ref name="X"/>) are resolved to their content if
    they were defined inside a {{Reflist|refs=...}} parameter; otherwise they
    are skipped (the inline definition is collected at its definition site).

    Returns a list of (name_or_None, rendered_html). No deduplication.
    """
    # Build a name → content lookup for refs defined only in refs= parameters.
    refs_param_content: dict[str, str] = {}
    for tmpl in wikicode.filter_templates():
        if str(tmpl.name).strip().lower() != 'reflist':
            continue
        rp = next((p for p in tmpl.params if str(p.name).strip() == 'refs'), None)
        if not rp:
            continue
        for m in _REF_TAG_RE.finditer(str(rp.value)):
            name = m.group(1) or m.group(2) or m.group(3)
            content = m.group(4).strip()
            if name not in refs_param_content:
                refs_param_content[name] = content

    collected: list[tuple[str | None, str]] = []

    for tag in wikicode.filter_tags(recursive=False):
        if str(tag.tag).strip().lower() != 'ref':
            continue

        name = str(tag.get('name').value).strip() if tag.has('name') else None

        if tag.self_closing:
            # Resolve only if content was defined in a refs= param. Inline
            # definitions are already collected when their full tag is hit.
            if name and name in refs_param_content:
                contents = refs_param_content[name]
            else:
                continue
        else:
            contents = str(tag.contents).strip()
            if not contents:
                continue

        rendered = _render_ref_body(contents)
        if rendered:
            collected.append((name, rendered))

    return collected


def convert_reflist_template(
    wikicode: mwparserfromhell.wikicode.Wikicode,
    collected_refs: list[tuple[str | None, str]] | None = None,
) -> None:
    """Convert {{Reflist}} templates to <ol class="references">.

    Two modes:
      - ``refs=`` parameter present: render each ref defined there, prepended by
        any inline refs collected from the body.
      - bare {{Reflist}}: render only the inline refs.

    Non-Reflist templates are left for ``strip.strip_templates`` to remove.
    """
    for template in wikicode.filter_templates():
        if str(template.name).strip().lower() != 'reflist':
            continue

        refs_param = next(
            (p for p in template.params if str(p.name).strip() == 'refs'),
            None,
        )

        items: list[str] = []

        # Inline refs first, in body citation order.
        if collected_refs:
            for idx, (name, rendered) in enumerate(collected_refs, start=1):
                ref_id = html.escape(name, quote=True) if name else str(idx)
                items.append(f'<li id="ref_{ref_id}">{rendered}</li>')

        # Then refs defined in the refs= parameter.
        if refs_param:
            for m in _REF_TAG_RE.finditer(str(refs_param.value)):
                ref_name = m.group(1) or m.group(2) or m.group(3)
                ref_content = m.group(4).strip()
                try:
                    rendered = _render_ref_body(ref_content)
                except Exception:
                    rendered = html.escape(ref_content)
                if rendered:
                    items.append(
                        f'<li id="ref_{html.escape(ref_name, quote=True)}">{rendered}</li>'
                    )

        replacement = (
            '<ol class="references">\n' + '\n'.join(items) + '\n</ol>'
            if items else ''
        )
        try:
            wikicode.replace(template, replacement)
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Math / code / lang / indicator / section-link
# ---------------------------------------------------------------------------


def convert_math_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Replace {{math|...}} / {{mvar|...}} with <math> tags so the math
    extractor sees them as raw LaTeX content.
    """
    for template in wikicode.filter_templates():
        if str(template.name).strip().lower() not in MATH_TEMPLATE_NAMES:
            continue
        # str(param) (not param.value) so a literal '=' inside the math expression
        # isn't lost — mwparserfromhell parses {{math|a = b}} as a named param.
        content = str(template.params[0]) if template.params else ""
        try:
            wikicode.replace(template, f"<math>{content}</math>")
        except ValueError:
            pass


def convert_code_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Replace {{code|...}}, {{tt|...}}, etc. with <code>...</code>.

    Handles both ``{{code|x = 1}}`` (positional) and ``{{code|lang=python|x = 1}}``
    (named lang parameter alongside positional content).
    """
    for template in wikicode.filter_templates():
        name = str(template.name).strip().lower()
        if name not in ('code', 'codes', 'codett', 'c', 'mono', 'tt', 'kbd'):
            continue
        params = list(template.params)
        if not params:
            continue

        # Walk params in reverse — the code content is typically the last
        # positional param. Anything that *looks* like ``key=value`` (alpha
        # key) is treated as metadata and skipped.
        code_content: str | None = None
        for param in reversed(params):
            param_str = str(param).strip()
            if '=' not in param_str or not param_str.split('=')[0].strip().isalpha():
                code_content = (
                    param_str.split('=', 1)[1].strip()
                    if '=' in param_str else param_str
                )
                break

        if code_content:
            try:
                wikicode.replace(template, f'<code>{html.escape(code_content)}</code>')
            except ValueError:
                pass


def convert_lang_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Replace {{lang|XX|text}} / {{langx|XX|...|text}} with rendered HTML."""
    for template in wikicode.filter_templates():
        name = str(template.name).strip().lower()
        if name not in ('lang', 'langx'):
            continue
        positional = [
            str(p.value).strip()
            for p in template.params
            if str(p.name).strip().isdigit()
        ]
        # First positional is the language code; last is the text.
        replacement = (
            _render_lang(positional[0], positional[-1])
            if len(positional) >= 2 else None
        )
        try:
            if replacement:
                wikicode.replace(template, replacement)
            else:
                wikicode.remove(template)
        except ValueError:
            pass


def convert_wikidata_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Strip {{wikidata|...}} templates.

    These fetch live property values from Wikidata via API; no network access is
    available here, so we remove them rather than render stale or empty output.
    """
    for template in wikicode.filter_templates():
        if str(template.name).strip().lower() == 'wikidata':
            try:
                wikicode.remove(template)
            except ValueError:
                pass


def convert_indicator_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Replace status templates ({{yes}}, {{no}}, {{partial}}, ...) with
    a <span> carrying a CSS class for table styling.
    """
    for template in wikicode.filter_templates():
        name = str(template.name).strip().lower()
        if name in INDICATORS:
            text, css_class = INDICATORS[name]
            try:
                wikicode.replace(template, f'<span class="{css_class}">{text}</span>')
            except ValueError:
                pass


def convert_section_link_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Replace {{Section link|Page#Section}} with the equivalent [[wikilink]].

    Output is wikitext — the link converter picks it up later in the pipeline.
    """
    for template in wikicode.filter_templates():
        if str(template.name).strip().lower() != 'section link':
            continue
        params = list(template.params)
        if not params:
            continue
        target = str(params[0]).strip()
        label = str(params[1]).strip() if len(params) > 1 else None
        replacement = f'[[{target}|{label}]]' if label else f'[[{target}]]'
        try:
            wikicode.replace(template, replacement)
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Infobox
# ---------------------------------------------------------------------------


def _is_image_field(field_name: str) -> bool:
    name = field_name.lower().strip().replace('-', '_')
    for prefix in IMAGE_FIELD_PREFIXES:
        if name == prefix or name.startswith(prefix + '_') or name.endswith('_' + prefix):
            return True
    return 'caption' in name or name.startswith('alt_') or name.endswith('_alt')


def _render_infobox_value_template(template) -> str | None:
    """Render templates that commonly appear inside infobox cell values.

    Returns the rendered string, or ``None`` to remove the template entirely.
    """
    name = str(template.name).strip().lower()
    params = list(template.params)

    # Date templates: positional 1=year, 2=month, 3=day
    if name in (
        'birth date', 'birth date and age', 'birth-date and age',
        'death date', 'death date and age', 'death-date and age',
        'start date', 'start date and age', 'end date', 'end date and age',
    ):
        indexed: dict[int, str] = {}
        for p in params:
            pname = str(p.name).strip()
            if pname.isdigit():
                indexed[int(pname)] = str(p.value).strip()
        year = indexed.get(1, '')
        month_raw = indexed.get(2, '')
        day = indexed.get(3, '')
        try:
            month_name = MONTH_NAMES[int(month_raw)] if month_raw else ''
        except (ValueError, IndexError):
            month_name = month_raw
        if year and month_name and day:
            return f'{month_name} {day}, {year}'
        if year and month_name:
            return f'{month_name} {year}'
        return year or None

    # flatlist / plainlist: first positional param holds wiki-list lines
    if name in ('flatlist', 'plainlist'):
        for p in params:
            pname = str(p.name).strip()
            if pname in ('class', 'style', 'indent'):
                continue
            items: list[str] = []
            for line in str(p.value).split('\n'):
                item = line.strip().lstrip('*#').strip()
                if item:
                    item = _render_infobox_value(item)
                if item:
                    items.append(item)
            if items:
                lis = ''.join(f'<li>{item}</li>' for item in items)
                return f'<ul class="infobox-list">{lis}</ul>'
            break

    # ubl / unbulleted list / bulleted list: positional params are items
    if name in ('unbulleted list', 'ubl', 'bulleted list'):
        items = []
        for p in params:
            pname = str(p.name).strip()
            if pname.isdigit():
                item = _render_infobox_value(str(p.value).strip())
                if item:
                    items.append(item)
        if items:
            lis = ''.join(f'<li>{item}</li>' for item in items)
            return f'<ul class="infobox-list">{lis}</ul>'

    # hlist: positional params rendered as "a · b · c"
    if name == 'hlist':
        items = []
        for p in params:
            pname = str(p.name).strip()
            if pname in ('class', 'style', 'ul_style', 'li_style', 'indent', 'item_style'):
                continue
            v = str(p.value).strip()
            if v:
                items.append(v)
        return ' · '.join(items) if items else None

    # Language annotation
    if name in ('lang', 'langx', 'lang-xx'):
        positional = [str(p.value).strip() for p in params if str(p.name).strip().isdigit()]
        if len(positional) >= 2:
            return _render_lang(positional[0], positional[-1])
        return None

    # Pass-through wrappers: render the (last) positional param
    if name in ('nowrap', 'abbr', 'msd', 'nowr'):
        for p in params:
            pname = str(p.name).strip()
            if not pname.isdigit():
                continue
            val = str(p.value).strip()
            if val:
                return val
        if params:
            return str(params[-1].value).strip() or None

    # {{URL|url|label}}
    if name == 'url':
        if params:
            url = str(params[0].value).strip()
            label = str(params[1].value).strip() if len(params) > 1 else url
            return (
                f'<a href="{html.escape(url, quote=True)}" rel="noopener noreferrer" '
                f'target="_blank">{html.escape(label)}</a>'
            )

    # {{wikidata|...}} fetches live property values from Wikidata; we have no
    # API access, so skip the row rather than show a stale or empty value.
    if name == 'wikidata':
        return None

    return None  # Strip unknown templates entirely.


def _render_infobox_value(raw_value: str) -> str:
    """Process an infobox field value into HTML, handling nested templates and tags."""
    wikicode = mwparserfromhell.parse(raw_value)

    for template in wikicode.filter_templates():
        rendered = _render_infobox_value_template(template)
        try:
            if rendered is not None:
                wikicode.replace(template, rendered)
            else:
                wikicode.remove(template)
        except ValueError:
            pass

    # Drop refs and unwrap inline-formatting tags (keep their contents).
    for tag in wikicode.filter_tags():
        tag_name = str(tag.tag).strip().lower()
        try:
            if tag_name in ('ref', 'references'):
                wikicode.remove(tag)
            elif tag_name in ('small', 'sup', 'sub', 'span', 'div'):
                wikicode.replace(tag, str(tag.contents))
        except ValueError:
            pass

    text = str(wikicode).strip()

    # Drop bare [[File:...]] / [[Image:...]] (e.g. inside captions).
    text = re.sub(r'\[\[(File|Image):[^\]]*\]\]', '', text, flags=re.IGNORECASE)

    text = convert_bold_italic(text)
    text = convert_links(text)
    return text.strip()


def convert_infobox_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Replace {{Infobox ...}} templates with HTML <table class="infobox">."""
    for template in wikicode.filter_templates():
        name = str(template.name).strip()
        if not name.lower().startswith('infobox'):
            continue

        display_type = name[len('infobox'):].strip()
        if display_type:
            display_type = display_type[0].upper() + display_type[1:]

        rows: list[tuple[str, str]] = []
        for param in template.params:
            field_name = str(param.name).strip()
            raw_value = str(param.value).strip()

            if not raw_value or raw_value.startswith('<!--'):
                continue
            if _is_image_field(field_name):
                continue
            if IMAGE_VALUE_RE.match(raw_value):
                continue

            label = field_name.replace('_', ' ').strip()
            if label:
                label = label[0].upper() + label[1:]

            rendered = _render_infobox_value(raw_value)
            if not rendered or not rendered.strip():
                continue

            rows.append((html.escape(label), rendered))

        if not rows and not display_type:
            try:
                wikicode.remove(template)
            except ValueError:
                pass
            continue

        parts = ['<table class="infobox">']
        if display_type:
            parts.append(f'<caption>{html.escape(display_type)}</caption>')
        parts.append('<tbody>')
        for label, value in rows:
            parts.append(f'<tr><th>{label}</th><td>{value}</td></tr>')
        parts.append('</tbody>')
        parts.append('</table>')

        try:
            wikicode.replace(template, '\n'.join(parts))
        except ValueError:
            pass
