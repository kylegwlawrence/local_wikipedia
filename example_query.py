#!/usr/bin/env python3
"""Example usage of the query_database function."""
import json
from parse.parse import query_database

# Example 1: Get full article text for a specific article
print("=== Get article text for 'Python (programming language)' ===")
result = query_database(
    "SELECT title, text_content FROM articles WHERE title = 'Python (programming language)'",
    format="json"
)
if result:
    article = result[0]
    print(f"Title: {article['title']}")
    print(f"Content length: {len(article['text_content'])} characters")
    print(f"\nFirst 500 characters:")
    print(article['text_content'][:500])
    print("...\n")
else:
    print("Article not found\n")

# Example 2: Search for articles (table format - default)
print("=== Search for Python-related articles ===")
result = query_database(
    "SELECT title, page_id, text_bytes FROM articles WHERE title LIKE 'Python%' LIMIT 5"
)
print(result)

# Example 3: Count articles by namespace (JSON format)
print("\n=== Count articles by namespace ===")
result = query_database(
    "SELECT namespace, COUNT(*) as count FROM articles GROUP BY namespace",
    format="json"
)
print(json.dumps(result, indent=2))

# Example 4: Find largest articles
print("\n=== Top 5 largest articles ===")
result = query_database(
    "SELECT title, text_bytes FROM articles ORDER BY text_bytes DESC LIMIT 5"
)
print(result)

# Example 5: Recent edits (JSON)
print("\n=== Most recent edits ===")
result = query_database(
    "SELECT title, timestamp, contributor_username FROM articles ORDER BY timestamp DESC LIMIT 3",
    format="json"
)
print(json.dumps(result, indent=2))

# Example 6: Search in article text (full content search)
print("\n=== Articles mentioning 'programming language' ===")
result = query_database(
    """
    SELECT title, text_bytes
    FROM articles
    WHERE text_content LIKE '%programming language%'
    LIMIT 5
    """
)
print(result)

# Example 7: Statistics
print("\n=== Database statistics ===")
result = query_database(
    """
    SELECT
        COUNT(*) as total_articles,
        AVG(text_bytes) as avg_size,
        MAX(text_bytes) as max_size,
        MIN(text_bytes) as min_size
    FROM articles
    """,
    format="json"
)
print(json.dumps(result, indent=2))
