"""Extract article fields from a MediaWiki ``<page>`` XML element."""
import xml.etree.ElementTree as ET
from typing import Any

NS = {"mw": "http://www.mediawiki.org/xml/export-0.11/"}
PAGE_TAG = "{http://www.mediawiki.org/xml/export-0.11/}page"


def _get_text(elem: ET.Element, tag: str) -> str | None:
    child = elem.find(tag, NS)
    return child.text if child is not None else None


def parse_page_element(page_elem: ET.Element) -> dict[str, Any] | None:
    """Extract article data from a ``<page>`` element, or ``None`` to skip."""
    try:
        title = _get_text(page_elem, "mw:title")
        if not title:
            return None

        page_id = _get_text(page_elem, "mw:id")
        namespace = _get_text(page_elem, "mw:ns")
        if page_id is None or namespace is None:
            return None

        revision = page_elem.find("mw:revision", NS)
        if revision is None:
            return None

        revision_id = _get_text(revision, "mw:id")
        if not revision_id:
            return None

        parent_revision_id = _get_text(revision, "mw:parentid")
        timestamp = _get_text(revision, "mw:timestamp")
        comment = _get_text(revision, "mw:comment")

        contributor = revision.find("mw:contributor", NS)
        contributor_username = None
        contributor_id = None
        if contributor is not None:
            contributor_username = _get_text(contributor, "mw:username")
            contrib_id = _get_text(contributor, "mw:id")
            contributor_id = int(contrib_id) if contrib_id else None

        text_elem = revision.find("mw:text", NS)
        if text_elem is None:
            return None

        text_content = text_elem.text or ""
        text_bytes = text_elem.get("bytes")

        return {
            "page_id": int(page_id),
            "title": title,
            "namespace": int(namespace),
            "revision_id": int(revision_id),
            "parent_revision_id": int(parent_revision_id) if parent_revision_id else None,
            "timestamp": timestamp or "",
            "contributor_username": contributor_username,
            "contributor_id": contributor_id,
            "comment": comment,
            "text_bytes": int(text_bytes) if text_bytes else len(text_content),
            "text_content": text_content,
        }
    except (ValueError, AttributeError):
        return None
