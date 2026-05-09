#!/usr/bin/env python3
"""Simple script to get article text by title."""
import sys
from parse.parse import query_database
from parse.wikitext_to_markdown import convert_wikitext_to_markdown

def get_article(title):
    """Get the full text of an article by its exact title."""
    result = query_database(
        f"SELECT title, text_content, text_bytes, timestamp FROM articles WHERE title = '{title}'",
        format="json"
    )
    
    if not result:
        print(f"Article '{title}' not found.")
        print("\nTip: Article titles are case-sensitive. Try searching first:")
        print(f"  SELECT title FROM articles WHERE title LIKE '%{title}%'")
        return None
    
    return result[0]

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python get_article.py 'Article Title'")
        print("\nExamples:")
        print("  python get_article.py 'Python (programming language)'")
        print("  python get_article.py 'April'")
        sys.exit(1)
    
    title = sys.argv[1]
    article = get_article(title)
    
    if article:
        # Convert wikitext to clean, readable Markdown
        markdown_content = convert_wikitext_to_markdown(article['text_content'])

        print(f"Title: {article['title']}")
        print(f"Size: {article['text_bytes']:,} bytes")
        print(f"Last edited: {article['timestamp']}")
        print(f"\n{'='*80}")
        print(markdown_content)
        print(f"{'='*80}")
