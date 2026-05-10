"""Database integrity check used by ``parse --verify-only``."""
import pathlib
import sqlite3
from typing import Any


def verify_database(db_path: pathlib.Path) -> dict[str, Any]:
    """Return article count, sample rows, and most recent parse metadata."""
    if not db_path.exists():
        raise RuntimeError(f"Database not found: {db_path}")

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM articles")
        article_count = cursor.fetchone()[0]

        cursor.execute("SELECT title, text_bytes FROM articles LIMIT 5")
        samples = cursor.fetchall()

        cursor.execute("SELECT * FROM parse_metadata ORDER BY id DESC LIMIT 1")
        metadata = cursor.fetchone()

        conn.close()
        return {
            "article_count": article_count,
            "samples": samples,
            "metadata": metadata,
        }
    except sqlite3.Error as e:
        raise RuntimeError(f"Database verification failed: {e}")
