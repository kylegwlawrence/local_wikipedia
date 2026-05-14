# Consuming the External RAG API

The API is intentionally minimal — two endpoints, JSON in / JSON out, no auth. Your app just needs to be able to make HTTP requests to wherever `uvicorn app:app` is running (default `http://127.0.0.1:8000`).

## 1. Make sure the server is running

```bash
# In the local_wikipedia repo
ollama serve &                              # for dense retrieval
uvicorn app:app --host 0.0.0.0 --port 8000  # bind 0.0.0.0 if calling from another machine
```

You also need at least one `dumps/{wiki}_rag.db` built (`python -m rag.embed --wiki simplewiki`). The server reports only corpora whose RAG DB exists on disk.

## 2. Discover what's available

```http
GET /rag/info
```

Response (example):
```json
{
  "server_name": "local-wikipedia",
  "server_version": "0.1.0",
  "description": "Local Wikipedia RAG server. Hosts English and Simple English Wikipedia.",
  "embedding_model": "nomic-embed-text",
  "embedding_dim": 768,
  "default_top_k": 5,
  "max_top_k": 50,
  "article_url_template": "/article/{title}",
  "corpora": [
    { "id": "simplewiki", "display_name": "Simple English", "article_count": 123456 },
    { "id": "enwiki",     "display_name": "English",        "article_count": 6000000 }
  ]
}
```

Use `corpora[].id` as the `corpus` field in retrieval. Use `article_url_template` to build citation links — substitute `{title}` and prepend the server's base URL.

## 3. Retrieve chunks

```http
POST /rag/retrieve
Content-Type: application/json

{ "query": "what is photosynthesis", "corpus": "simplewiki", "top_k": 5 }
```

Validation rules (422 on violation):
- `query` — required, non-blank after `strip()`
- `corpus` — required; 404 if it's not one of the corpora listed by `/rag/info`
- `top_k` — optional, integer in `[1, 50]`, defaults to 5

Response:
```json
{
  "used_dense": true,
  "hits": [
    {
      "corpus": "simplewiki",
      "chunk_id": 41827,
      "page_id": 1923,
      "title": "Photosynthesis",
      "section": "Light reactions",
      "chunk_index": 2,
      "text": "Photosynthesis is the process by which …",
      "text_length": 1432,
      "score": 0.0317
    }
  ]
}
```

`used_dense: false` means Ollama was unreachable and you got sparse-only (FTS5 BM25) results — still usable, just lower-quality on natural-language questions. `score` is an RRF score; higher is better, but the absolute value isn't comparable across queries. Use it for ordering, not thresholding.

## 4. Client examples

### Python
```python
import httpx

BASE = "http://127.0.0.1:8000"

def retrieve(query: str, corpus: str = "simplewiki", top_k: int = 5):
    r = httpx.post(
        f"{BASE}/rag/retrieve",
        json={"query": query, "corpus": corpus, "top_k": top_k},
        timeout=30.0,
    )
    r.raise_for_status()
    return r.json()

hits = retrieve("how does photosynthesis work")["hits"]
for h in hits:
    print(f"[{h['score']:.3f}] {h['title']} § {h['section']}")
    print(h["text"][:200], "...\n")
```

### JavaScript / TypeScript
```ts
const BASE = "http://127.0.0.1:8000";

export async function retrieve(query: string, corpus = "simplewiki", topK = 5) {
  const res = await fetch(`${BASE}/rag/retrieve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, corpus, top_k: topK }),
  });
  if (!res.ok) throw new Error(`RAG ${res.status}: ${await res.text()}`);
  return res.json() as Promise<{ used_dense: boolean; hits: Hit[] }>;
}
```

### curl
```bash
curl -s -X POST http://127.0.0.1:8000/rag/retrieve \
  -H 'Content-Type: application/json' \
  -d '{"query":"what is photosynthesis","corpus":"simplewiki","top_k":5}' \
  | jq '.hits[] | {title, section, score}'
```

## 5. Typical RAG-app integration shape

In your chat application:

1. On startup, call `GET /rag/info` once. Cache `corpora`, `article_url_template`, and `embedding_model`.
2. On each user message, call `POST /rag/retrieve` with the message as `query`.
3. Build a prompt by concatenating `hits[].text` with a header like `"[{title} § {section}]"`, then send to your own LLM (this server does **not** generate).
4. For citations, render links as `BASE + article_url_template.replace("{title}", encodeURIComponent(hit.title))` — e.g. `http://127.0.0.1:8000/article/Photosynthesis`.

## 6. Things to know

- **No auth, no rate limits, no CORS headers.** It's designed for trusted local/LAN clients. If you call it from a browser on a different origin, add CORS middleware in `app/__init__.py`. If you expose it beyond localhost, put it behind a reverse proxy with auth.
- **No streaming.** `/rag/retrieve` is a single JSON response — there's nothing to stream because no generation happens.
- **Dense retrieval depends on Ollama.** If `ollama serve` isn't reachable, retrieval silently degrades to sparse-only and `used_dense` becomes `false`. Surface that flag in your UI if you care about quality.
- **The server only knows about corpora whose `_rag.db` exists.** If you build a new wiki, just re-call `/rag/info` — no restart needed.
- **Latency:** ~50–200 ms per query for simplewiki on local hardware; dominated by the Ollama embed call for the query (one short embed per request).

---

# Notes for an AI agent building a consumer app

This section is a handoff to whoever is building the chat / RAG-consumer app against this server. The above sections are the wire contract; this section is the *intent* behind it and the patterns that will save you a wrong turn.

## Architectural boundaries — don't cross them

- **Treat the RAG server as a black-box HTTP service.** Do not import any module from this repo into the consumer (`from rag import retriever`, `import paths`, etc.). The server is meant to run as its own process — possibly on a different machine. If your consumer lives in this same repo, still talk to it over HTTP, not in-process.
- **The server is retrieval-only.** It will never generate text. Plug it into whatever LLM you want (Ollama, Anthropic, OpenAI). The server does not know or care.
- **Don't replicate logic that lives server-side.** You do *not* need to: chunk text, manage embeddings, prepend `search_query:` to your query, manage `revision_id`, or know anything about `sqlite-vec`. All of that is the server's problem. If you find yourself reaching for the `rag/` package, you're going the wrong way.
- **Treat the article URL template as opaque.** Don't hardcode `/article/{title}`. Read `article_url_template` from `/rag/info` and substitute. The template will change before the API contract does.

## Exact response schema (Pydantic source of truth)

These mirror `app/routes/rag.py` and are the canonical types — match them in your client.

```python
class CorpusInfo:
    id: str                  # use this as the `corpus` field in /rag/retrieve
    display_name: str        # human-facing label
    article_count: int       # rows in articles_meta = embedded subset, NOT total wiki size

class ServerInfo:
    server_name: str
    server_version: str
    description: str
    embedding_model: str     # e.g. "nomic-embed-text" — informational
    embedding_dim: int       # e.g. 768 — informational
    default_top_k: int       # use this when the user didn't specify one
    max_top_k: int           # hard cap; requests above this return 422
    article_url_template: str
    corpora: list[CorpusInfo]

class Hit:
    corpus: str              # echoes the request's `corpus` — useful when fanning out
    chunk_id: int            # stable within a corpus until the next `--reset`
    page_id: int             # use to dedupe hits from the same article
    title: str               # canonical (post-redirect) title; use as-is for URL building
    section: str | None      # None for the lead section; otherwise the heading text
    chunk_index: int         # position within the section (0, 1, 2, …)
    text: str                # plain text, already stripped of wikitext markup
    text_length: int         # character count of text
    score: float             # RRF score — ordering only, not a threshold

class RetrieveResponse:
    hits: list[Hit]          # sorted descending by score
    used_dense: bool         # False ⇒ Ollama was unreachable, sparse-only results
```

### HTTP status codes you'll actually see
- `200` — success, including `hits: []` if nothing matched
- `404` — unknown `corpus` (response body lists what *is* available)
- `422` — Pydantic validation failure (blank query, `top_k` out of range, missing field)
- `5xx` — server bug or sqlite-vec extension not loaded; surface the error to the user, don't retry blindly

## Prompt assembly pattern that works

The hits give you `text` without any header. The chunks were embedded *with* `Title — Section\n\n` prepended, so the model saw context during retrieval — but the response strips it back off. **You need to re-add that context when feeding chunks to your LLM**, otherwise the LLM sees a wall of disconnected paragraphs.

```python
def format_context(hits: list[dict]) -> str:
    blocks = []
    for h in hits:
        header = h["title"]
        if h["section"]:
            header += f" § {h['section']}"
        blocks.append(f"[{header}]\n{h['text']}")
    return "\n\n---\n\n".join(blocks)

context = format_context(response["hits"])
system_prompt = (
    "Answer the user's question using only the context below. "
    "Cite sources by their bracketed title. If the answer isn't in the context, say so.\n\n"
    f"CONTEXT:\n{context}"
)
```

Why the `[Title § Section]` header is non-negotiable:
1. It restores the disambiguation the embedder had.
2. It gives the LLM a natural citation token to echo back.
3. You'll parse those citations later to render links.

## Citation rendering — title encoding is subtle

The article URL endpoint uses FastAPI's `{title:path}` converter, which means **slashes in titles must NOT be percent-encoded** (e.g. `AC/DC`). But spaces, `?`, `#`, `&`, and other reserved characters **must** be encoded. The safest approach is `urllib.parse.quote(title, safe="/")` — encode everything except `/`.

```python
from urllib.parse import quote

def article_url(base: str, template: str, title: str) -> str:
    return base.rstrip("/") + template.replace("{title}", quote(title, safe="/"))

# article_url("http://127.0.0.1:8000", "/article/{title}", "AC/DC")
# → "http://127.0.0.1:8000/article/AC/DC"
# article_url(..., "Q&A (album)")
# → "http://127.0.0.1:8000/article/Q%26A%20%28album%29"
```

Test this with a title containing both a slash and a special char before claiming it works. `AC/DC` and `Q&A (album)` are both real Wikipedia titles.

## Deduplication and reranking

A query like "photosynthesis" can return multiple chunks from the same article — `chunk_index` 0, 1, 2 of the lead, then the "Light reactions" section. For most chat UIs this is noisy. Patterns that work:

```python
def dedupe_by_article(hits: list[dict], keep_per_article: int = 2) -> list[dict]:
    seen: dict[int, int] = {}
    out = []
    for h in hits:
        count = seen.get(h["page_id"], 0)
        if count < keep_per_article:
            out.append(h)
            seen[h["page_id"]] = count + 1
    return out
```

Request `top_k` higher than you actually want (say 15) and then dedupe down to ~5 for the prompt. The server clamps at 50 so don't go above that.

**Do not threshold on `score`.** RRF scores depend on query/corpus statistics and are not comparable between queries. "Is this score good enough?" is a question you can't answer from the score alone.

## Handling the `used_dense: false` fallback

When Ollama is down, the server still returns results — just sparse-only. You have three reasonable options:

1. **Quietly serve sparse results** — cheapest UX, but quality drop is real on natural-language questions ("how does X work" type queries get worse; keyword queries are largely unaffected).
2. **Show a banner in the UI** — "Semantic search unavailable, keyword-only results." This is what most apps should do.
3. **Refuse to answer** — appropriate only if your app's value prop depends on dense retrieval (e.g. an "ask anything" assistant).

Surface `used_dense` somewhere; never silently swallow it.

## Configuration the consumer app needs

At minimum:
- `RAG_BASE_URL` — defaults to `http://127.0.0.1:8000`; allow override via env var so the server can move.
- `RAG_DEFAULT_CORPUS` — which corpus to query if the user doesn't pick one. `simplewiki` is a good default (smaller, faster, more concise articles).
- `RAG_TIMEOUT` — 30s is generous; the embed call dominates and is usually <500 ms.

Cache `/rag/info` for the lifetime of a user session, but re-fetch on each new session — corpora may have appeared since you last looked. Don't cache it process-wide forever.

## Testing the consumer without a live server

Use `httpx.MockTransport` (or `respx` if already a dep) to stub `/rag/info` and `/rag/retrieve`. Don't try to spin up the real server in tests — it needs Ollama, real wiki DBs, and `sqlite-vec`. Example:

```python
import httpx
import respx

@respx.mock
def test_chat_uses_retrieved_context():
    respx.get("http://rag/rag/info").respond(json={
        "server_name": "local-wikipedia", "server_version": "0.1.0",
        "description": "", "embedding_model": "nomic-embed-text", "embedding_dim": 768,
        "default_top_k": 5, "max_top_k": 50, "article_url_template": "/article/{title}",
        "corpora": [{"id": "simplewiki", "display_name": "Simple", "article_count": 100}],
    })
    respx.post("http://rag/rag/retrieve").respond(json={
        "used_dense": True,
        "hits": [{"corpus": "simplewiki", "chunk_id": 1, "page_id": 1,
                  "title": "Photosynthesis", "section": None, "chunk_index": 0,
                  "text": "Photosynthesis is …", "text_length": 20, "score": 0.5}],
    })
    # …call your chat code, assert it formatted the prompt correctly
```

## Common mistakes — don't make these

1. **Don't add `search_query:` to your query.** The server does this. If you add it again, you'll get worse retrieval.
2. **Don't try to filter hits by minimum score.** Use rank order, not score thresholds.
3. **Don't assume `section` is always present.** Lead-section chunks have `section: None`. Your header builder must handle this.
4. **Don't percent-encode slashes in titles.** See "Citation rendering" above. `/article/AC/DC` is intentional.
5. **Don't poll `/rag/info` on every request.** It does a `COUNT(*)` per corpus. Cache it.
6. **Don't request `top_k` higher than `max_top_k`.** You'll get a 422. Read the cap from `/rag/info`.
7. **Don't retry on 404 or 422.** They're permanent for that request. Only retry on 5xx and connection errors, with backoff.
8. **Don't import `paths`, `rag.*`, or anything else from this repo.** HTTP only.

## Suggested minimum viable consumer structure

If you're building from scratch in a sibling directory:

```
my_chat_app/
  rag_client.py     # thin httpx wrapper: get_info() + retrieve()
  prompts.py        # format_context(), system_prompt builder, citation parser
  chat.py           # orchestrates: query → retrieve → prompt → LLM → response
  ui.py             # whatever frontend (FastAPI + HTMX is consistent with this repo)
  config.py         # RAG_BASE_URL, RAG_DEFAULT_CORPUS, LLM endpoint, etc.
  tests/
    test_rag_client.py  # respx-stubbed
    test_prompts.py     # pure unit tests on formatting
```

Keep `rag_client.py` under ~100 lines. It's just two HTTP calls.

## What "done" looks like for a consumer app

A minimal but complete integration:
1. User types a question.
2. App calls `POST /rag/retrieve` with `query=question, corpus=default, top_k=10`.
3. App dedupes by `page_id` to ~5 hits.
4. App formats context with `[Title § Section]` headers.
5. App sends a system prompt (with context) + the user question to its LLM.
6. App streams the LLM response back to the user.
7. App shows the source list under the answer as clickable links built from `article_url_template`.
8. App surfaces `used_dense: false` when it happens.

That's the whole loop. Anything more sophisticated (multi-turn memory, re-querying mid-conversation, agentic loops) builds on top of that core.
