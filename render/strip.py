"""Wikitext noise removal: templates, refs, comments, category links.

All functions operate on a plain string (post-flatten) via regex rather than
on the mwparserfromhell wikicode tree, which makes individual `.remove()` calls
O(n) each — catastrophically slow for large articles with hundreds of templates.
"""
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


def strip_external_links_section(text: str) -> str:
    """Remove the '== External links ==' section and all its content."""
    return re.sub(
        r"\n*==\s*External links\s*==.*?(?=\n==|\Z)",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
