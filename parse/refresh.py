"""Incremental refresh of an existing wiki database from a new dump.

Unlike parse_dump (which atomically replaces the whole database), refresh_dump
operates on the live database in-place. For each article in the dump it:
  - skips if revision_id matches what is already stored,
  - archives the old row then updates if revision_id differs,
  - inserts if the page_id is new.
"""

import bz2
import pathlib
import sqlite3
import xml.etree.ElementTree as ET
from typing import Any

from parse.pipeline import BATCH_SIZE, NAMESPACE_MAIN
from parse.schema import create_schema
from parse.xml_reader import PAGE_TAG, parse_page_element

_ARCHIVE_SQL = """
    INSERT INTO articles_archive (
        page_id, title, namespace, revision_id, parent_revision_id,
        timestamp, contributor_username, contributor_id, comment,
        text_bytes, text_content, created_at
    )
    SELECT
        page_id, title, namespace, revision_id, parent_revision_id,
        timestamp, contributor_username, contributor_id, comment,
        text_bytes, text_content, created_at
    FROM articles WHERE page_id = ?
"""

_UPDATE_SQL = """
    UPDATE articles SET
        title = :title,
        namespace = :namespace,
        revision_id = :revision_id,
        parent_revision_id = :parent_revision_id,
        timestamp = :timestamp,
        contributor_username = :contributor_username,
        contributor_id = :contributor_id,
        comment = :comment,
        text_bytes = :text_bytes,
        text_content = :text_content
    WHERE page_id = :page_id
"""

_INSERT_SQL = """
    INSERT OR IGNORE INTO articles (
        page_id, title, namespace, revision_id, parent_revision_id,
        timestamp, contributor_username, contributor_id, comment,
        text_bytes, text_content
    ) VALUES (
        :page_id, :title, :namespace, :revision_id, :parent_revision_id,
        :timestamp, :contributor_username, :contributor_id, :comment,
        :text_bytes, :text_content
    )
"""


def refresh_dump(
    dump_path: pathlib.Path,
    db_path: pathlib.Path,
    job_id: int,
    jobs_db_path: pathlib.Path,
    namespace_filter: int = NAMESPACE_MAIN,
) -> dict[str, int]:
    """Incrementally refresh ``db_path`` from the given dump file.

    Returns a summary dict with keys:
        scanned, skipped, updated, inserted, archived
    """
    from jobs import (
        refresh as refresh_jobs,  # imported here to avoid circular import at module level
    )

    if not dump_path.exists():
        raise RuntimeError(f"Dump file not found: {dump_path}")
    if not db_path.exists():
        raise RuntimeError(f"Database not found: {db_path}. Run the initial parse first.")

    wiki_conn = sqlite3.connect(db_path)
    jobs_conn = refresh_jobs.connect_jobs(jobs_db_path)

    stats: dict[str, int] = {
        "scanned": 0,
        "skipped": 0,
        "updated": 0,
        "inserted": 0,
        "archived": 0,
    }

    try:
        # Ensure articles_archive table exists (migration for older databases).
        create_schema(wiki_conn)

        staging: list[dict[str, Any]] = []

        def _flush(final: bool = False) -> None:
            if not staging:
                return

            # Bulk lookup of existing revision_ids for this batch.
            placeholders = ",".join("?" * len(staging))
            page_ids = [a["page_id"] for a in staging]
            existing: dict[int, int] = dict(
                wiki_conn.execute(
                    f"SELECT page_id, revision_id FROM articles WHERE page_id IN ({placeholders})",
                    page_ids,
                ).fetchall()
            )

            to_archive: list[tuple[int]] = []
            to_update: list[dict[str, Any]] = []
            to_insert: list[dict[str, Any]] = []

            for article in staging:
                pid = article["page_id"]
                stats["scanned"] += 1
                if pid not in existing:
                    to_insert.append(article)
                    stats["inserted"] += 1
                elif existing[pid] == article["revision_id"]:
                    stats["skipped"] += 1
                else:
                    to_archive.append((pid,))
                    to_update.append(article)
                    stats["updated"] += 1
                    stats["archived"] += 1

            # Archive must happen before UPDATE so a crash leaves old data intact.
            if to_archive:
                wiki_conn.executemany(_ARCHIVE_SQL, to_archive)
            if to_update:
                wiki_conn.executemany(_UPDATE_SQL, to_update)
            if to_insert:
                wiki_conn.executemany(_INSERT_SQL, to_insert)

            wiki_conn.commit()
            staging.clear()

            refresh_jobs.update_job(
                jobs_conn,
                job_id,
                articles_scanned=stats["scanned"],
                articles_skipped=stats["skipped"],
                articles_updated=stats["updated"],
                articles_inserted=stats["inserted"],
                articles_archived=stats["archived"],
            )

        print(f"Refreshing {dump_path.name} → {db_path.name} …", flush=True)

        truncated = False
        with bz2.open(dump_path, "rb") as f:
            context = ET.iterparse(f, events=("end",))
            try:
                for _event, elem in context:
                    if elem.tag != PAGE_TAG:
                        continue
                    article = parse_page_element(elem)
                    elem.clear()
                    if not article or article["namespace"] != namespace_filter:
                        continue
                    staging.append(article)
                    if len(staging) >= BATCH_SIZE:
                        _flush()
            except (ET.ParseError, EOFError):
                truncated = True

        if truncated:
            print(
                f"Warning: dump truncated — saving {stats['scanned']:,} articles processed before end of file",
                flush=True,
            )

        _flush(final=True)
        return stats

    finally:
        wiki_conn.close()
        jobs_conn.close()
