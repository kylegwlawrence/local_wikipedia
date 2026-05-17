"""CLI: harvest arXiv metadata via OAI-PMH and upsert into ``arxiv.db``.

Usage:
    python -m arxiv.ingest [--from YYYY-MM-DD] [--until YYYY-MM-DD] [--from-cache] [--reset]

The default ``--from`` is the last successful harvest date stored in
``ingest_state.last_harvested_date``, falling back to ``2021-01-01`` on
first run. ``--from-cache`` re-parses every XML file in the OAI cache
directory and skips the network entirely — the main path during schema
iteration so a local mistake doesn't re-burn arxiv's rate limit.
"""

import argparse
import json
import sqlite3
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from tqdm import tqdm

import paths
from arxiv import oai
from arxiv.schema import connect_papers, get_ingest_state, set_ingest_state

DEFAULT_FROM = "2021-01-01"
BATCH_SIZE = 1000

_UPDATE_COLS = (
    "oai_datestamp",
    "title",
    "abstract",
    "authors",
    "categories",
    "primary_category",
    "submitted_date",
    "updated_date",
    "doi",
    "journal_ref",
    "comments",
)


def upsert_paper(conn: sqlite3.Connection, record: dict[str, Any]) -> str:
    """Insert or update one paper. Returns ``'inserted' | 'updated' | 'skipped'``."""
    existing = conn.execute("SELECT oai_datestamp FROM papers WHERE id = ?", (record["id"],)).fetchone()
    if existing is not None and existing["oai_datestamp"] == record["oai_datestamp"]:
        return "skipped"

    values = (
        record["oai_datestamp"],
        record["title"],
        record["abstract"],
        json.dumps(record["authors"]),
        record["categories"],
        record["primary_category"],
        record["submitted_date"],
        record["updated_date"],
        record["doi"],
        record["journal_ref"],
        record["comments"],
    )
    if existing is None:
        conn.execute(
            "INSERT INTO papers (id, " + ", ".join(_UPDATE_COLS) + ") "
            "VALUES (?, " + ", ".join("?" * len(_UPDATE_COLS)) + ")",
            (record["id"], *values),
        )
        return "inserted"
    set_clause = ", ".join(f"{c} = ?" for c in _UPDATE_COLS)
    conn.execute(f"UPDATE papers SET {set_clause} WHERE id = ?", (*values, record["id"]))
    return "updated"


def reset_papers(conn: sqlite3.Connection) -> None:
    """Drop all rows from ``papers`` and ``ingest_state``."""
    conn.executescript("DELETE FROM papers; DELETE FROM ingest_state;")
    conn.commit()


def ingest_records(
    conn: sqlite3.Connection,
    records: Iterable[dict[str, Any]],
    batch_size: int = BATCH_SIZE,
) -> dict[str, int]:
    """Apply ``upsert_paper`` to each record, committing every ``batch_size`` rows."""
    stats = {"inserted": 0, "updated": 0, "skipped": 0}
    for i, record in enumerate(records, 1):
        action = upsert_paper(conn, record)
        stats[action] += 1
        if i % batch_size == 0:
            conn.commit()
    conn.commit()
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from",
        dest="from_date",
        default=None,
        help=f"ISO date lower bound. Default: ingest_state.last_harvested_date, else {DEFAULT_FROM}.",
    )
    parser.add_argument("--until", dest="until_date", default=None, help="ISO date upper bound (inclusive).")
    parser.add_argument(
        "--from-cache",
        action="store_true",
        help="Re-parse every XML file in the OAI cache dir; do not hit the network.",
    )
    parser.add_argument("--reset", action="store_true", help="Drop papers + ingest_state before starting.")
    args = parser.parse_args(argv)

    paths.DUMPS_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect_papers(paths.ARXIV_DB)

    if args.reset:
        print("Resetting papers + ingest_state...")
        reset_papers(conn)

    if args.from_cache:
        if not paths.ARXIV_OAI_CACHE_DIR.exists():
            print(f"Cache dir not found: {paths.ARXIV_OAI_CACHE_DIR}", file=sys.stderr)
            conn.close()
            return 1
        records: Iterable[dict[str, Any]] = oai.iter_cached_records(paths.ARXIV_OAI_CACHE_DIR)
        source = f"cache ({paths.ARXIV_OAI_CACHE_DIR})"
    else:
        from_date = args.from_date or get_ingest_state(conn, "last_harvested_date") or DEFAULT_FROM
        source = f"OAI-PMH from={from_date}" + (f" until={args.until_date}" if args.until_date else "")
        records = oai.harvest_records(
            from_date=from_date,
            until_date=args.until_date,
            cache_dir=paths.ARXIV_OAI_CACHE_DIR,
        )

    print(f"Ingesting from {source}...")
    stats = ingest_records(conn, tqdm(records, desc="ingest", unit="rec"))

    # State is updated only when we actually hit the network — replaying cache
    # shouldn't move the "last harvested" pointer, since the cache may be stale.
    if not args.from_cache:
        cutoff = args.until_date or datetime.now(UTC).date().isoformat()
        set_ingest_state(conn, "last_harvested_date", cutoff)
        conn.commit()

    conn.close()
    print(f"Done. inserted={stats['inserted']} updated={stats['updated']} skipped={stats['skipped']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
