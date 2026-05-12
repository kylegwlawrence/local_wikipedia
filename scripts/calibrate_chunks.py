"""Measure real nomic-embed-text token counts for the chunker's output.

The chunker caps chunks at ``rag.chunker.MAX_CHUNK_CHARS`` characters. The
original comment in chunker.py claimed ~4 chars/token (an OpenAI BPE rule of
thumb), but nomic-embed-text uses a different tokenizer with roughly 1.3-1.5
chars/token on English text, so 1600-char chunks land closer to 1000-1200
tokens — well past the ~512-token quality sweet spot for the model.

This script samples articles from the wiki DB, runs them through the existing
chunker, sends each chunk to Ollama's ``/api/embed`` endpoint (which returns
``prompt_eval_count`` alongside the embedding), and prints the token-count
distribution + a suggested MAX_CHUNK_CHARS to land p95 ≈ ``--target-tokens``.

Usage:
    python -m scripts.calibrate_chunks --wiki simplewiki --sample 100
"""

import argparse
import statistics
import sys

import httpx

import db as wiki_db
from paths import db_path_for
from rag import chunker
from rag.embedder import EMBED_MODEL, OLLAMA_BASE_URL


def _token_count(text: str, base_url: str) -> int:
    """Return nomic-embed-text's actual token count for ``text``.

    Uses the newer ``/api/embed`` endpoint which exposes ``prompt_eval_count``
    in the response body. Older Ollama builds (<0.1.45) lack this field; in
    that case raises a KeyError and the script will skip and report.
    """
    resp = httpx.post(
        f"{base_url}/api/embed",
        json={"model": EMBED_MODEL, "input": text},
        timeout=60.0,
    )
    resp.raise_for_status()
    return resp.json()["prompt_eval_count"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wiki", default="simplewiki")
    parser.add_argument(
        "--sample",
        type=int,
        default=100,
        help="Number of articles to sample (default: 100)",
    )
    parser.add_argument(
        "--target-tokens",
        type=int,
        default=512,
        help="Token count to land p95 at (default: 512)",
    )
    parser.add_argument("--ollama-url", default=OLLAMA_BASE_URL)
    args = parser.parse_args(argv)

    db_path = db_path_for(args.wiki)
    if not db_path.exists():
        print(f"error: wiki DB not found: {db_path}", file=sys.stderr)
        return 1

    conn = wiki_db.connect(db_path)
    rows = conn.execute(
        "SELECT title, text_content FROM articles "
        "WHERE namespace = 0 AND length(text_content) > 500 "
        "ORDER BY RANDOM() LIMIT ?",
        (args.sample,),
    ).fetchall()
    conn.close()

    print(f"Sampling {len(rows)} articles from {args.wiki} (MAX_CHUNK_CHARS={chunker.MAX_CHUNK_CHARS})…", flush=True)

    char_counts: list[int] = []
    token_counts: list[int] = []

    for i, row in enumerate(rows, 1):
        chunks = chunker.chunk_article(row["title"], row["text_content"])
        for ch in chunks:
            text = ch["text"]
            try:
                tokens = _token_count(text, args.ollama_url)
            except (httpx.HTTPError, KeyError) as exc:
                print(f"  skip ({type(exc).__name__}): {exc}", file=sys.stderr)
                continue
            char_counts.append(len(text))
            token_counts.append(tokens)
        if i % 10 == 0:
            print(f"  …{i}/{len(rows)} articles, {len(token_counts)} chunks measured", flush=True)

    if not token_counts:
        print("error: no chunks produced", file=sys.stderr)
        return 1

    quants = statistics.quantiles(token_counts, n=20)
    p50 = int(statistics.median(token_counts))
    p95 = int(quants[18])
    p99 = int(quants[-1])
    tmax = max(token_counts)

    # Compute the chars/token ratio observed at the largest chunks (top
    # decile). That's the boundary that determines whether the cap is set
    # right — small chunks will always fit regardless.
    pairs = sorted(zip(char_counts, token_counts, strict=True), key=lambda p: -p[0])
    top = pairs[: max(10, len(pairs) // 10)]
    high_ratio = statistics.mean(c / t for c, t in top if t)

    suggested = int(args.target_tokens * high_ratio * 0.9)  # 10% headroom

    print()
    print(f"Chunks measured: {len(token_counts)}")
    print(f"Current MAX_CHUNK_CHARS: {chunker.MAX_CHUNK_CHARS}")
    print()
    print("Token count distribution:")
    print(f"  p50: {p50}")
    print(f"  p95: {p95}")
    print(f"  p99: {p99}")
    print(f"  max: {tmax}")
    print()
    print(f"Chars/token at top 10% of chunks: {high_ratio:.2f}")
    print(f"Suggested MAX_CHUNK_CHARS for p95 ≈ {args.target_tokens} tokens: {suggested}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
