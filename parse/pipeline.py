"""Stream a bz2 XML dump into SQLite."""
import bz2
import os
import pathlib
import sqlite3
import time
import xml.etree.ElementTree as ET
from typing import Any

from tqdm import tqdm

from parse.schema import create_schema
from parse.xml_reader import PAGE_TAG, parse_page_element

BATCH_SIZE = 1000
NAMESPACE_MAIN = 0


def _batch_insert_articles(
    conn: sqlite3.Connection, articles: list[dict[str, Any]]
) -> None:
    """Insert a batch of articles using ``executemany``."""
    if not articles:
        return
    conn.cursor().executemany(
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
    conn.cursor().execute(
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
    """Parse a Wikipedia ``.xml.bz2`` dump into a SQLite database.

    Writes to ``{db_path}.tmp`` first and atomically renames on success so a
    partial run can never leave a corrupt destination database.

    Returns:
        ``(total_pages_parsed, articles_inserted)``.
    """
    if not dump_path.exists():
        raise RuntimeError(f"Dump file not found: {dump_path}")

    tmp_db = db_path.with_suffix(".db.tmp")
    conn: sqlite3.Connection | None = None
    completed = False

    try:
        start_time = time.time()
        conn = sqlite3.connect(tmp_db)
        create_schema(conn)

        batch: list[dict[str, Any]] = []
        total_pages = 0
        articles_inserted = 0

        print(f"Parsing {dump_path.name} ...", flush=True)

        truncated = False
        with bz2.open(dump_path, "rb") as f:
            context = ET.iterparse(f, events=("end",))

            with tqdm(unit="pages", desc="Parsing dump") as pbar:
                try:
                    for _event, elem in context:
                        if elem.tag == PAGE_TAG:
                            total_pages += 1
                            article = parse_page_element(elem)
                            if article and article["namespace"] == namespace_filter:
                                batch.append(article)
                                articles_inserted += 1
                                if len(batch) >= BATCH_SIZE:
                                    _batch_insert_articles(conn, batch)
                                    conn.commit()
                                    batch.clear()
                            elem.clear()
                            pbar.update(1)
                except (ET.ParseError, EOFError):
                    truncated = True

        if truncated:
            print(
                f"Warning: dump truncated — saving {articles_inserted:,} articles "
                "parsed before end of file",
                flush=True,
            )

        if batch:
            _batch_insert_articles(conn, batch)
            conn.commit()

        end_time = time.time()
        _record_metadata(
            conn,
            dump_path.stem.split("-")[0],
            dump_path.name,
            total_pages,
            articles_inserted,
            start_time,
            end_time,
        )
        conn.close()
        conn = None
        os.replace(tmp_db, db_path)
        completed = True
        return total_pages, articles_inserted

    except sqlite3.Error as e:
        raise RuntimeError(f"Database error: {e}")
    finally:
        # Always release the connection and clean the tmp file on any failure
        # path (including KeyboardInterrupt and unexpected exceptions). The
        # tmp file is only kept on the happy path, where os.replace has already
        # consumed it.
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        if not completed:
            try:
                tmp_db.unlink()
            except FileNotFoundError:
                pass
