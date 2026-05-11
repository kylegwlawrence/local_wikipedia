"""Ollama embedding calls for the RAG pipeline."""
import asyncio
import struct
import time

import httpx

OLLAMA_BASE_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
EMBEDDING_DIM = 768

_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 2.0


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
            time.sleep(_BACKOFF_BASE ** attempt)
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
            await asyncio.sleep(_BACKOFF_BASE ** attempt)
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
