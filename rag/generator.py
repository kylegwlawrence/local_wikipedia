"""Ollama chat generation with streaming for the RAG pipeline."""
import json
from collections.abc import AsyncGenerator

import httpx

from rag.embedder import OLLAMA_BASE_URL
from rag.retriever import Chunk

CHAT_MODEL = "llama3"
_CONTEXT_CAP = 6000

_SYSTEM_PROMPT = (
    "You are a helpful assistant answering questions based on Wikipedia articles. "
    "Use only the provided context to answer. "
    "If the context doesn't contain the answer, say so clearly. "
    "Keep answers concise and factual."
)


def build_prompt(question: str, chunks: list[Chunk]) -> list[dict]:
    """Build the messages list for the Ollama chat API."""
    parts = []
    total = 0
    for i, chunk in enumerate(chunks, 1):
        header = f"[{i}] {chunk.title}"
        if chunk.section:
            header += f" — {chunk.section}"
        entry = f"{header}\n{chunk.text}"
        if total + len(entry) > _CONTEXT_CAP:
            break
        parts.append(entry)
        total += len(entry)

    context = "\n\n".join(parts)
    user_content = f"{context}\n\nQuestion: {question}" if context else question
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


async def stream_response(
    messages: list[dict],
    model: str = CHAT_MODEL,
    base_url: str = OLLAMA_BASE_URL,
) -> AsyncGenerator[str, None]:
    """Stream tokens from Ollama /api/chat. Yields individual token strings."""
    payload = {"model": model, "messages": messages, "stream": True}
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", f"{base_url}/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                data = json.loads(line)
                token = data.get("message", {}).get("content", "")
                if token:
                    yield token
                if data.get("done"):
                    return
