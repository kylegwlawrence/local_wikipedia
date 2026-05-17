"""CLI: embed arXiv papers via nomic-embed-text into ``arxiv_rag.db``.

Usage:
    python -m arxiv.embed [--limit N] [--batch 100] [--reset]

Diffs ``arxiv.db.papers`` against ``arxiv_rag.db.papers_meta`` by
``oai_datestamp`` — new papers get embedded, changed papers are deleted +
re-embedded, unchanged papers are skipped. Each paper is one chunk
(title + abstract + categories). FTS5 is bulk-rebuilt once at the end.
"""

import argparse
import sqlite3
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from tqdm import tqdm

import paths
from arxiv.schema import (
    connect_arxiv_rag,
    connect_papers,
    get_embedding_dim,
    set_embedding_dim,
)
from arxiv.templates_meta import format_embed_text
from rag import embedder

DEFAULT_BATCH = 100


def load_embedded(rag_conn: sqlite3.Connection) -> dict[str, str]:
    """Return ``{arxiv_id: oai_datestamp}`` for already-embedded papers."""
    rows = rag_conn.execute("SELECT arxiv_id, oai_datestamp FROM papers_meta").fetchall()
    return {r["arxiv_id"]: r["oai_datestamp"] for r in rows}


def delete_paper(rag_conn: sqlite3.Connection, arxiv_id: str) -> None:
    """Remove one paper's rows from ``papers_meta`` and ``papers_vec``.

    Deliberately does not touch ``papers_fts``: the CLI does a bulk
    ``rebuild`` at the end of the run and that rebuild reads from the
    content table (``papers_meta``), so any rows we delete here are
    automatically dropped from the FTS index when it gets rebuilt. Trying
    to do an incremental FTS delete here would fail with a malformed-DB
    error when the row was never incrementally indexed (the common case
    for fresh-batch runs).
    """
    row = rag_conn.execute("SELECT rowid FROM papers_meta WHERE arxiv_id = ?", (arxiv_id,)).fetchone()
    if row is None:
        return
    rowid = row["rowid"]
    rag_conn.execute("DELETE FROM papers_vec WHERE rowid = ?", (rowid,))
    rag_conn.execute("DELETE FROM papers_meta WHERE rowid = ?", (rowid,))


def reset_rag(rag_conn: sqlite3.Connection) -> None:
    """Wipe all embeddings from ``arxiv_rag.db``, keeping schema intact."""
    rag_conn.executescript("""
        DELETE FROM papers_vec;
        DELETE FROM papers_meta;
        DELETE FROM _meta;
    """)
    rag_conn.execute("INSERT INTO papers_fts(papers_fts) VALUES('rebuild')")
    rag_conn.commit()


def _check_dim(rag_conn: sqlite3.Connection, vec: list[float]) -> None:
    """Record the embedding dimension on first write; raise if it ever changes."""
    actual = len(vec)
    stored = get_embedding_dim(rag_conn)
    if stored is None:
        set_embedding_dim(rag_conn, actual)
    elif actual != stored:
        raise ValueError(
            f"Embedding dimension mismatch: arxiv_rag.db expects {stored}, "
            f"model returned {actual}. Use --reset to wipe and re-embed."
        )


def embed_papers(
    rag_conn: sqlite3.Connection,
    papers: list[dict[str, Any]],
    embedded_at: str,
    ollama_url: str = embedder.OLLAMA_BASE_URL,
) -> int:
    """Embed a batch of papers and insert into ``papers_meta`` + ``papers_vec``.

    Caller must ensure any prior rows for these ``arxiv_id`` values have
    already been deleted (the UNIQUE constraint on ``arxiv_id`` enforces it).
    """
    if not papers:
        return 0
    texts = [f"{embedder.EMBED_DOC_PREFIX}{format_embed_text(p)}" for p in papers]
    vecs = embedder.embed_texts_batch(texts, base_url=ollama_url)
    _check_dim(rag_conn, vecs[0])
    for paper, vec in zip(papers, vecs, strict=True):
        cur = rag_conn.execute(
            "INSERT INTO papers_meta (arxiv_id, oai_datestamp, embed_text, embedded_at) VALUES (?, ?, ?, ?)",
            (paper["id"], paper["oai_datestamp"], format_embed_text(paper), embedded_at),
        )
        rag_conn.execute(
            "INSERT INTO papers_vec (rowid, embedding) VALUES (?, ?)",
            (cur.lastrowid, embedder.pack_embedding(vec)),
        )
    return len(papers)


def iter_papers(papers_conn: sqlite3.Connection, limit: int | None = None) -> Iterator[dict[str, Any]]:
    """Yield paper dicts ordered by id, with only the fields embed.py needs."""
    query = "SELECT id, oai_datestamp, title, abstract, categories FROM papers ORDER BY id"
    params: tuple = ()
    if limit is not None:
        query += " LIMIT ?"
        params = (limit,)
    for row in papers_conn.execute(query, params):
        yield {
            "id": row["id"],
            "oai_datestamp": row["oai_datestamp"],
            "title": row["title"],
            "abstract": row["abstract"],
            "categories": row["categories"],
        }


def embed_one_abstract(
    arxiv_id: str,
    *,
    papers_conn: sqlite3.Connection,
    rag_conn: sqlite3.Connection,
    ollama_url: str = embedder.OLLAMA_BASE_URL,
    embedded_at: str | None = None,
) -> None:
    """Embed (or re-embed) a single paper's abstract into ``papers_meta``.

    Designed for per-paper UI triggers — the CLI loop in ``main`` calls
    ``embed_papers`` directly for batch efficiency. Idempotent: any prior
    row for ``arxiv_id`` is deleted before re-inserting. Bulk-rebuilds
    ``papers_fts`` at the end because there is no batched end-of-run here.

    Raises KeyError if ``arxiv_id`` is not present in ``papers``.
    """
    row = papers_conn.execute(
        "SELECT id, oai_datestamp, title, abstract, categories FROM papers WHERE id = ?",
        (arxiv_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"arxiv_id not found in papers: {arxiv_id}")
    paper = {
        "id": row["id"],
        "oai_datestamp": row["oai_datestamp"],
        "title": row["title"],
        "abstract": row["abstract"],
        "categories": row["categories"],
    }
    if embedded_at is None:
        embedded_at = datetime.now(UTC).isoformat()

    delete_paper(rag_conn, arxiv_id)
    embed_papers(rag_conn, [paper], embedded_at, ollama_url=ollama_url)
    rag_conn.execute("INSERT INTO papers_fts(papers_fts) VALUES('rebuild')")
    rag_conn.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Process at most N papers (smoke test).")
    parser.add_argument(
        "--batch", type=int, default=DEFAULT_BATCH, help=f"Embed in batches of N (default {DEFAULT_BATCH})."
    )
    parser.add_argument("--ollama-url", default=embedder.OLLAMA_BASE_URL)
    parser.add_argument("--reset", action="store_true", help="Wipe arxiv_rag.db before starting.")
    args = parser.parse_args(argv)

    if not paths.ARXIV_DB.exists():
        print(
            f"Error: {paths.ARXIV_DB} not found. Run `python -m arxiv.ingest` first.",
            file=sys.stderr,
        )
        return 1

    paths.DUMPS_DIR.mkdir(parents=True, exist_ok=True)
    papers_conn = connect_papers(paths.ARXIV_DB)
    rag_conn = connect_arxiv_rag(paths.ARXIV_RAG_DB)

    if args.reset:
        print("Resetting arxiv_rag.db...")
        reset_rag(rag_conn)

    embedded = load_embedded(rag_conn)
    embedded_at = datetime.now(UTC).isoformat()
    total = papers_conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    if args.limit is not None:
        total = min(total, args.limit)

    stats = {"new": 0, "updated": 0, "skipped": 0, "failed": 0}
    batch: list[tuple[dict[str, Any], str]] = []

    def flush(items: list[tuple[dict[str, Any], str]]) -> None:
        if not items:
            return
        try:
            embed_papers(rag_conn, [p for p, _ in items], embedded_at, ollama_url=args.ollama_url)
            rag_conn.commit()
            for _, action in items:
                stats[action] += 1
        except Exception as exc:
            rag_conn.rollback()
            print(f"\nBatch of {len(items)} failed: {exc}", file=sys.stderr)
            stats["failed"] += len(items)

    with tqdm(total=total, desc="embed", unit="paper") as pbar:
        try:
            for paper in iter_papers(papers_conn, limit=args.limit):
                prior = embedded.get(paper["id"])
                if prior == paper["oai_datestamp"]:
                    stats["skipped"] += 1
                    pbar.update(1)
                    continue
                action = "updated" if prior is not None else "new"
                if action == "updated":
                    delete_paper(rag_conn, paper["id"])
                batch.append((paper, action))

                if len(batch) >= args.batch:
                    flush(batch)
                    pbar.update(len(batch))
                    batch = []
            flush(batch)
            pbar.update(len(batch))
        finally:
            papers_conn.close()

    print("Rebuilding FTS5 index...")
    rag_conn.execute("INSERT INTO papers_fts(papers_fts) VALUES('rebuild')")
    rag_conn.commit()
    rag_conn.close()

    print(f"Done. new={stats['new']} updated={stats['updated']} skipped={stats['skipped']} failed={stats['failed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
