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
    """Call Ollama /api/embeddings synchronously. Returns 768 floats."""
    last_exc: Exception | None = None
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
        except Exception as exc:
            last_exc = exc
    raise RuntimeError(f"Ollama embedding failed after {_MAX_ATTEMPTS} attempts") from last_exc


async def embed_text_async(text: str, base_url: str = OLLAMA_BASE_URL) -> list[float]:
    """Async version of embed_text for use in FastAPI routes."""
    last_exc: Exception | None = None
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
        except Exception as exc:
            last_exc = exc
    raise RuntimeError(f"Ollama embedding failed after {_MAX_ATTEMPTS} attempts") from last_exc


def pack_embedding(embedding: list[float]) -> bytes:
    """Serialize a float32 vector to bytes for sqlite-vec storage."""
    return struct.pack(f"{len(embedding)}f", *embedding)


def unpack_embedding(data: bytes) -> list[float]:
    """Deserialize bytes from sqlite-vec back to a float list."""
    n = len(data) // 4
    return list(struct.unpack(f"{n}f", data))
