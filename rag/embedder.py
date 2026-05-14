"""Ollama embedding calls for the RAG pipeline."""

import asyncio
import struct
import time

import httpx

OLLAMA_BASE_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
EMBEDDING_DIM = 768

# nomic-embed-text is trained with explicit task prefixes; using them is
# required for best retrieval quality. Indexed passages get
# ``search_document:`` and user queries get ``search_query:``. Documents and
# queries embedded without these prefixes land in slightly different parts of
# the vector space and produce noticeably worse cosine matches.
EMBED_DOC_PREFIX = "search_document: "
EMBED_QUERY_PREFIX = "search_query: "

_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 2.0


def format_document(title: str, section: str | None, text: str) -> str:
    """Build the indexing-time string for a chunk.

    Prepends the ``search_document:`` task prefix nomic-embed-text expects,
    then a short header with the article title (and section heading when
    present) so the chunk vector encodes self-contained provenance instead of
    a fragment that depends on neighbouring chunks for context.

    Args:
        title: Source article title.
        section: Section heading containing the chunk, or None for the lead.
        text: Plain-text chunk content (the same string stored verbatim in
            ``chunks.text``).

    Returns:
        Prefixed string suitable for passing to ``embed_text`` or
        ``embed_texts_batch`` at index time.
    """
    header = f"{title} - {section}" if section else title
    return f"{EMBED_DOC_PREFIX}{header}\n\n{text}"


def format_query(query: str) -> str:
    """Apply the ``search_query:`` prefix to a user query before embedding."""
    return f"{EMBED_QUERY_PREFIX}{query}"


def embed_text(text: str, base_url: str = OLLAMA_BASE_URL) -> list[float]:
    """Call Ollama /api/embeddings synchronously and return the embedding vector.

    Retries up to ``_MAX_ATTEMPTS`` times with exponential backoff on HTTP errors.

    Args:
        text: The text to embed.
        base_url: Ollama server base URL.

    Returns:
        List of ``EMBEDDING_DIM`` floats representing the embedding vector.

    Raises:
        httpx.HTTPError: If all retry attempts fail.
    """
    for attempt in range(_MAX_ATTEMPTS):
        if attempt:
            time.sleep(_BACKOFF_BASE**attempt)
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(
                    f"{base_url}/api/embeddings",
                    json={"model": EMBED_MODEL, "prompt": text},
                )
                resp.raise_for_status()
                return resp.json()["embedding"]
        except httpx.HTTPError:
            if attempt == _MAX_ATTEMPTS - 1:
                raise


async def embed_text_async(text: str, base_url: str = OLLAMA_BASE_URL) -> list[float]:
    """Async version of embed_text for use in FastAPI routes.

    Retries up to ``_MAX_ATTEMPTS`` times with exponential backoff on HTTP errors.

    Args:
        text: The text to embed.
        base_url: Ollama server base URL.

    Returns:
        List of ``EMBEDDING_DIM`` floats representing the embedding vector.

    Raises:
        httpx.HTTPError: If all retry attempts fail.
    """
    for attempt in range(_MAX_ATTEMPTS):
        if attempt:
            await asyncio.sleep(_BACKOFF_BASE**attempt)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{base_url}/api/embeddings",
                    json={"model": EMBED_MODEL, "prompt": text},
                )
                resp.raise_for_status()
                return resp.json()["embedding"]
        except httpx.HTTPError:
            if attempt == _MAX_ATTEMPTS - 1:
                raise


def embed_texts_batch(texts: list[str], base_url: str = OLLAMA_BASE_URL) -> list[list[float]]:
    """Call Ollama /api/embed with multiple texts and return all embedding vectors.

    Uses the batch endpoint so N texts require only one HTTP round-trip instead of N.
    Retries up to ``_MAX_ATTEMPTS`` times with exponential backoff on HTTP errors.

    Args:
        texts: List of texts to embed.
        base_url: Ollama server base URL.

    Returns:
        List of embedding vectors (one per input text), each a list of floats.

    Raises:
        httpx.HTTPError: If all retry attempts fail.
    """
    for attempt in range(_MAX_ATTEMPTS):
        if attempt:
            time.sleep(_BACKOFF_BASE**attempt)
        try:
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(
                    f"{base_url}/api/embed",
                    json={"model": EMBED_MODEL, "input": texts},
                )
                resp.raise_for_status()
                return resp.json()["embeddings"]
        except httpx.HTTPError:
            if attempt == _MAX_ATTEMPTS - 1:
                raise


def pack_embedding(embedding: list[float]) -> bytes:
    """Serialize a float32 vector to raw bytes for sqlite-vec storage.

    Args:
        embedding: List of floats to serialize.

    Returns:
        Byte string of packed float32 values (4 bytes each).
    """
    return struct.pack(f"{len(embedding)}f", *embedding)


def unpack_embedding(data: bytes) -> list[float]:
    """Deserialize raw bytes from sqlite-vec back to a float list.

    Args:
        data: Packed float32 bytes as returned by sqlite-vec.

    Returns:
        List of floats reconstructed from the byte string.
    """
    n = len(data) // 4
    return list(struct.unpack(f"{n}f", data))
