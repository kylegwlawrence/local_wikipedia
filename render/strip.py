"""Wikicode noise removal: templates, refs, comments, category links.

These run after the wikicode-level template handlers in `templates.py` so any
template that produces output has already been replaced; remaining templates
are noise (navboxes, hatnotes, maintenance tags) we drop wholesale.
"""
import mwparserfromhell


def strip_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    for template in wikicode.filter_templates():
        try:
            wikicode.remove(template)
        except ValueError:
            pass


def strip_refs(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    for tag in wikicode.filter_tags():
        if tag.tag.lower() in ("ref", "references"):
            try:
                wikicode.remove(tag)
            except ValueError:
                pass


def strip_comments(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    for comment in wikicode.filter_comments():
        try:
            wikicode.remove(comment)
        except ValueError:
            pass


def strip_categories(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    for link in wikicode.filter_wikilinks():
        if str(link.title).startswith(("Category:", "File:", "Image:")):
            try:
                wikicode.remove(link)
            except ValueError:
                pass
