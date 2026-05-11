"""Hybrid dense + sparse retrieval for the RAG pipeline.

Dense search uses sqlite-vec ANN on chunk embeddings.
Sparse search uses FTS5 BM25 on chunk text.
Results are merged with Reciprocal Rank Fusion (RRF).
"""
import sqlite3
from dataclasses import dataclass
from typing import NamedTuple

import httpx

from rag import embedder


@dataclass
class Chunk:
    """A single retrieved text chunk with its provenance and relevance score.

    Attributes:
        chunk_id: Primary key in the chunks table.
        page_id: ID of the source article.
        title: Title of the source article.
        section: Section heading within the article, or None for the lead.
        text: Plain text content of the chunk.
        score: RRF relevance score (higher is more relevant).
    """

    chunk_id: int
    page_id: int
    title: str
    section: str | None
    text: str
    score: float


class RetrievalResult(NamedTuple):
    """Return value of ``retrieve()``.

    Attributes:
        hits: Retrieved chunks sorted by descending RRF score.
        used_dense: True when the dense (Ollama) embedding succeeded.
            False when Ollama was unreachable and results are sparse-only.
    """

    hits: list[Chunk]
    used_dense: bool


def retrieve(
    query: str,
    rag_conn: sqlite3.Connection,
    top_k: int = 5,
    candidate_k: int = 50,
    rrf_k: int = 60,
    ollama_url: str = embedder.OLLAMA_BASE_URL,
) -> RetrievalResult:
    """Retrieve the most relevant chunks for a query using hybrid search.

    Combines dense ANN search (sqlite-vec) and sparse FTS5 search, merged
    with Reciprocal Rank Fusion. Falls back to sparse-only if Ollama is
    unreachable.

    Args:
        query: Natural language query string.
        rag_conn: Open RAG database connection with sqlite-vec loaded.
        top_k: Number of chunks to return.
        candidate_k: Number of candidates to collect from each search method
            before merging.
        rrf_k: RRF smoothing constant (score = 1 / (rrf_k + rank)).
        ollama_url: Ollama server base URL for dense embedding.

    Returns:
        RetrievalResult with hits sorted by descending RRF score and a
        used_dense flag indicating whether Ollama embedding succeeded.
    """
    if not query.strip():
        return RetrievalResult(hits=[], used_dense=False)

    sparse = _sparse_search(query, rag_conn, candidate_k)

    dense: list[tuple[int, float]] = []
    used_dense = False
    try:
        vec = embedder.embed_text(query, base_url=ollama_url)
        dense = _dense_search(vec, rag_conn, candidate_k)
        used_dense = True
    except httpx.HTTPError:
        pass  # sparse-only fallback

    merged = _rrf_merge(dense, sparse, k=rrf_k)[:top_k]
    if not merged:
        return RetrievalResult(hits=[], used_dense=used_dense)

    chunk_ids = [cid for cid, _ in merged]
    score_map = {cid: score for cid, score in merged}
    hydrated = _fetch_chunks(chunk_ids, score_map, rag_conn)
    hits = [hydrated[cid] for cid in chunk_ids if cid in hydrated]
    return RetrievalResult(hits=hits, used_dense=used_dense)


def _dense_search(
    query_embedding: list[float],
    rag_conn: sqlite3.Connection,
    k: int,
) -> list[tuple[int, float]]:
    """Run ANN search against chunks_vec and return (chunk_id, distance) pairs.

    Args:
        query_embedding: Query vector as a list of floats.
        rag_conn: Open RAG database connection with sqlite-vec loaded.
        k: Number of nearest neighbors to retrieve.

    Returns:
        List of (chunk_id, distance) tuples; lower distance means more similar.
    """
    packed = embedder.pack_embedding(query_embedding)
    rows = rag_conn.execute(
        "SELECT chunk_id, distance FROM chunks_vec WHERE embedding MATCH ? AND k = ?",
        (packed, k),
    ).fetchall()
    return [(r["chunk_id"], r["distance"]) for r in rows]


def _sparse_search(
    query: str,
    rag_conn: sqlite3.Connection,
    k: int,
) -> list[tuple[int, float]]:
    """Run FTS5 BM25 search against chunks_fts and return (chunk_id, rank) pairs.

    Each query word is quoted individually so FTS5 applies AND-of-terms logic
    rather than phrase matching, which improves recall on natural-language queries.
    Phrase quoting would only match verbatim adjacent sequences.

    Args:
        query: Natural language query string.
        rag_conn: Open RAG database connection.
        k: Maximum number of results to return.

    Returns:
        List of (chunk_id, rank) tuples. Rank is the negative BM25 score
        (FTS5 convention), so lower values indicate stronger matches.
    """
    words = query.split()
    if not words:
        return []
    escaped = " ".join('"' + w.replace('"', "") + '"' for w in words)
    try:
        rows = rag_conn.execute(
            "SELECT rowid, rank FROM chunks_fts WHERE chunks_fts MATCH ? LIMIT ?",
            (escaped, k),
        ).fetchall()
        return [(r["rowid"], r["rank"]) for r in rows]
    except sqlite3.OperationalError:
        return []


def _rrf_merge(
    dense: list[tuple[int, float]],
    sparse: list[tuple[int, float]],
    k: int = 60,
) -> list[tuple[int, float]]:
    """Merge dense and sparse result lists with Reciprocal Rank Fusion.

    Each chunk's RRF score is the sum of 1 / (k + rank) across every list it
    appears in. A chunk present in both lists scores higher than one in only one.

    Args:
        dense: (chunk_id, distance) pairs from dense search, ordered ascending.
        sparse: (chunk_id, rank) pairs from sparse search, ordered by BM25.
        k: Smoothing constant; higher values reduce the influence of top ranks.

    Returns:
        List of (chunk_id, rrf_score) tuples sorted by descending score.
    """
    scores: dict[int, float] = {}
    for rank, (chunk_id, _) in enumerate(dense, 1):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    for rank, (chunk_id, _) in enumerate(sparse, 1):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def _fetch_chunks(
    chunk_ids: list[int],
    score_map: dict[int, float],
    rag_conn: sqlite3.Connection,
) -> dict[int, Chunk]:
    """Hydrate a list of chunk IDs into Chunk objects joined with articles_meta.

    Args:
        chunk_ids: List of chunk_id values to fetch.
        score_map: Mapping of chunk_id to RRF score.
        rag_conn: Open RAG database connection.

    Returns:
        Dict mapping chunk_id to the corresponding Chunk object.
        Chunk IDs not found in the database are omitted.
    """
    if not chunk_ids:
        return {}
    placeholders = ",".join("?" * len(chunk_ids))
    rows = rag_conn.execute(
        f"SELECT c.chunk_id, c.page_id, c.section, c.text, am.title "
        f"FROM chunks c JOIN articles_meta am USING(page_id) "
        f"WHERE c.chunk_id IN ({placeholders})",
        chunk_ids,
    ).fetchall()
    return {
        r["chunk_id"]: Chunk(
            chunk_id=r["chunk_id"],
            page_id=r["page_id"],
            title=r["title"],
            section=r["section"],
            text=r["text"],
            score=score_map.get(r["chunk_id"], 0.0),
        )
        for r in rows
    }
