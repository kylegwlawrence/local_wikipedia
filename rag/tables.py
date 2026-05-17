"""Extract wikitables and infoboxes from wikitext for RAG chunking.

Produces serialized plain-text chunks suitable for embedding:
- Tables  → "Header: Value | Header: Value" rows, one chunk per table (split if large).
- Infoboxes → "Field: Value" lines per template parameter, one chunk per infobox.
"""

import re

import mwparserfromhell

from render.data import IMAGE_FIELD_PREFIXES, TAXONOMY_TEMPLATE_NAMES

MAX_TABLE_CHARS = 1600  # mirror chunker.MAX_CHUNK_CHARS

_TABLE_OPEN_RE = re.compile(r"^[:\s]*\{\|")

_WIKILINK_RE = re.compile(r'\[\[(?:[^|\]]+\|)?([^\]]+)\]\]')
_BOLD_ITALIC_RE = re.compile(r"'{2,5}(.*?)'{2,5}")
_HTML_TAG_RE = re.compile(r'<[^>]+>')
_REF_RE = re.compile(r'<ref[^>]*>.*?</ref>|<ref[^>]*/>', re.DOTALL | re.IGNORECASE)
_LIST_TEMPLATE_NAMES = frozenset({"plainlist", "flatlist"})
_LIST_CONTAINER_NAMES = frozenset({"collapsible list"}) | _LIST_TEMPLATE_NAMES


def _extract_list_items(parsed_val) -> str:
    """Extract plain-text items from {{Plainlist}} / {{Flatlist}} field values.

    Walks the parsed value for a plainlist or flatlist template and returns
    ``" / "``-joined item strings with wikitext markup stripped.  Returns ``""``
    when no such template is found.
    """
    for tpl in parsed_val.filter_templates():
        if str(tpl.name).strip().lower() not in _LIST_TEMPLATE_NAMES:
            continue
        for p in tpl.params:
            if str(p.name).strip() in ("style", "class", "indent"):
                continue
            items = []
            for line in str(p.value).split("\n"):
                item = line.strip().lstrip("*#").strip()
                if not item:
                    continue
                item = _REF_RE.sub("", item)
                item = _WIKILINK_RE.sub(r"\1", item)
                item = _BOLD_ITALIC_RE.sub(r"\1", item)
                item = _HTML_TAG_RE.sub("", item).strip()
                if item:
                    items.append(item)
            if items:
                return " / ".join(items)
    return ""
_TABLE_INNER_OPEN_RE = re.compile(r"^\s*\{\|")
_TABLE_INNER_CLOSE_RE = re.compile(r"^\s*\|\}")


def _strip_cell(raw: str) -> str:
    """Strip wikitext markup from a cell value to plain text."""
    try:
        return mwparserfromhell.parse(raw).strip_code().strip().replace("\n", " ")
    except (ValueError, AttributeError):
        return raw.strip().replace("\n", " ")


def _parse_cell_content(cell: str) -> str:
    """Strip leading attributes (``attrs | content``) then plain-text the remainder."""
    cell = cell.strip()
    pipe_idx = cell.find("|")
    if pipe_idx != -1 and "[" not in cell[:pipe_idx]:
        cell = cell[pipe_idx + 1 :].strip()
    return _strip_cell(cell)


def _split_rows_into_chunks(header_line: str, rows: list[str], max_chars: int) -> list[dict]:
    """Split serialized rows into chunks at row boundaries, repeating header_line per part."""
    chunks: list[dict] = []
    part_lines: list[str] = [header_line]
    chunk_index = 0

    for row in rows:
        candidate = "\n".join(part_lines + [row])
        if len(candidate) <= max_chars:
            part_lines.append(row)
        else:
            if len(part_lines) > 1:
                chunks.append({"chunk_index": chunk_index, "text": "\n".join(part_lines)})
                chunk_index += 1
            part_lines = [header_line, row]

    if len(part_lines) > 1:
        chunks.append({"chunk_index": chunk_index, "text": "\n".join(part_lines)})

    return chunks


def extract_tables(wikitext: str, section: str | None) -> tuple[list[dict], str]:
    """Extract ``{| … |}`` tables from wikitext and serialize them to plain-text chunks.

    Returns a two-tuple:
        - List of chunk dicts: ``{section, chunk_index, text, chunk_type='table'}``.
        - Cleaned wikitext with table lines replaced by empty lines so the caller
          can pass it to ``_strip_wikitext`` without re-finding the tables.

    Unclosed tables (no matching ``|}``) are silently skipped; their lines are
    returned in the cleaned wikitext unchanged.

    Args:
        wikitext: Raw wikitext fragment for one section.
        section: Section heading (or None for the lead) to attach to returned chunks.
    """
    lines = wikitext.split("\n")
    consumed: set[int] = set()  # line indices that belong to a complete table block
    all_chunks: list[dict] = []

    i = 0
    while i < len(lines):
        if not _TABLE_OPEN_RE.match(lines[i]):
            i += 1
            continue

        # Collect lines belonging to this table block.
        block_start = i
        block: list[str] = [lines[i].lstrip(":").lstrip()]
        j = i + 1
        depth = 1
        while j < len(lines) and depth > 0:
            if _TABLE_INNER_OPEN_RE.match(lines[j]):
                depth += 1
            elif _TABLE_INNER_CLOSE_RE.match(lines[j]):
                depth -= 1
            block.append(lines[j])
            j += 1

        if depth > 0:
            # Unclosed table — skip without consuming lines.
            i += 1
            continue

        # Mark all lines in this block as consumed.
        for k in range(block_start, j):
            consumed.add(k)
        i = j

        # Serialize the block.
        table_chunks = _serialize_table(block, section)
        all_chunks.extend(table_chunks)

    # Build cleaned wikitext: replace consumed lines with empty lines.
    cleaned_lines = ["" if idx in consumed else line for idx, line in enumerate(lines)]
    cleaned = "\n".join(cleaned_lines)

    return all_chunks, cleaned


def _serialize_table(table_lines: list[str], section: str | None) -> list[dict]:
    """Convert a parsed table block into one or more plain-text chunk dicts."""
    caption: str | None = None
    header_cells: list[str] = []
    body_rows: list[list[str]] = []
    current_row: list[str] = []
    nested_depth = 0
    in_header = False

    for idx, line in enumerate(table_lines):
        stripped = line.strip()

        if idx == 0:
            continue  # opening {| line

        if stripped.startswith("{|"):
            nested_depth += 1
            continue
        if stripped.startswith("|}"):
            if nested_depth > 0:
                nested_depth -= 1
            continue
        if nested_depth > 0:
            continue  # skip nested table contents entirely

        if stripped.startswith("|+"):
            caption = _strip_cell(stripped[2:].strip())
            continue

        if stripped.startswith("|-"):
            if current_row:
                if in_header:
                    header_cells = current_row
                else:
                    body_rows.append(current_row)
                current_row = []
            in_header = False
            continue

        if stripped.startswith("!"):
            cells = [_parse_cell_content(c) for c in re.split(r"!!", stripped[1:])]
            current_row.extend(cells)
            in_header = True
            continue

        if stripped.startswith("|"):
            cells = [_parse_cell_content(c) for c in re.split(r"\|\|", stripped[1:])]
            current_row.extend(cells)
            continue

        if stripped and current_row:
            # Continuation line — append to last cell.
            current_row[-1] = (current_row[-1] + " " + _strip_cell(stripped)).strip()

    # Flush final row.
    if current_row:
        if in_header or (not body_rows and not header_cells):
            header_cells = current_row
        else:
            body_rows.append(current_row)

    if not header_cells and not body_rows:
        return []

    # Promote first body row to header if no explicit header row found.
    if not header_cells and body_rows:
        header_cells = body_rows.pop(0)

    header_line = "Table: " + (caption if caption else "untitled")

    serialized_rows: list[str] = []
    for row in body_rows:
        pairs: list[str] = []
        for col_i, cell in enumerate(row):
            if col_i < len(header_cells):
                header = header_cells[col_i]
            else:
                header = f"col{col_i + 1}"
            if cell:
                pairs.append(f"{header}: {cell}")
        if pairs:
            serialized_rows.append(" | ".join(pairs))

    if not serialized_rows:
        return []

    raw_chunks = _split_rows_into_chunks(header_line, serialized_rows, MAX_TABLE_CHARS)
    return [
        {
            "section": section,
            "chunk_index": c["chunk_index"],
            "text": c["text"],
            "chunk_type": "table",
        }
        for c in raw_chunks
    ]


def extract_infoboxes(wikitext: str, article_title: str) -> list[dict]:
    """Extract ``{{Infobox …}}`` and taxonomy templates and serialize them to plain-text chunk dicts.

    Each infobox or taxonomy box becomes one or more chunks with ``chunk_type='infobox'``:

        Infobox: {article_title}[ — {kind}]   (for infoboxes)
        Taxon: {article_title}[ ({name})]      (for speciesbox / taxobox / automatic taxobox)
        Field: Value
        Field: Value
        …

    Image fields and empty values are skipped. Nested template values are
    flattened to plain text via ``mwparserfromhell.strip_code()``.

    Args:
        wikitext: Full article wikitext (infoboxes live in the lead section).
        article_title: Article title — used as the subject line in the chunk header.

    Returns:
        List of chunk dicts: ``{section=None, chunk_index, text, chunk_type='infobox'}``.
        Returns ``[]`` if no infobox or taxonomy templates are found.
    """
    try:
        parsed = mwparserfromhell.parse(wikitext)
    except (ValueError, AttributeError):
        return []

    all_chunks: list[dict] = []
    global_chunk_index = 0

    for tpl in parsed.filter_templates(recursive=False):
        name = str(tpl.name).strip()
        name_lower = name.lower()
        is_infobox = name_lower.startswith("infobox")
        is_taxobox = name_lower in TAXONOMY_TEMPLATE_NAMES
        if not is_infobox and not is_taxobox:
            continue

        if is_infobox:
            kind = name[len("infobox") :].strip(" _")
            header = f"Infobox: {article_title}"
            if kind:
                header = f"{header} — {kind}"
            skip_field = None
        else:
            common_name = str(tpl.get("name").value).strip() if tpl.has("name") else ""
            header = f"Taxon: {article_title}"
            if common_name:
                header = f"{header} ({common_name})"
            skip_field = "name"

        field_lines: list[str] = []
        for param in tpl.params:
            field = str(param.name).strip()
            raw_value = str(param.value).strip()

            if not raw_value:
                continue

            # Skip image fields by checking the lowercase field name prefix.
            field_lower = field.lower()
            if skip_field and field_lower == skip_field:
                continue
            if any(field_lower.startswith(prefix) for prefix in IMAGE_FIELD_PREFIXES):
                continue

            try:
                parsed_val = mwparserfromhell.parse(raw_value)
                value = parsed_val.strip_code().strip()
                if not value:
                    # Try extracting plain-text items from list-type templates
                    # ({{Plainlist}}, {{Flatlist}}, {{Collapsible list | {{Plainlist}} }})
                    # before falling back to the noisy positional-arg collector.
                    value = _extract_list_items(parsed_val)
                if not value:
                    # Generic fallback: collect positional args from nested templates
                    # (e.g. {{birth date|1980|1|1}} → "1980 1 1").
                    parts = []
                    for nested_tpl in parsed_val.filter_templates():
                        for nested_param in nested_tpl.params:
                            if str(nested_param.name).strip().isdigit():
                                v = str(nested_param.value).strip()
                                if v:
                                    parts.append(v)
                    value = " ".join(parts)
            except (ValueError, AttributeError):
                value = raw_value.strip()

            value = value.replace("\n", " / ").strip()
            if not value:
                continue

            field_lines.append(f"{field}: {value}")

        if not field_lines:
            continue

        # Split into chunks if the infobox is very large.
        raw_chunks = _split_rows_into_chunks(header, field_lines, MAX_TABLE_CHARS)
        for c in raw_chunks:
            all_chunks.append(
                {
                    "section": None,
                    "chunk_index": global_chunk_index,
                    "text": c["text"],
                    "chunk_type": "infobox",
                }
            )
            global_chunk_index += 1

    return all_chunks
