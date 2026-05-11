"""Tests for rag/embedder.py — Ollama embedding calls."""
import struct

import httpx
import pytest
import respx

from rag.embedder import (
    EMBED_MODEL,
    EMBEDDING_DIM,
    OLLAMA_BASE_URL,
    embed_text,
    embed_texts_batch,
    pack_embedding,
    unpack_embedding,
)

_FAKE_VEC = [0.1] * EMBEDDING_DIM
_FAKE_VECS = [[float(i)] * EMBEDDING_DIM for i in range(3)]


class TestEmbedText:
    @respx.mock
    def test_returns_embedding_on_success(self):
        respx.post(f"{OLLAMA_BASE_URL}/api/embeddings").mock(
            return_value=httpx.Response(200, json={"embedding": _FAKE_VEC})
        )
        result = embed_text("hello")
        assert result == _FAKE_VEC

    @respx.mock
    def test_sends_correct_model_and_prompt(self):
        route = respx.post(f"{OLLAMA_BASE_URL}/api/embeddings").mock(
            return_value=httpx.Response(200, json={"embedding": _FAKE_VEC})
        )
        embed_text("test text")
        body = route.calls[0].request.read()
        import json
        payload = json.loads(body)
        assert payload["model"] == EMBED_MODEL
        assert payload["prompt"] == "test text"

    @respx.mock
    def test_raises_after_all_retries_fail(self):
        respx.post(f"{OLLAMA_BASE_URL}/api/embeddings").mock(
            return_value=httpx.Response(503)
        )
        with pytest.raises(httpx.HTTPStatusError):
            embed_text("hello")

    @respx.mock
    def test_respects_custom_base_url(self):
        custom = "http://remotehost:11434"
        respx.post(f"{custom}/api/embeddings").mock(
            return_value=httpx.Response(200, json={"embedding": _FAKE_VEC})
        )
        result = embed_text("hello", base_url=custom)
        assert result == _FAKE_VEC


class TestEmbedTextsBatch:
    @respx.mock
    def test_returns_all_embeddings(self):
        texts = ["a", "b", "c"]
        respx.post(f"{OLLAMA_BASE_URL}/api/embed").mock(
            return_value=httpx.Response(200, json={"embeddings": _FAKE_VECS})
        )
        result = embed_texts_batch(texts)
        assert result == _FAKE_VECS
        assert len(result) == 3

    @respx.mock
    def test_sends_correct_payload(self):
        texts = ["first chunk", "second chunk"]
        route = respx.post(f"{OLLAMA_BASE_URL}/api/embed").mock(
            return_value=httpx.Response(200, json={"embeddings": _FAKE_VECS[:2]})
        )
        embed_texts_batch(texts)
        import json
        payload = json.loads(route.calls[0].request.read())
        assert payload["model"] == EMBED_MODEL
        assert payload["input"] == texts

    @respx.mock
    def test_raises_after_all_retries_fail(self):
        respx.post(f"{OLLAMA_BASE_URL}/api/embed").mock(
            return_value=httpx.Response(503)
        )
        with pytest.raises(httpx.HTTPStatusError):
            embed_texts_batch(["hello"])

    @respx.mock
    def test_single_text_works(self):
        respx.post(f"{OLLAMA_BASE_URL}/api/embed").mock(
            return_value=httpx.Response(200, json={"embeddings": [_FAKE_VEC]})
        )
        result = embed_texts_batch(["only one"])
        assert len(result) == 1
        assert result[0] == _FAKE_VEC

    @respx.mock
    def test_respects_custom_base_url(self):
        custom = "http://remotehost:11434"
        respx.post(f"{custom}/api/embed").mock(
            return_value=httpx.Response(200, json={"embeddings": _FAKE_VECS[:2]})
        )
        result = embed_texts_batch(["a", "b"], base_url=custom)
        assert result == _FAKE_VECS[:2]


class TestPackUnpack:
    def test_roundtrip(self):
        vec = [0.1, 0.2, -0.5, 1.0]
        packed = pack_embedding(vec)
        unpacked = unpack_embedding(packed)
        assert len(unpacked) == len(vec)
        for a, b in zip(unpacked, vec):
            assert abs(a - b) < 1e-6

    def test_pack_produces_correct_byte_length(self):
        vec = [0.0] * EMBEDDING_DIM
        packed = pack_embedding(vec)
        assert len(packed) == EMBEDDING_DIM * 4
