"""Command-line entry point for parsing a dump into SQLite."""

import argparse
import pathlib
import sqlite3
import sys

from parse.pipeline import NAMESPACE_MAIN, parse_dump
from parse.schema import create_schema
from parse.verify import verify_database
from paths import DEFAULT_WIKI, DUMPS_DIR


def _find_latest_dump(wiki: str, dumps_dir: pathlib.Path | None = None) -> pathlib.Path | None:
    """Find the latest dump file for ``wiki`` in ``dumps_dir``.

    Patterns are tried in preference order: multistream, monolithic, then
    partial ``.tmp`` downloads (so a truncated download can still be parsed).
    """
    if dumps_dir is None:
        dumps_dir = DUMPS_DIR
    if not dumps_dir.exists():
        return None
    for pattern in [
        f"{wiki}-*-pages-articles-multistream.xml.bz2",
        f"{wiki}-*-pages-articles.xml.bz2",
        f"{wiki}-*-pages-articles.xml.bz2.tmp",
    ]:
        matches = sorted(dumps_dir.glob(pattern), reverse=True)
        if matches:
            return matches[0]
    return None


def main(argv: list[str] | None = None) -> int:
    """CLI: parse a dump (or verify an existing database)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wiki", default=DEFAULT_WIKI)
    parser.add_argument("--dump", type=pathlib.Path)
    parser.add_argument("--database", type=pathlib.Path)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--rebuild-fts", action="store_true")

    args = parser.parse_args(argv)
    db_path = args.database or DUMPS_DIR / f"{args.wiki}.db"

    if args.rebuild_fts:
        if not db_path.exists():
            print(f"ERROR: Database not found: {db_path}", file=sys.stderr)
            return 1
        try:
            conn = sqlite3.connect(db_path)
            create_schema(conn)
            print("Rebuilding FTS5 index…", flush=True)
            conn.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
            conn.commit()
            conn.close()
            print("Done.", flush=True)
            return 0
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

    if args.verify_only:
        try:
            stats = verify_database(db_path)
            print("\n=== Database Statistics ===")
            print(f"Database: {db_path}")
            print(f"Articles: {stats['article_count']:,}")
            print("\nSample articles:")
            for title, text_bytes in stats["samples"]:
                print(f"  - {title} ({text_bytes:,} bytes)")
            if stats["metadata"]:
                meta = stats["metadata"]
                print("\nLast parse:")
                print(f"  Wiki: {meta[1]}")
                print(f"  Source: {meta[2]}")
                print(f"  Total pages: {meta[3]:,}")
                print(f"  Articles: {meta[4]:,}")
                print(f"  Duration: {meta[7]:.1f}s")
            return 0
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

    dump_path = args.dump or _find_latest_dump(args.wiki)
    if not dump_path:
        print(f"ERROR: No dump file found for {args.wiki}", file=sys.stderr)
        print(f"Run: python -m download.download --wiki {args.wiki}", file=sys.stderr)
        return 1

    try:
        total_pages, articles_inserted = parse_dump(dump_path, db_path, NAMESPACE_MAIN)
        print("\n=== Summary ===")
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
