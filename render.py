"""Convert Wikipedia wikitext to clean, readable HTML."""
import re
import mwparserfromhell


def convert_wikitext_to_html(wikitext: str) -> str:
    """Convert wikitext to clean HTML.

    Wikilinks are rewritten to point at this app's ``/article/{title}``
    endpoint and decorated with HTMX attributes so the front-end loads them
    as fragments rather than full-page navigations.

    Args:
        wikitext: Raw Wikipedia wikitext content.

    Returns:
        Formatted HTML string.

    Example:
        >>> wikitext = "'''Bold''' and ''italic'' text"
        >>> html = convert_wikitext_to_html(wikitext)
        >>> print(html)
        <p><strong>Bold</strong> and <em>italic</em> text</p>
    """
    if not wikitext or not wikitext.strip():
        return ""

    try:
        # Parse wikitext into structured object
        wikicode = mwparserfromhell.parse(wikitext)

        # Convert code templates before stripping all templates
        _convert_code_templates(wikicode)
        # Strip unwanted elements
        _strip_templates(wikicode)
        _strip_refs(wikicode)
        _strip_comments(wikicode)
        _strip_categories(wikicode)

        # Convert to string for text-based transformations
        text = str(wikicode)

        # Extract and protect code blocks from further processing
        text, code_blocks = _extract_syntaxhighlight(text)

        # Apply conversions in order
        # Block-level elements first (tables, lists, headings)
        text = _convert_tables(text)
        text = _convert_lists(text)
        text = _convert_headings(text)
        # Then inline elements (bold, italic, links) - but tables handle their own
        text = _convert_bold_italic(text)
        text = _convert_links(text)
        # Wrap paragraphs
        text = _wrap_paragraphs(text)
        # Restore code blocks
        text = _restore_code_blocks(text, code_blocks)
        # Clean up
        text = _clean_extra_markup(text)

        return text.strip()

    except Exception as e:
        # Fallback to plain text if parsing fails
        import html
        return f"<p>{html.escape(wikitext)}</p>"


_TABLE_OPEN_RE = re.compile(r'^[:\s]*\{\|')
_TABLE_INNER_OPEN_RE = re.compile(r'^\s*\{\|')
_TABLE_INNER_CLOSE_RE = re.compile(r'^\s*\|\}')


def _convert_tables(text: str) -> str:
    """Convert wikitext {| ... |} tables to HTML tables.

    Handles tables whose opening line is prefixed with a colon (``:{|``),
    which MediaWiki renders as an indented table.
    """
    lines = text.split('\n')
    out = []
    i = 0
    while i < len(lines):
        if _TABLE_OPEN_RE.match(lines[i]):
            # Normalise the opening line: strip any leading colon/whitespace so
            # _wikitext_table_to_html receives a plain ``{|`` line.
            table_block = [lines[i].lstrip(':').lstrip()]
            i += 1
            depth = 1
            while i < len(lines) and depth > 0:
                cur = lines[i].lstrip()
                if _TABLE_INNER_OPEN_RE.match(lines[i]):
                    depth += 1
                elif _TABLE_INNER_CLOSE_RE.match(lines[i]):
                    depth -= 1
                table_block.append(lines[i])
                i += 1
            if depth > 0:
                # Unclosed table — emit raw lines so no content is swallowed
                out.extend(table_block)
            else:
                out.append(_wikitext_table_to_html(table_block))
        else:
            out.append(lines[i])
            i += 1
    return '\n'.join(out)


def _wikitext_table_to_html(table_lines: list[str]) -> str:
    """Convert a collected {| ... |} block into an HTML table."""
    import html

    # Parse table attributes from opening line
    table_attrs = _parse_table_attributes(table_lines[0] if table_lines else '')
    table_class = table_attrs.get('class', '')

    caption = None
    header_rows: list[list[dict]] = []
    body_rows: list[list[dict]] = []
    current_row: list[dict] = []
    nested_depth = 0
    in_header = False

    for idx, line in enumerate(table_lines):
        stripped = line.strip()

        if idx == 0:
            continue  # {| opening line

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
            caption = html.escape(stripped[2:].strip())
            continue

        if stripped.startswith('|-'):
            if current_row:
                if in_header:
                    header_rows.append(current_row)
                else:
                    body_rows.append(current_row)
                current_row = []
            in_header = False
            continue

        if stripped.startswith('!'):
            cells = [_parse_cell(c, is_header=True) for c in re.split(r'!!', stripped[1:])]
            current_row.extend(cells)
            in_header = True
            continue

        if stripped.startswith('|'):
            cells = [_parse_cell(c, is_header=False) for c in re.split(r'\|\|', stripped[1:])]
            current_row.extend(cells)
            continue

        if stripped and current_row:
            # Continuation of previous cell
            current_row[-1]['content'] += ' ' + html.escape(stripped)

    if current_row:
        if in_header or (not body_rows and not header_rows):
            header_rows.append(current_row)
        else:
            body_rows.append(current_row)

    if not header_rows and not body_rows:
        return ''

    # If no explicit header rows, treat first row as header
    if not header_rows and body_rows:
        header_rows.append(body_rows.pop(0))

    # Build HTML
    html_parts = [f'<table class="{table_class}">']

    if caption:
        html_parts.append(f'<caption>{caption}</caption>')

    if header_rows:
        html_parts.append('<thead>')
        for row in header_rows:
            html_parts.append('<tr>')
            for cell in row:
                html_parts.append(_render_cell(cell, tag='th'))
            html_parts.append('</tr>')
        html_parts.append('</thead>')

    if body_rows:
        html_parts.append('<tbody>')
        for row in body_rows:
            html_parts.append('<tr>')
            for cell in row:
                html_parts.append(_render_cell(cell, tag='td'))
            html_parts.append('</tr>')
        html_parts.append('</tbody>')

    html_parts.append('</table>')

    return '\n'.join(html_parts)


def _parse_table_attributes(opening_line: str) -> dict[str, str]:
    """Parse attributes from table opening line {| class="wikitable" ... """
    attrs = {}
    # Remove {| prefix
    line = opening_line.strip()
    if line.startswith('{|'):
        line = line[2:].strip()

    # Extract class if present
    class_match = re.search(r'class=["\']([^"\']+)["\']', line)
    if class_match:
        attrs['class'] = class_match.group(1)
    else:
        # Default to wikitable for clean Wikipedia-style table rendering
        attrs['class'] = 'wikitable'

    return attrs


def _parse_cell(cell: str, is_header: bool = False) -> dict:
    """Parse a table cell, extracting attributes and content.

    Returns a dict with 'content', 'align', 'colspan', 'rowspan', etc.
    """
    import html

    cell = cell.strip()
    result = {
        'content': '',
        'align': None,
        'colspan': None,
        'rowspan': None,
        'style': None,
    }

    # Check if cell has attributes (pattern: attrs | content)
    pipe_idx = cell.find('|')
    if pipe_idx != -1 and '[' not in cell[:pipe_idx]:
        attrs_part = cell[:pipe_idx]
        content_part = cell[pipe_idx + 1:].strip()

        # Parse attributes
        # align
        align_match = re.search(r'align=["\']?(\w+)["\']?', attrs_part)
        if align_match:
            result['align'] = align_match.group(1)

        # text-align in style
        style_align_match = re.search(r'text-align:\s*(\w+)', attrs_part)
        if style_align_match:
            result['align'] = style_align_match.group(1)

        # colspan
        colspan_match = re.search(r'colspan=["\']?(\d+)["\']?', attrs_part)
        if colspan_match:
            result['colspan'] = int(colspan_match.group(1))

        # rowspan
        rowspan_match = re.search(r'rowspan=["\']?(\d+)["\']?', attrs_part)
        if rowspan_match:
            result['rowspan'] = int(rowspan_match.group(1))

        # background color from style
        bg_match = re.search(r'background:\s*([^;"|]+)', attrs_part)
        if bg_match:
            result['style'] = f'background:{bg_match.group(1).strip()}'

        result['content'] = html.escape(content_part)
    else:
        result['content'] = html.escape(cell)

    return result


def _render_cell(cell: dict, tag: str = 'td') -> str:
    """Render a cell dict as an HTML tag.

    Processes cell content through inline converters (bold, italic, links)
    before rendering.
    """
    attrs = []

    if cell.get('align'):
        attrs.append(f'class="align-{cell["align"]}"')

    if cell.get('colspan') and cell['colspan'] > 1:
        attrs.append(f'colspan="{cell["colspan"]}"')

    if cell.get('rowspan') and cell['rowspan'] > 1:
        attrs.append(f'rowspan="{cell["rowspan"]}"')

    if cell.get('style'):
        attrs.append(f'style="{cell["style"]}"')

    # Process inline wikitext in cell content
    import html
    content = cell["content"]
    # Unescape so we can process wikitext
    content = html.unescape(content)
    # Apply inline converters
    content = _convert_bold_italic(content)
    content = _convert_links(content)

    attrs_str = ' ' + ' '.join(attrs) if attrs else ''
    return f'<{tag}{attrs_str}>{content}</{tag}>'


def _convert_bold_italic(text: str) -> str:
    """Convert '''bold''' and ''italic'' to HTML.

    Args:
        text: Text with wikitext formatting.

    Returns:
        Text with HTML formatting.
    """
    # Convert bold+italic (must come first)
    text = re.sub(r"'''''(.+?)'''''", r"<strong><em>\1</em></strong>", text)

    # Convert bold
    text = re.sub(r"'''(.+?)'''", r"<strong>\1</strong>", text)

    # Convert italic
    text = re.sub(r"''(.+?)''", r"<em>\1</em>", text)

    return text


def _convert_headings(text: str) -> str:
    """Convert == Heading == to <h2>Heading</h2>.

    Args:
        text: Text with wikitext headings.

    Returns:
        Text with HTML headings.
    """
    # Handle heading levels 2-6 (MediaWiki uses 2+ equals)
    for level in range(6, 1, -1):  # Start from highest level to avoid partial matches
        equals = "=" * level
        # Match: ===Title=== with optional whitespace
        pattern = rf"^{re.escape(equals)}\s*(.+?)\s*{re.escape(equals)}\s*$"
        replacement = rf"<h{level}>\1</h{level}>"
        text = re.sub(pattern, replacement, text, flags=re.MULTILINE)

    return text


def _convert_links(text: str) -> str:
    """Convert [[Page]] and [[Page|Label]] wikilinks to local article links.

    Each link points at ``/article/{title}`` and carries HTMX attributes so
    the front-end loads it as a fragment swap into ``#article`` rather than
    a full-page navigation.

    Title normalisation matches MediaWiki's convention:
      - First letter is capitalised (``[[python]]`` → ``Python``)
      - ``#anchor`` is split off the target and re-attached as the URL fragment

    Args:
        text: Text with wikitext links.

    Returns:
        Text with HTML links.
    """
    import html
    from urllib.parse import quote

    def render(target: str, label: str, escape_label: bool) -> str:
        title, _, anchor = target.partition("#")
        # Apply MediaWiki's first-letter capitalisation. str.capitalize()
        # would lowercase the rest, so do it manually.
        title = title.strip()
        if title:
            title = title[:1].upper() + title[1:]
        # Build the local URL. quote() handles spaces, slashes, and other
        # path-unsafe characters; matches what search_results.html does.
        href = f"/article/{quote(title)}"
        hx_url = href  # HTMX attribute uses the same URL
        if anchor:
            href = f"{href}#{quote(anchor)}"
        # The href value is interpolated into an HTML attribute, so escape it.
        href_attr = html.escape(href, quote=True)
        hx_attr = html.escape(hx_url, quote=True)
        rendered_label = html.escape(label) if escape_label else label
        return (
            f'<a href="{href_attr}" '
            f'hx-get="{hx_attr}" '
            f'hx-target="#article" '
            f'hx-swap="innerHTML">{rendered_label}</a>'
        )

    # [[Page|Label]] — labels may contain inline HTML (e.g. <code>), so
    # don't escape them.
    text = re.sub(
        r"\[\[([^\]|]+)\|([^\]]+)\]\]",
        lambda m: render(m.group(1), m.group(2), escape_label=False),
        text,
    )

    # [[Page]] — the page name doubles as the visible label and is plain text.
    text = re.sub(
        r"\[\[([^\]|]+)\]\]",
        lambda m: render(m.group(1), m.group(1), escape_label=True),
        text,
    )

    return text


def _convert_lists(text: str) -> str:
    """Convert wikitext lists to HTML format.

    Handles all four wikitext list types and arbitrary nesting/mixing:
      *  → unordered (<ul><li>item</li></ul>)
      #  → ordered (<ol><li>item</li></ol>)
      ;  → definition term (<dl><dt>term</dt></dl>)
      :  → definition description (<dd>description</dd>)

    The last prefix character determines type; prefix length determines depth.
    Mixed prefixes like #* or *# are supported naturally.
    """
    lines = text.split("\n")
    converted = []
    stack = []  # Track open list tags: [type_char, ...]

    def close_lists_to_level(target_level):
        """Close lists until we're at the target level."""
        while len(stack) > target_level:
            list_type = stack.pop()
            if list_type == '*':
                converted.append('</ul>')
            elif list_type == '#':
                converted.append('</ol>')
            elif list_type in (';', ':'):
                converted.append('</dl>')

    for line in lines:
        m = re.match(r'^([*#;:]+)(.*)', line)
        if not m:
            # Close all open lists
            close_lists_to_level(0)
            converted.append(line)
            continue

        prefix = m.group(1)
        # Don't escape — list content may contain inline HTML like <code> tags
        # from the trusted wikitext source.
        content = m.group(2).lstrip()

        # Determine what lists should be open at each level
        target_stack = list(prefix)

        # Find where current and target stacks diverge
        common_len = 0
        for i in range(min(len(stack), len(target_stack))):
            if stack[i] == target_stack[i]:
                common_len += 1
            else:
                break

        # Close lists beyond the common prefix
        close_lists_to_level(common_len)

        # Open new lists as needed
        for i in range(common_len, len(target_stack)):
            list_char = target_stack[i]
            # Special handling for definition lists - ; and : share the same <dl>
            if list_char in (';', ':'):
                if not stack or stack[-1] not in (';', ':'):
                    converted.append('<dl>')
                    stack.append(list_char)
                else:
                    stack.append(list_char)
            elif list_char == '*':
                converted.append('<ul>')
                stack.append('*')
            elif list_char == '#':
                converted.append('<ol>')
                stack.append('#')

        # Add the list item
        last = prefix[-1]
        if last == '*':
            converted.append(f'<li>{content}</li>')
        elif last == '#':
            converted.append(f'<li>{content}</li>')
        elif last == ';':
            converted.append(f'<dt>{content}</dt>')
        elif last == ':':
            converted.append(f'<dd>{content}</dd>')

    # Close any remaining open lists
    close_lists_to_level(0)

    return "\n".join(converted)


def _convert_code_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Convert {{code|...}} templates to <code> tags.

    Handles templates like {{code|lang=python|print("hello")}} and converts
    them to <code>print("hello")</code>.

    Args:
        wikicode: Parsed wikicode object (modified in-place).
    """
    import html

    for template in wikicode.filter_templates():
        template_name = str(template.name).strip().lower()
        if template_name in ('code', 'codes', 'codett', 'c', 'mono', 'tt', 'kbd'):
            # Extract the code content from template params
            # Format: {{code|lang=python|actual code here}}
            # or: {{code|actual code here}}
            params = list(template.params)
            if params:
                # Find the last param that doesn't look like lang= or similar
                code_content = None
                for param in reversed(params):
                    param_str = str(param).strip()
                    if '=' not in param_str or not param_str.split('=')[0].strip().isalpha():
                        # This is the code content (either positional or named param with code)
                        if '=' in param_str:
                            code_content = param_str.split('=', 1)[1].strip()
                        else:
                            code_content = param_str
                        break

                if code_content:
                    # Replace template with <code> tag
                    replacement = f'<code>{html.escape(code_content)}</code>'
                    try:
                        wikicode.replace(template, replacement)
                    except ValueError:
                        pass


def _extract_syntaxhighlight(text: str) -> tuple[str, dict[str, str]]:
    """Extract <syntaxhighlight> blocks and replace with placeholders.

    This prevents code content from being processed by other converters.

    Args:
        text: Wikitext string.

    Returns:
        Tuple of (text with placeholders, dict of placeholder -> code block HTML).
    """
    import html

    code_blocks = {}
    counter = 0

    # Match <syntaxhighlight ...>content</syntaxhighlight>
    pattern = r'<syntaxhighlight[^>]*>(.*?)</syntaxhighlight>'

    def replace_with_placeholder(match):
        nonlocal counter
        content = match.group(1).strip()
        # Escape the content for HTML
        escaped = html.escape(content)
        code_html = f'<pre><code>{escaped}</code></pre>'

        # Use a placeholder that looks like a block element to avoid being wrapped in <p>
        placeholder = f'<div data-codeblock="{counter}"></div>'
        code_blocks[placeholder] = code_html
        counter += 1
        return placeholder

    text = re.sub(pattern, replace_with_placeholder, text, flags=re.DOTALL | re.IGNORECASE)
    return text, code_blocks


def _restore_code_blocks(text: str, code_blocks: dict[str, str]) -> str:
    """Restore code blocks from placeholders.

    Args:
        text: Text with placeholders.
        code_blocks: Dict of placeholder -> code block HTML.

    Returns:
        Text with code blocks restored.
    """
    for placeholder, code_html in code_blocks.items():
        text = text.replace(placeholder, code_html)
    return text


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


def _wrap_paragraphs(text: str) -> str:
    """Wrap plain text blocks in <p> tags.

    Identifies consecutive lines that aren't block-level HTML and wraps them in paragraphs.
    Preserves existing HTML structure (headings, lists, tables, etc.).
    """
    # Block-level tags that should NOT be wrapped in <p>
    block_tags = (
        'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption',
        'ul', 'ol', 'dl', 'li', 'dt', 'dd',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'div', 'p', 'blockquote', 'pre', 'syntaxhighlight'
    )

    lines = text.split('\n')
    result = []
    paragraph_lines = []

    def flush_paragraph():
        """Wrap accumulated paragraph lines in <p> tags."""
        if paragraph_lines:
            content = ' '.join(line.strip() for line in paragraph_lines if line.strip())
            if content:
                result.append(f'<p>{content}</p>')
            paragraph_lines.clear()

    def is_block_tag(line: str) -> bool:
        """Check if line starts with a block-level HTML tag."""
        stripped = line.strip()
        if not stripped.startswith('<'):
            return False
        # Check if it's a block tag (opening or closing)
        for tag in block_tags:
            if stripped.startswith(f'<{tag}') or stripped.startswith(f'</{tag}'):
                return True
        return False

    for line in lines:
        stripped = line.strip()

        # Check if line is block-level HTML
        if not stripped:
            # Blank line - flush current paragraph
            flush_paragraph()
            continue
        elif is_block_tag(stripped):
            # Block-level HTML - flush paragraph and add the HTML
            flush_paragraph()
            result.append(line)
        else:
            # Plain text or inline HTML - add to current paragraph
            paragraph_lines.append(stripped)

    # Flush any remaining paragraph
    flush_paragraph()

    return '\n'.join(result)


def _clean_extra_markup(text: str) -> str:
    """Clean up extra whitespace and formatting.

    Args:
        text: Text with potential extra markup.

    Returns:
        Cleaned text.
    """
    # Remove multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Remove trailing whitespace from lines
    text = "\n".join(line.rstrip() for line in text.split("\n"))

    return text
