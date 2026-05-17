"""Hybrid dense + sparse retrieval over the arXiv RAG database.

Mirrors ``rag/retriever.py`` in structure (sqlite-vec ANN ⊕ FTS5 BM25 ⊕
Reciprocal Rank Fusion) but returns whole-paper rows joined from
``arxiv.db.papers`` instead of chunk rows. Falls back to sparse-only when
Ollama is unreachable.
"""

import json
import sqlite3
from dataclasses import dataclass
from typing import NamedTuple

import httpx

from rag import embedder


@dataclass
class Paper:
    """One retrieved paper with metadata and RRF relevance score."""

    arxiv_id: str
    title: str
    authors: list[str]
    abstract: str
    categories: str
    primary_category: str
    submitted_date: str
    updated_date: str | None
    doi: str | None
    journal_ref: str | None
    score: float


class RetrievalResult(NamedTuple):
    """Return value of ``retrieve()``."""

    hits: list[Paper]
    used_dense: bool


def retrieve(
    query: str,
    rag_conn: sqlite3.Connection,
    papers_conn: sqlite3.Connection,
    top_k: int = 20,
    candidate_k: int = 50,
    rrf_k: int = 60,
    ollama_url: str = embedder.OLLAMA_BASE_URL,
) -> RetrievalResult:
    """Retrieve the most relevant papers for ``query`` via hybrid search.

    Args:
        query: Natural-language query string.
        rag_conn: Open ``arxiv_rag.db`` connection (sqlite-vec loaded).
        papers_conn: Open ``arxiv.db`` connection.
        top_k: Number of papers to return.
        candidate_k: Candidates collected from each method before RRF.
        rrf_k: RRF smoothing constant.
        ollama_url: Ollama URL for the dense embedding.
    """
    if not query.strip():
        return RetrievalResult(hits=[], used_dense=False)

    sparse = _sparse_search(query, rag_conn, candidate_k)

    dense: list[tuple[int, float]] = []
    used_dense = False
    try:
        vec = embedder.embed_text(embedder.format_query(query), base_url=ollama_url)
        dense = _dense_search(vec, rag_conn, candidate_k)
        used_dense = True
    except httpx.HTTPError:
        pass

    merged = _rrf_merge(dense, sparse, k=rrf_k)[:top_k]
    if not merged:
        return RetrievalResult(hits=[], used_dense=used_dense)

    rowids = [rowid for rowid, _ in merged]
    score_map = {rowid: score for rowid, score in merged}
    hits = _hydrate(rowids, score_map, rag_conn, papers_conn)
    return RetrievalResult(hits=hits, used_dense=used_dense)


def _dense_search(
    query_embedding: list[float],
    rag_conn: sqlite3.Connection,
    k: int,
) -> list[tuple[int, float]]:
    """Run sqlite-vec ANN search on ``papers_vec``; return ``(rowid, distance)``."""
    packed = embedder.pack_embedding(query_embedding)
    rows = rag_conn.execute(
        "SELECT rowid, distance FROM papers_vec WHERE embedding MATCH ? AND k = ?",
        (packed, k),
    ).fetchall()
    return [(r["rowid"], r["distance"]) for r in rows]


def _sparse_search(
    query: str,
    rag_conn: sqlite3.Connection,
    k: int,
) -> list[tuple[int, float]]:
    """FTS5 BM25 search on ``papers_fts``; return ``(rowid, rank)``.

    Per-word quoting (``"w1" "w2"``) forces FTS5 into AND-of-terms mode so
    natural-language queries don't fail when no exact phrase exists.
    """
    words = query.split()
    if not words:
        return []
    escaped = " ".join('"' + w.replace('"', "") + '"' for w in words)
    try:
        rows = rag_conn.execute(
            "SELECT rowid, rank FROM papers_fts WHERE papers_fts MATCH ? LIMIT ?",
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
    """Reciprocal Rank Fusion: ``score = Σ 1/(k + rank)`` across both lists."""
    scores: dict[int, float] = {}
    for rank, (rowid, _) in enumerate(dense, 1):
        scores[rowid] = scores.get(rowid, 0.0) + 1.0 / (k + rank)
    for rank, (rowid, _) in enumerate(sparse, 1):
        scores[rowid] = scores.get(rowid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def _hydrate(
    rowids: list[int],
    score_map: dict[int, float],
    rag_conn: sqlite3.Connection,
    papers_conn: sqlite3.Connection,
) -> list[Paper]:
    """Look up ``papers_meta.arxiv_id`` then the full ``papers`` row, preserving order."""
    if not rowids:
        return []

    placeholders = ",".join("?" * len(rowids))
    meta_rows = rag_conn.execute(
        f"SELECT rowid, arxiv_id FROM papers_meta WHERE rowid IN ({placeholders})",
        rowids,
    ).fetchall()
    rowid_to_arxiv = {r["rowid"]: r["arxiv_id"] for r in meta_rows}
    ordered_arxiv_ids = [rowid_to_arxiv[r] for r in rowids if r in rowid_to_arxiv]
    if not ordered_arxiv_ids:
        return []

    placeholders = ",".join("?" * len(ordered_arxiv_ids))
    paper_rows = papers_conn.execute(
        "SELECT id, title, authors, abstract, categories, primary_category, "
        "submitted_date, updated_date, doi, journal_ref "
        f"FROM papers WHERE id IN ({placeholders})",
        ordered_arxiv_ids,
    ).fetchall()
    papers_by_id = {r["id"]: r for r in paper_rows}

    out: list[Paper] = []
    for rowid in rowids:
        arxiv_id = rowid_to_arxiv.get(rowid)
        if arxiv_id is None:
            continue
        row = papers_by_id.get(arxiv_id)
        if row is None:
            continue
        out.append(
            Paper(
                arxiv_id=arxiv_id,
                title=row["title"],
                authors=json.loads(row["authors"]),
                abstract=row["abstract"],
                categories=row["categories"],
                primary_category=row["primary_category"],
                submitted_date=row["submitted_date"],
                updated_date=row["updated_date"],
                doi=row["doi"],
                journal_ref=row["journal_ref"],
                score=score_map.get(rowid, 0.0),
            )
        )
    return out
