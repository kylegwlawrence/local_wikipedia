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
        text = _convert_headings(text)
        text = _convert_bold_italic(text)
        text = _convert_links(text, base_url)
        text = _convert_lists(text)
        text = _clean_extra_markup(text)

        return text.strip()

    except Exception as e:
        # Fallback to plain text if parsing fails
        return wikitext


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
    """Convert * and # lists to Markdown format.

    Args:
        text: Text with wikitext lists.

    Returns:
        Text with Markdown lists.
    """
    lines = text.split("\n")
    converted_lines = []

    for line in lines:
        # Bullet lists: * Item → - Item
        if re.match(r"^\*+\s", line):
            # Count asterisks for nesting level
            asterisks = len(re.match(r"^\*+", line).group())
            indent = "  " * (asterisks - 1)  # 2 spaces per level
            content = re.sub(r"^\*+\s*", "", line)
            converted_lines.append(f"{indent}- {content}")
        # Numbered lists: # Item → 1. Item
        # BUT: Don't match markdown headings which also use # (headings have multiple # with space after)
        elif re.match(r"^#+\s", line) and not re.match(r"^#{2,}\s", line):
            hashes = len(re.match(r"^#+", line).group())
            indent = "  " * (hashes - 1)
            content = re.sub(r"^#+\s*", "", line)
            converted_lines.append(f"{indent}1. {content}")
        else:
            converted_lines.append(line)

    return "\n".join(converted_lines)


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
