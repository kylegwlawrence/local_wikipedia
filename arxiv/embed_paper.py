"""End-to-end full-paper embed pipeline: download → render → chunk → embed.

The single entry point ``embed_one_paper`` performs every step for one
arxiv_id and writes the results into ``arxiv_rag.db``. Called by:

* ``workers/arxiv_embed.py`` for each item in a batch embed job
* ``app/routes/arxiv.py`` for direct UI triggers (future)

Idempotency is keyed on ``oai_datestamp``: if ``papers_full_meta`` already
holds the same datestamp and status=='embedded', the call is a no-op
unless ``force=True``.
"""

import pathlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

import paths
from arxiv import chunker, download, render
from rag import embedder


@dataclass(frozen=True)
class EmbedResult:
    """Outcome of an embed attempt for one paper."""

    status: str  # 'embedded' | 'no_html' | 'skipped'
    chunk_count: int
    error_message: str | None = None


def embed_one_paper(
    arxiv_id: str,
    *,
    papers_conn: sqlite3.Connection,
    rag_conn: sqlite3.Connection,
    ollama_url: str = embedder.OLLAMA_BASE_URL,
    force: bool = False,
) -> EmbedResult:
    """Download + extract + chunk + embed one paper.

    Returns:
        - ``status='embedded'`` after a successful run (``chunk_count >= 0``)
        - ``status='no_html'`` if arXiv has no HTML version (404 on
          ``arxiv.org/html/{id}``)
        - ``status='skipped'`` if the paper is already embedded at the
          current ``oai_datestamp`` and ``force=False``

    Raises:
        KeyError: ``arxiv_id`` is not in ``papers``.
        httpx.HTTPStatusError: persistent 5xx / 429 from arXiv.
    """
    row = papers_conn.execute("SELECT id, oai_datestamp, title FROM papers WHERE id = ?", (arxiv_id,)).fetchone()
    if row is None:
        raise KeyError(f"arxiv_id not found in papers: {arxiv_id}")
    title = row["title"]
    oai_datestamp = row["oai_datestamp"]

    prior = rag_conn.execute(
        "SELECT oai_datestamp, status FROM papers_full_meta WHERE arxiv_id = ?",
        (arxiv_id,),
    ).fetchone()
    if prior is not None and prior["oai_datestamp"] == oai_datestamp and prior["status"] == "embedded" and not force:
        return EmbedResult(status="skipped", chunk_count=_count_chunks(rag_conn, arxiv_id))

    _delete_chunks(rag_conn, arxiv_id)

    html_path = download.download_html(arxiv_id, force=force)
    if html_path is None:
        _upsert_full_meta(
            rag_conn,
            arxiv_id,
            oai_datestamp,
            status="no_html",
            chunk_count=0,
            html_path=None,
            markdown_path=None,
        )
        rag_conn.commit()
        return EmbedResult(status="no_html", chunk_count=0)

    markdown = render.html_to_markdown(html_path.read_text(encoding="utf-8"))
    md_path = html_path.with_suffix(".md")
    md_path.write_text(markdown, encoding="utf-8")

    chunks = chunker.chunk_paper(markdown)
    if chunks:
        texts = [embedder.format_document(title, c["section"] or None, c["text"]) for c in chunks]
        vecs = embedder.embed_texts_batch(texts, base_url=ollama_url)
        for chunk, vec in zip(chunks, vecs, strict=True):
            cur = rag_conn.execute(
                "INSERT INTO paper_chunks (arxiv_id, section, chunk_index, text, text_length) VALUES (?, ?, ?, ?, ?)",
                (arxiv_id, chunk["section"], chunk["chunk_index"], chunk["text"], len(chunk["text"])),
            )
            rag_conn.execute(
                "INSERT INTO paper_chunks_vec (rowid, embedding) VALUES (?, ?)",
                (cur.lastrowid, embedder.pack_embedding(vec)),
            )
        rag_conn.execute("INSERT INTO paper_chunks_fts(paper_chunks_fts) VALUES('rebuild')")

    _upsert_full_meta(
        rag_conn,
        arxiv_id,
        oai_datestamp,
        status="embedded",
        chunk_count=len(chunks),
        html_path=_relative(html_path),
        markdown_path=_relative(md_path),
    )
    rag_conn.commit()
    return EmbedResult(status="embedded", chunk_count=len(chunks))


def _count_chunks(rag_conn: sqlite3.Connection, arxiv_id: str) -> int:
    row = rag_conn.execute("SELECT COUNT(*) FROM paper_chunks WHERE arxiv_id = ?", (arxiv_id,)).fetchone()
    return int(row[0]) if row else 0


def _delete_chunks(rag_conn: sqlite3.Connection, arxiv_id: str) -> None:
    """Remove existing rows in ``paper_chunks`` + ``paper_chunks_vec`` for one paper.

    ``paper_chunks_fts`` is left alone — the caller rebuilds it after insert,
    which transparently drops any orphaned FTS entries via ``content='paper_chunks'``.
    """
    rag_conn.execute(
        "DELETE FROM paper_chunks_vec WHERE rowid IN (SELECT chunk_id FROM paper_chunks WHERE arxiv_id = ?)",
        (arxiv_id,),
    )
    rag_conn.execute("DELETE FROM paper_chunks WHERE arxiv_id = ?", (arxiv_id,))


def _upsert_full_meta(
    rag_conn: sqlite3.Connection,
    arxiv_id: str,
    oai_datestamp: str,
    *,
    status: str,
    chunk_count: int,
    html_path: str | None,
    markdown_path: str | None,
    error_message: str | None = None,
) -> None:
    rag_conn.execute(
        "INSERT INTO papers_full_meta (arxiv_id, oai_datestamp, status, chunk_count, "
        "html_path, markdown_path, error_message, embedded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(arxiv_id) DO UPDATE SET "
        "oai_datestamp = excluded.oai_datestamp, "
        "status = excluded.status, "
        "chunk_count = excluded.chunk_count, "
        "html_path = excluded.html_path, "
        "markdown_path = excluded.markdown_path, "
        "error_message = excluded.error_message, "
        "embedded_at = excluded.embedded_at",
        (
            arxiv_id,
            oai_datestamp,
            status,
            chunk_count,
            html_path,
            markdown_path,
            error_message,
            datetime.now(UTC).isoformat(),
        ),
    )


def _relative(p: pathlib.Path) -> str:
    """Return ``p`` relative to ``BASE_DIR`` so the stored path is portable."""
    try:
        return str(p.relative_to(paths.BASE_DIR))
    except ValueError:
        return str(p)
