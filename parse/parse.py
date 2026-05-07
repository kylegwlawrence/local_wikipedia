"""Parse Wikipedia dump XML and store articles in SQLite database."""
import argparse
import bz2
import os
import pathlib
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from typing import Any
from tqdm import tqdm

# Define constants
DEFAULT_WIKI = "simplewiki"
DUMPS_DIR = pathlib.Path("dumps")
BATCH_SIZE = 1000
NAMESPACE_MAIN = 0

# XML namespace for MediaWiki export format
NS = {"mw": "http://www.mediawiki.org/xml/export-0.11/"}


def _create_schema(conn: sqlite3.Connection) -> None:
    """Create database schema with tables and indexes.

    Args:
        conn: SQLite connection.
    """
    cursor = conn.cursor()

    # Main articles table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            page_id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            namespace INTEGER NOT NULL DEFAULT 0,
            revision_id INTEGER NOT NULL,
            parent_revision_id INTEGER,
            timestamp TEXT NOT NULL,
            contributor_username TEXT,
            contributor_id INTEGER,
            comment TEXT,
            text_bytes INTEGER,
            text_content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Indexes for common queries
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_articles_title ON articles(title)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_articles_namespace ON articles(namespace)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_articles_timestamp ON articles(timestamp)")

    # Metadata table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS parse_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wiki TEXT NOT NULL,
            source_file TEXT NOT NULL,
            total_pages INTEGER NOT NULL,
            articles_count INTEGER NOT NULL,
            parse_started_at TEXT NOT NULL,
            parse_completed_at TEXT NOT NULL,
            parse_duration_seconds REAL NOT NULL
        )
    """)

    # SQLite performance tuning
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA page_size=4096")
    cursor.execute("PRAGMA synchronous=NORMAL")

    conn.commit()


def _get_text(elem: ET.Element, tag: str) -> str | None:
    """Extract text content from a child element.

    Args:
        elem: Parent XML element.
        tag: Tag name to search for (with namespace).

    Returns:
        Text content if found, None otherwise.
    """
    child = elem.find(tag, NS)
    return child.text if child is not None else None


def _parse_page_element(page_elem: ET.Element) -> dict[str, Any] | None:
    """Extract article data from a <page> XML element.

    Args:
        page_elem: XML element representing a page.

    Returns:
        Dictionary with article fields, or None if page should be skipped.
    """
    try:
        title = _get_text(page_elem, "mw:title")
        if not title:
            return None

        page_id = _get_text(page_elem, "mw:id")
        namespace = _get_text(page_elem, "mw:ns")

        if page_id is None or namespace is None:
            return None

        # Find revision element
        revision = page_elem.find("mw:revision", NS)
        if revision is None:
            return None

        revision_id = _get_text(revision, "mw:id")
        if not revision_id:
            return None

        parent_revision_id = _get_text(revision, "mw:parentid")
        timestamp = _get_text(revision, "mw:timestamp")
        comment = _get_text(revision, "mw:comment")

        # Extract contributor info
        contributor = revision.find("mw:contributor", NS)
        contributor_username = None
        contributor_id = None
        if contributor is not None:
            contributor_username = _get_text(contributor, "mw:username")
            contrib_id = _get_text(contributor, "mw:id")
            contributor_id = int(contrib_id) if contrib_id else None

        # Extract text content
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
    except (ValueError, AttributeError) as e:
        return None


def _batch_insert_articles(conn: sqlite3.Connection, articles: list[dict[str, Any]]) -> None:
    """Insert a batch of articles using executemany for performance.

    Args:
        conn: SQLite connection.
        articles: List of article dictionaries.
    """
    if not articles:
        return

    cursor = conn.cursor()
    cursor.executemany(
        """
        INSERT OR REPLACE INTO articles (
            page_id, title, namespace, revision_id, parent_revision_id,
            timestamp, contributor_username, contributor_id, comment,
            text_bytes, text_content
        ) VALUES (
            :page_id, :title, :namespace, :revision_id, :parent_revision_id,
            :timestamp, :contributor_username, :contributor_id, :comment,
            :text_bytes, :text_content
        )
        """,
        articles,
    )


def _record_metadata(
    conn: sqlite3.Connection,
    wiki: str,
    source_file: str,
    total_pages: int,
    articles_count: int,
    start_time: float,
    end_time: float,
) -> None:
    """Record parse metadata for tracking.

    Args:
        conn: SQLite connection.
        wiki: Wiki name.
        source_file: Source dump filename.
        total_pages: Total pages parsed.
        articles_count: Number of articles inserted.
        start_time: Start timestamp.
        end_time: End timestamp.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO parse_metadata (
            wiki, source_file, total_pages, articles_count,
            parse_started_at, parse_completed_at, parse_duration_seconds
        ) VALUES (?, ?, ?, ?, datetime(?,'unixepoch'), datetime(?,'unixepoch'), ?)
        """,
        (
            wiki,
            source_file,
            total_pages,
            articles_count,
            start_time,
            end_time,
            end_time - start_time,
        ),
    )
    conn.commit()


def parse_dump(
    dump_path: pathlib.Path,
    db_path: pathlib.Path,
    namespace_filter: int = NAMESPACE_MAIN,
) -> tuple[int, int]:
    """Parse Wikipedia XML dump and insert into SQLite database.

    Args:
        dump_path: Path to .xml.bz2 dump file.
        db_path: Path to SQLite database (will be created).
        namespace_filter: Only include pages in this namespace (0=articles).

    Returns:
        Tuple of (total_pages_parsed, articles_inserted).

    Raises:
        RuntimeError: If XML parsing fails or database operations fail.
    """
    if not dump_path.exists():
        raise RuntimeError(f"Dump file not found: {dump_path}")

    # Write to temp database for atomic operation
    tmp_db = db_path.with_suffix(".db.tmp")

    try:
        start_time = time.time()
        conn = sqlite3.connect(tmp_db)
        _create_schema(conn)

        batch: list[dict[str, Any]] = []
        total_pages = 0
        articles_inserted = 0

        print(f"Parsing {dump_path.name} ...", flush=True)

        with bz2.open(dump_path, "rb") as f:
            # Use iterparse for memory-efficient streaming
            context = ET.iterparse(f, events=("end",))

            with tqdm(unit="pages", desc="Parsing dump") as pbar:
                for event, elem in context:
                    # Process each <page> element
                    if elem.tag == "{http://www.mediawiki.org/xml/export-0.11/}page":
                        total_pages += 1

                        article = _parse_page_element(elem)

                        # Filter by namespace
                        if article and article["namespace"] == namespace_filter:
                            batch.append(article)
                            articles_inserted += 1

                            # Batch insert for performance
                            if len(batch) >= BATCH_SIZE:
                                _batch_insert_articles(conn, batch)
                                conn.commit()
                                batch.clear()

                        # Clear element to free memory
                        elem.clear()
                        pbar.update(1)

        # Insert remaining batch
        if batch:
            _batch_insert_articles(conn, batch)
            conn.commit()

        end_time = time.time()

        # Record metadata
        _record_metadata(
            conn,
            dump_path.stem.split("-")[0],  # Extract wiki name from filename
            dump_path.name,
            total_pages,
            articles_inserted,
            start_time,
            end_time,
        )

        conn.close()

        # Atomic rename
        os.replace(tmp_db, db_path)

        return total_pages, articles_inserted

    except ET.ParseError as e:
        # Cleanup temp file on error
        try:
            tmp_db.unlink()
        except FileNotFoundError:
            pass
        raise RuntimeError(f"XML parsing failed: {e}")
    except sqlite3.Error as e:
        # Cleanup temp file on error
        try:
            tmp_db.unlink()
        except FileNotFoundError:
            pass
        raise RuntimeError(f"Database error: {e}")


def verify_database(db_path: pathlib.Path) -> dict[str, Any]:
    """Verify database integrity and return statistics.

    Args:
        db_path: Path to SQLite database.

    Returns:
        Dictionary with database statistics.

    Raises:
        RuntimeError: If database is invalid or cannot be opened.
    """
    if not db_path.exists():
        raise RuntimeError(f"Database not found: {db_path}")

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get article count
        cursor.execute("SELECT COUNT(*) FROM articles")
        article_count = cursor.fetchone()[0]

        # Get sample articles
        cursor.execute("SELECT title, text_bytes FROM articles LIMIT 5")
        samples = cursor.fetchall()

        # Get metadata
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


def _find_latest_dump(wiki: str) -> pathlib.Path | None:
    """Find the latest dump file for a given wiki in the dumps directory.

    Args:
        wiki: Wiki name (e.g., simplewiki, enwiki).

    Returns:
        Path to the latest dump file, or None if not found.
    """
    if not DUMPS_DIR.exists():
        return None

    # Find all matching dump files
    pattern = f"{wiki}-*-pages-articles-multistream.xml.bz2"
    matches = sorted(DUMPS_DIR.glob(pattern), reverse=True)

    return matches[0] if matches else None


def main(argv: list[str] | None = None) -> int:
    """Parse Wikipedia dump into SQLite database.

    Args:
        argv: Argument list to parse. Defaults to ``sys.argv[1:]`` when ``None``.

    Returns:
        ``0`` on success, ``1`` on failure.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wiki",
        default=DEFAULT_WIKI,
        help="wiki name for auto-discovery (e.g., simplewiki, enwiki)",
    )
    parser.add_argument(
        "--dump",
        type=pathlib.Path,
        help="explicit path to dump file (optional, auto-discovers if not set)",
    )
    parser.add_argument(
        "--database",
        type=pathlib.Path,
        help="output database path (default: dumps/{wiki}.db)",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="verify existing database without parsing",
    )

    args = parser.parse_args(argv)

    # Determine database path
    db_path = args.database or DUMPS_DIR / f"{args.wiki}.db"

    # Verify-only mode
    if args.verify_only:
        try:
            stats = verify_database(db_path)
            print(f"\n=== Database Statistics ===")
            print(f"Database: {db_path}")
            print(f"Articles: {stats['article_count']:,}")
            print(f"\nSample articles:")
            for title, text_bytes in stats["samples"]:
                print(f"  - {title} ({text_bytes:,} bytes)")
            if stats["metadata"]:
                meta = stats["metadata"]
                print(f"\nLast parse:")
                print(f"  Wiki: {meta[1]}")
                print(f"  Source: {meta[2]}")
                print(f"  Total pages: {meta[3]:,}")
                print(f"  Articles: {meta[4]:,}")
                print(f"  Duration: {meta[7]:.1f}s")
            return 0
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

    # Determine dump path
    dump_path = args.dump
    if not dump_path:
        dump_path = _find_latest_dump(args.wiki)
        if not dump_path:
            print(f"ERROR: No dump file found for {args.wiki}", file=sys.stderr)
            print(f"Run: python download/download.py --wiki {args.wiki}", file=sys.stderr)
            return 1

    # Parse dump
    try:
        total_pages, articles_inserted = parse_dump(dump_path, db_path, NAMESPACE_MAIN)

        print(f"\n=== Summary ===")
        print(f"Dump: {dump_path.name}")
        print(f"Database: {db_path}")
        print(f"Total pages parsed: {total_pages:,}")
        print(f"Articles inserted (namespace=0): {articles_inserted:,}")
        print(f"Database size: {db_path.stat().st_size:,} bytes")

        return 0
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
