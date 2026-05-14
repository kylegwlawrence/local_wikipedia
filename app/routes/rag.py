"""Retrieval-only HTTP API for external chat applications.

``GET /rag/info`` advertises server identity, available corpora, and the
article URL template the chat app uses to build citation links.
``POST /rag/retrieve`` runs hybrid dense + sparse retrieval against one
corpus and returns ranked chunks.

The server is intentionally retrieval-only: no LLM generation happens here.
The chat app assembles its own prompts and calls its own Ollama instance.
See CLAUDE.md for the full wire contract.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

import paths
from app.config import (
    RAG_ARTICLE_URL_TEMPLATE,
    RAG_DEFAULT_TOP_K,
    RAG_DESCRIPTION,
    RAG_MAX_TOP_K,
    RAG_SERVER_NAME,
    RAG_SERVER_VERSION,
    WIKI_DISPLAY_NAMES,
)
from rag import embedder, retriever
from rag.schema import connect_rag

router = APIRouter()


class CorpusInfo(BaseModel):
    id: str
    display_name: str
    article_count: int


class ServerInfo(BaseModel):
    server_name: str
    server_version: str
    description: str
    embedding_model: str
    embedding_dim: int
    default_top_k: int
    max_top_k: int
    article_url_template: str
    corpora: list[CorpusInfo]


class RetrieveRequest(BaseModel):
    query: str = Field(..., min_length=1)
    corpus: str = Field(..., min_length=1)
    top_k: int = Field(default=RAG_DEFAULT_TOP_K, ge=1, le=RAG_MAX_TOP_K)

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("query must not be empty or whitespace-only")
        return stripped


class Hit(BaseModel):
    corpus: str
    chunk_id: int
    page_id: int
    title: str
    section: str | None
    chunk_index: int
    text: str
    text_length: int
    score: float


class RetrieveResponse(BaseModel):
    hits: list[Hit]
    used_dense: bool


def _available_corpora() -> list[str]:
    """Return wikis whose RAG DB exists on disk, in deterministic order."""
    return sorted(w for w in paths.KNOWN_WIKIS if paths.rag_db_path_for(w).exists())


@router.get("/rag/info")
def rag_info() -> ServerInfo:
    """Server identity + the list of corpora the chat app can query.

    Each corpus's ``article_count`` is the count of rows in ``articles_meta``
    on the RAG DB — i.e. the embedded subset actually retrievable, not the
    wiki's total article count.
    """
    corpora = []
    for wiki in _available_corpora():
        conn = connect_rag(paths.rag_db_path_for(wiki))
        try:
            article_count = conn.execute("SELECT COUNT(*) FROM articles_meta").fetchone()[0]
        finally:
            conn.close()
        corpora.append(
            CorpusInfo(
                id=wiki,
                display_name=WIKI_DISPLAY_NAMES.get(wiki, wiki),
                article_count=article_count,
            )
        )
    return ServerInfo(
        server_name=RAG_SERVER_NAME,
        server_version=RAG_SERVER_VERSION,
        description=RAG_DESCRIPTION,
        embedding_model=embedder.EMBED_MODEL,
        embedding_dim=embedder.EMBEDDING_DIM,
        default_top_k=RAG_DEFAULT_TOP_K,
        max_top_k=RAG_MAX_TOP_K,
        article_url_template=RAG_ARTICLE_URL_TEMPLATE,
        corpora=corpora,
    )


@router.post("/rag/retrieve")
def rag_retrieve(req: RetrieveRequest) -> RetrieveResponse:
    """Hybrid dense + sparse retrieval against the requested corpus.

    Raises:
        HTTPException: 404 when ``corpus`` is not a configured wiki or has no
            RAG DB on disk. 422 (via Pydantic) for empty/whitespace query or
            out-of-range top_k.
    """
    available = _available_corpora()
    if req.corpus not in available:
        raise HTTPException(
            status_code=404,
            detail=f"corpus '{req.corpus}' not found; available: {available}",
        )

    conn = connect_rag(paths.rag_db_path_for(req.corpus))
    try:
        result = retriever.retrieve(req.query, conn, top_k=req.top_k)
    finally:
        conn.close()

    hits = [
        Hit(
            corpus=req.corpus,
            chunk_id=h.chunk_id,
            page_id=h.page_id,
            title=h.title,
            section=h.section,
            chunk_index=h.chunk_index,
            text=h.text,
            text_length=h.text_length,
            score=h.score,
        )
        for h in result.hits
    ]
    return RetrieveResponse(hits=hits, used_dense=result.used_dense)
