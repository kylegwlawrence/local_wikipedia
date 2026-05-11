"""Offline embedding pipeline for the RAG system.

Usage:
    python -m rag.embed --wiki simplewiki
    python -m rag.embed --wiki enwiki --limit 1000 --batch 50
    python -m rag.embed --wiki simplewiki --reset
"""
import argparse
import sys

import httpx
from tqdm import tqdm

import db as wiki_db
from paths import db_path_for, rag_db_path_for
from rag import chunker, embedder
from rag.schema import connect_rag


def _load_embedded(rag_conn) -> dict[int, int]:
    """Return {page_id: revision_id} for all already-embedded articles.

    Args:
        rag_conn: Open RAG database connection.

    Returns:
        Mapping of page_id to the revision_id stored at last embed time.
    """
    rows = rag_conn.execute("SELECT page_id, revision_id FROM articles_meta").fetchall()
    return {r["page_id"]: r["revision_id"] for r in rows}


def _delete_article(rag_conn, page_id: int) -> None:
    """Remove all chunks, vectors, FTS entries, and meta for one article.

    Args:
        rag_conn: Open RAG database connection.
        page_id: The article's page_id to delete.
    """
    rows = rag_conn.execute(
        "SELECT chunk_id, text FROM chunks WHERE page_id = ?", (page_id,)
    ).fetchall()
    if rows:
        chunk_ids = [r["chunk_id"] for r in rows]
        placeholders = ",".join("?" * len(chunk_ids))
        rag_conn.execute(
            f"DELETE FROM chunks_vec WHERE chunk_id IN ({placeholders})", chunk_ids
        )
        # chunks_fts is a content table so deletions must be issued explicitly;
        # DELETE FROM chunks alone doesn't update the FTS index.
        for r in rows:
            rag_conn.execute(
                "INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', ?, ?)",
                (r["chunk_id"], r["text"]),
            )
        rag_conn.execute(
            f"DELETE FROM chunks WHERE chunk_id IN ({placeholders})", chunk_ids
        )
    rag_conn.execute("DELETE FROM articles_meta WHERE page_id = ?", (page_id,))


def _insert_chunk(
    rag_conn,
    page_id: int,
    chunk: dict,
    vec: list[float],
    *,
    fts_incremental: bool,
) -> int:
    """Insert one chunk row, its vector, and optionally an FTS entry.

    Args:
        rag_conn: Open RAG database connection.
        page_id: The article's page_id.
        chunk: Chunk dict with keys ``section``, ``chunk_index``, and ``text``.
        vec: Embedding vector for this chunk.
        fts_incremental: If True, insert directly into chunks_fts now. If False,
            skip — the caller will do a single bulk FTS rebuild at the end.

    Returns:
        The newly assigned chunk_id.
    """
    cur = rag_conn.execute(
        "INSERT INTO chunks (page_id, section, chunk_index, text, text_length) "
        "VALUES (?, ?, ?, ?, ?)",
        (page_id, chunk["section"], chunk["chunk_index"],
         chunk["text"], len(chunk["text"])),
    )
    chunk_id = cur.lastrowid
    rag_conn.execute(
        "INSERT INTO chunks_vec (chunk_id, embedding) VALUES (?, ?)",
        (chunk_id, embedder.pack_embedding(vec)),
    )
    if fts_incremental:
        rag_conn.execute(
            "INSERT INTO chunks_fts(rowid, text) VALUES (?, ?)",
            (chunk_id, chunk["text"]),
        )
    return chunk_id


def _embed_article(rag_conn, page_id: int, title: str, revision_id: int,
                   wikitext: str, ollama_url: str) -> int:
    """Chunk and embed one article, writing results to the RAG database.

    Uses ``fts_incremental=False`` because the CLI does a bulk FTS rebuild after
    all articles are processed — far faster than per-chunk inserts at scale.

    Args:
        rag_conn: Open RAG database connection.
        page_id: Article page_id.
        title: Article title.
        revision_id: Current revision ID for incremental tracking.
        wikitext: Raw wikitext content of the article.
        ollama_url: Ollama server base URL.

    Returns:
        Number of chunks successfully inserted.
    """
    if chunker.is_redirect(wikitext):
        return 0

    categories = chunker.extract_categories(wikitext)
    chunks = chunker.chunk_article(title, wikitext)

    rag_conn.execute(
        "INSERT OR REPLACE INTO articles_meta (page_id, title, revision_id, categories) "
        "VALUES (?, ?, ?, ?)",
        (page_id, title, revision_id, "|".join(categories)),
    )

    if not chunks:
        return 0

    texts = [chunk["text"] for chunk in chunks]
    try:
        vecs = embedder.embed_texts_batch(texts, base_url=ollama_url)
    except httpx.HTTPError:
        return 0

    for chunk, vec in zip(chunks, vecs):
        _insert_chunk(rag_conn, page_id, chunk, vec, fts_incremental=False)
    return len(vecs)


def embed_one(
    rag_conn,
    page_id: int,
    title: str,
    revision_id: int,
    wikitext: str,
    ollama_url: str = embedder.OLLAMA_BASE_URL,
) -> int:
    """Chunk, embed, and store one article, syncing FTS incrementally.

    Intended for live web requests — syncs FTS5 per chunk rather than doing
    a full rebuild at the end, and commits before returning. Removes any
    existing data for the article before re-inserting.

    Args:
        rag_conn: Open RAG database connection.
        page_id: Article page_id.
        title: Article title.
        revision_id: Current revision ID for incremental tracking.
        wikitext: Raw wikitext content of the article.
        ollama_url: Ollama server base URL.

    Returns:
        Number of chunks successfully embedded. Returns 0 for redirect
        articles without modifying the RAG database.
    """
    if chunker.is_redirect(wikitext):
        return 0

    _delete_article(rag_conn, page_id)

    categories = chunker.extract_categories(wikitext)
    chunks_data = chunker.chunk_article(title, wikitext)

    rag_conn.execute(
        "INSERT OR REPLACE INTO articles_meta (page_id, title, revision_id, categories) "
        "VALUES (?, ?, ?, ?)",
        (page_id, title, revision_id, "|".join(categories)),
    )

    if not chunks_data:
        rag_conn.commit()
        return 0

    texts = [chunk["text"] for chunk in chunks_data]
    try:
        vecs = embedder.embed_texts_batch(texts, base_url=ollama_url)
    except httpx.HTTPError:
        rag_conn.commit()
        return 0

    for chunk, vec in zip(chunks_data, vecs):
        _insert_chunk(rag_conn, page_id, chunk, vec, fts_incremental=True)

    rag_conn.commit()
    return len(vecs)


def main(argv: list[str] | None = None) -> int:
    """Run the offline embedding pipeline from the command line.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]`` if None.

    Returns:
        Exit code: 0 on success, 1 if the wiki database is not found.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wiki", default="enwiki")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N articles (for testing)")
    parser.add_argument("--batch", type=int, default=100,
                        help="Commit every N articles (default: 100)")
    parser.add_argument("--ollama-url", default=embedder.OLLAMA_BASE_URL)
    parser.add_argument("--reset", action="store_true",
                        help="Delete all existing data and re-embed from scratch")
    args = parser.parse_args(argv)

    wiki_path = db_path_for(args.wiki)
    if not wiki_path.exists():
        print(f"Error: wiki database not found: {wiki_path}", file=sys.stderr)
        return 1

    rag_path = rag_db_path_for(args.wiki)
    rag_conn = connect_rag(rag_path)

    if args.reset:
        print("Resetting RAG database...")
        rag_conn.executescript("""
            DELETE FROM chunks_vec;
            DELETE FROM chunks;
            DELETE FROM articles_meta;
        """)
        rag_conn.commit()

    wiki_conn = wiki_db.connect(wiki_path)
    embedded = _load_embedded(rag_conn)

    query = "SELECT page_id, title, revision_id, text_content FROM articles WHERE namespace=0 ORDER BY page_id"
    params: list[int] = []
    if args.limit:
        query += " LIMIT ?"
        params.append(args.limit)

    rows = wiki_conn.execute(query, params).fetchall()
    wiki_conn.close()

    stats = {"skipped": 0, "embedded": 0, "updated": 0, "failed": 0}

    for i, row in enumerate(tqdm(rows, desc=f"Embedding {args.wiki}", unit="art")):
        page_id = row["page_id"]
        revision_id = row["revision_id"]

        if page_id in embedded:
            if embedded[page_id] == revision_id:
                stats["skipped"] += 1
                continue
            _delete_article(rag_conn, page_id)
            stats["updated"] += 1
        else:
            stats["embedded"] += 1

        try:
            _embed_article(
                rag_conn, page_id, row["title"], revision_id,
                row["text_content"], args.ollama_url,
            )
        except Exception as exc:
            print(f"\nFailed {row['title']!r}: {exc}", file=sys.stderr)
            stats["failed"] += 1

        if (i + 1) % args.batch == 0:
            rag_conn.commit()

    rag_conn.commit()

    print("Rebuilding FTS5 index...")
    rag_conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    rag_conn.commit()

    rag_conn.close()
    print(
        f"Done. embedded={stats['embedded']} updated={stats['updated']} "
        f"skipped={stats['skipped']} failed={stats['failed']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
