"""Convert Wikipedia wikitext to clean, readable Markdown."""
import re
import mwparserfromhell


def convert_wikitext_to_markdown(
    wikitext: str,
    base_url: str = "https://en.wikipedia.org/wiki/",
) -> str:
    """Convert wikitext to clean Markdown.

    Args:
        wikitext: Raw Wikipedia wikitext content.
        base_url: Base URL for wikilinks (e.g. ``https://en.wikipedia.org/wiki/``).

    Returns:
        Formatted Markdown string.

    Example:
        >>> wikitext = "'''Bold''' and ''italic'' text"
        >>> markdown = convert_wikitext_to_markdown(wikitext)
        >>> print(markdown)
        **Bold** and *italic* text
    """
    if not wikitext or not wikitext.strip():
        return ""

    try:
        # Parse wikitext into structured object
        wikicode = mwparserfromhell.parse(wikitext)

        # Strip unwanted elements first
        _strip_templates(wikicode)
        _strip_refs(wikicode)
        _strip_comments(wikicode)
        _strip_categories(wikicode)

        # Convert to string for text-based transformations
        text = str(wikicode)

        # Apply conversions in order
        text = _convert_tables(text)
        text = _convert_lists(text)
        text = _convert_headings(text)
        text = _convert_bold_italic(text)
        text = _convert_links(text, base_url)
        text = _clean_extra_markup(text)

        return text.strip()

    except Exception as e:
        # Fallback to plain text if parsing fails
        return wikitext


def _convert_tables(text: str) -> str:
    """Convert wikitext {| ... |} tables to Markdown pipe tables."""
    lines = text.split('\n')
    out = []
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith('{|'):
            table_block = [lines[i]]
            i += 1
            depth = 1
            while i < len(lines) and depth > 0:
                cur = lines[i].lstrip()
                if cur.startswith('{|'):
                    depth += 1
                elif cur.startswith('|}'):
                    depth -= 1
                table_block.append(lines[i])
                i += 1
            if depth > 0:
                # Unclosed table — emit raw lines so no content is swallowed
                out.extend(table_block)
            else:
                out.append(_wikitext_table_to_markdown(table_block))
        else:
            out.append(lines[i])
            i += 1
    return '\n'.join(out)


def _wikitext_table_to_markdown(table_lines: list[str]) -> str:
    """Convert a collected {| ... |} block into a Markdown pipe table."""
    header_cells: list[str] | None = None
    rows: list[list[str]] = []
    current_row: list[str] = []
    nested_depth = 0

    for idx, line in enumerate(table_lines):
        stripped = line.strip()

        if idx == 0:
            continue  # {| opening line with table attributes

        if stripped.startswith('{|'):
            nested_depth += 1
            continue
        if stripped.startswith('|}'):
            if nested_depth > 0:
                nested_depth -= 1
            continue
        if nested_depth > 0:
            continue  # skip nested table content

        if stripped.startswith('|+'):
            continue  # caption

        if stripped.startswith('|-'):
            if current_row:
                rows.append(current_row)
                current_row = []
            continue

        if stripped.startswith('!'):
            cells = [_extract_cell_content(c) for c in re.split(r'!!', stripped[1:])]
            if header_cells is None:
                header_cells = cells
            else:
                header_cells.extend(cells)
            continue

        if stripped.startswith('|'):
            cells = [_extract_cell_content(c) for c in re.split(r'\|\|', stripped[1:])]
            current_row.extend(cells)
            continue

        if stripped and current_row:
            current_row[-1] = f"{current_row[-1]} {stripped}"

    if current_row:
        rows.append(current_row)

    if not rows and header_cells is None:
        return ''

    if header_cells is None:
        header_cells = rows.pop(0)

    col_count = max(len(header_cells), max((len(r) for r in rows), default=0))
    if col_count == 0:
        return ''

    def _pad(cells: list[str]) -> list[str]:
        return (cells + [''] * col_count)[:col_count]

    md_lines = [
        '| ' + ' | '.join(_pad(header_cells)) + ' |',
        '| ' + ' | '.join(['---'] * col_count) + ' |',
    ]
    for row in rows:
        md_lines.append('| ' + ' | '.join(_pad(row)) + ' |')

    return '\n'.join(md_lines)


def _extract_cell_content(cell: str) -> str:
    """Strip wikitext cell attributes, returning only display content.

    Handles patterns like ``style="..." | actual content`` where everything
    before the first bare ``|`` (one not inside ``[[...]]``) is attributes.
    """
    cell = cell.strip()
    pipe_idx = cell.find('|')
    if pipe_idx != -1 and '[' not in cell[:pipe_idx]:
        return cell[pipe_idx + 1:].strip()
    return cell


def _convert_bold_italic(text: str) -> str:
    """Convert '''bold''' and ''italic'' to Markdown.

    Args:
        text: Text with wikitext formatting.

    Returns:
        Text with Markdown formatting.
    """
    # Convert bold+italic (must come first)
    text = re.sub(r"'''''(.+?)'''''", r"***\1***", text)

    # Convert bold
    text = re.sub(r"'''(.+?)'''", r"**\1**", text)

    # Convert italic
    text = re.sub(r"''(.+?)''", r"*\1*", text)

    return text


def _convert_headings(text: str) -> str:
    """Convert == Heading == to ## Heading.

    Args:
        text: Text with wikitext headings.

    Returns:
        Text with Markdown headings.
    """
    # Handle heading levels 2-6 (MediaWiki uses 2+ equals, Markdown uses 2-6 hashes)
    for level in range(6, 1, -1):  # Start from highest level to avoid partial matches
        equals = "=" * level
        hashes = "#" * level
        # Match: ===Title=== with optional whitespace
        pattern = rf"^{re.escape(equals)}\s*(.+?)\s*{re.escape(equals)}\s*$"
        replacement = rf"{hashes} \1"
        text = re.sub(pattern, replacement, text, flags=re.MULTILINE)

    return text


def _convert_links(text: str, base_url: str = "https://en.wikipedia.org/wiki/") -> str:
    """Convert [[Page]] and [[Page|Label]] to Markdown links.

    Args:
        text: Text with wikitext links.
        base_url: Base URL prepended to each page slug.

    Returns:
        Text with Markdown links.
    """
    text = re.sub(
        r"\[\[([^\]|]+)\|([^\]]+)\]\]",
        lambda m: f"[{m.group(2)}]({base_url}{m.group(1).replace(' ', '_')})",
        text
    )

    text = re.sub(
        r"\[\[([^\]|]+)\]\]",
        lambda m: f"[{m.group(1)}]({base_url}{m.group(1).replace(' ', '_')})",
        text
    )

    return text


def _convert_lists(text: str) -> str:
    """Convert wikitext lists to Markdown format.

    Handles all four wikitext list types and arbitrary nesting/mixing:
      *  → unordered (- item)
      #  → ordered (1. item)
      ;  → definition term (**term**)
      :  → definition description / indentation (> text)

    The last prefix character determines type; prefix length determines depth.
    Mixed prefixes like #* or *# are supported naturally.
    """
    lines = text.split("\n")
    converted = []
    prev_was_list = False
    for line in lines:
        m = re.match(r'^([*#;:]+)(.*)', line)
        if not m:
            converted.append(line)
            prev_was_list = False
            continue
        # Insert a blank line when starting a new list directly after a
        # non-blank, non-list line. Python-Markdown won't recognize a list
        # otherwise — it folds the items into the preceding paragraph.
        if not prev_was_list and converted and converted[-1].strip():
            converted.append("")
        prefix = m.group(1)
        content = m.group(2).lstrip()
        level = len(prefix)
        last = prefix[-1]
        indent = "  " * (level - 1)
        if last == '*':
            converted.append(f"{indent}- {content}")
        elif last == '#':
            converted.append(f"{indent}1. {content}")
        elif last == ';':
            converted.append(f"**{content}**")
        elif last == ':':
            converted.append(f"{'>' * level} {content}")
        prev_was_list = True
    return "\n".join(converted)


def _strip_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Remove or simplify {{template}} syntax.

    Args:
        wikicode: Parsed wikicode object (modified in-place).
    """
    # Remove all templates
    for template in wikicode.filter_templates():
        try:
            wikicode.remove(template)
        except ValueError:
            pass  # Template already removed


def _strip_refs(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Remove or convert <ref> tags to footnotes.

    Args:
        wikicode: Parsed wikicode object (modified in-place).
    """
    # Remove all reference tags
    for tag in wikicode.filter_tags():
        if tag.tag.lower() in ("ref", "references"):
            try:
                wikicode.remove(tag)
            except ValueError:
                pass


def _strip_comments(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Remove HTML comments.

    Args:
        wikicode: Parsed wikicode object (modified in-place).
    """
    for comment in wikicode.filter_comments():
        try:
            wikicode.remove(comment)
        except ValueError:
            pass


def _strip_categories(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Remove category links.

    Args:
        wikicode: Parsed wikicode object (modified in-place).
    """
    for link in wikicode.filter_wikilinks():
        if str(link.title).startswith(("Category:", "File:", "Image:")):
            try:
                wikicode.remove(link)
            except ValueError:
                pass


def _clean_extra_markup(text: str) -> str:
    """Clean up remaining HTML, extra whitespace, etc.

    Args:
        text: Text with potential extra markup.

    Returns:
        Cleaned text.
    """
    # Remove remaining HTML tags
    text = re.sub(r"<[^>]+>", "", text)

    # Remove multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Remove trailing whitespace from lines
    text = "\n".join(line.rstrip() for line in text.split("\n"))

    return text
