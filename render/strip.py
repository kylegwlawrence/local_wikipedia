"""Wikitext noise removal: templates, refs, comments, category links.

All functions operate on a plain string (post-flatten) via regex rather than
on the mwparserfromhell wikicode tree, which makes individual `.remove()` calls
O(n) each — catastrophically slow for large articles with hundreds of templates.
"""
import html as _html
import re


def strip_templates(text: str) -> str:
    """Remove remaining {{template}} markup by iteratively peeling innermost templates."""
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r'\{\{[^{}]*\}\}', '', text)
    return text


def strip_refs(text: str) -> str:
    """Remove <ref> and <references> tags."""
    text = re.sub(r'<ref\b[^>]*/>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<ref\b[^>]*>.*?</ref>', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<references\b[^>]*/>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<references\b[^>]*>.*?</references>', '', text, flags=re.IGNORECASE | re.DOTALL)
    return text


def strip_comments(text: str) -> str:
    """Remove HTML comments."""
    return re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)


def strip_categories(text: str) -> str:
    """Remove [[Category:...]], [[File:...]], [[Image:...]], [[Media:...]] wikilinks."""
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r'\[\[(?:Category|File|Image|Media):[^\[\]]*\]\]', '', text, flags=re.IGNORECASE)
    return text


def strip_nowiki(text: str) -> str:
    """Replace <nowiki>content</nowiki> with escaped plain text.

    The tag is used to display wiki markup literally. HTML-escaping plus
    bracket escaping prevents the content from being re-interpreted as
    wikilinks or templates by later pipeline stages.
    """
    def _escape(m: re.Match) -> str:
        content = _html.escape(m.group(1))
        content = content.replace('[[', '&#91;&#91;').replace(']]', '&#93;&#93;')
        return content

    return re.sub(r'<nowiki>(.*?)</nowiki>', _escape, text, flags=re.IGNORECASE | re.DOTALL)


def convert_gallery(text: str) -> str:
    """Convert <gallery> blocks to a <ul> of pipe-separated captions.

    File lines without a caption are dropped; the caption (the part after |)
    is kept so image descriptions remain visible.
    """
    def _render(m: re.Match) -> str:
        items = []
        for line in m.group(1).split('\n'):
            line = line.strip()
            if not line or not re.match(r'(?:File|Image):', line, re.IGNORECASE):
                continue
            if '|' in line:
                caption = line.split('|', 1)[1].strip()
                if caption:
                    items.append(f'<li>{caption}</li>')
        return (
            '<ul class="gallery-captions">\n' + '\n'.join(items) + '\n</ul>'
            if items else ''
        )

    return re.sub(
        r'<gallery\b[^>]*>(.*?)</gallery>',
        _render,
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )


def strip_external_links_section(text: str) -> str:
    """Remove the '== External links ==' section and all its content."""
    return re.sub(
        r"\n*==\s*External links\s*==.*?(?=\n==|\Z)",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
