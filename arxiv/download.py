"""Download arXiv-rendered HTML for one paper.

arXiv serves a LaTeXML-rendered HTML5 version at ``arxiv.org/html/{id}``
for most papers. This module fetches that page, caches it under
``ARXIV_PAPERS_DIR``, and returns the cache path. A 404 means arXiv has
no HTML for this paper — the worker records ``papers_full_meta.status =
'no_html'`` and skips. Persistent 5xx / 429 raises after retry exhaustion.

The HTML is not parsed here — that's ``arxiv/render.py``.
"""

import pathlib
import time
from collections.abc import Callable

import httpx

import paths

HTML_URL_TEMPLATE = "https://arxiv.org/html/{arxiv_id}"
USER_AGENT = "local_wikipedia/0.1 (mailto:kylegwlawrence@gmail.com)"
REQUEST_TIMEOUT = 60.0
MAX_ATTEMPTS = 3
BACKOFF_BASE = 5.0
MIN_REQUEST_INTERVAL = 3.0  # arXiv asks bulk fetchers to wait ≥ 3s between requests


def html_cache_path(arxiv_id: str) -> pathlib.Path:
    """Where the cached HTML lives for ``arxiv_id`` (does not have to exist yet)."""
    return paths.ARXIV_PAPERS_DIR / f"{arxiv_id}.html"


def download_html(
    arxiv_id: str,
    *,
    force: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> pathlib.Path | None:
    """Fetch ``arxiv.org/html/{id}`` into the per-paper cache.

    Returns the cached path, or ``None`` if arXiv responded 404 (no HTML
    version exists for this paper). Cached files are reused unless
    ``force=True``. Persistent 429 / 5xx raise ``httpx.HTTPStatusError``
    after ``MAX_ATTEMPTS``.
    """
    cache_path = html_cache_path(arxiv_id)
    if cache_path.exists() and not force:
        return cache_path

    url = HTML_URL_TEMPLATE.format(arxiv_id=arxiv_id)
    text = _get_with_retry(url, sleep=sleep)
    if text is None:
        return None

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    try:
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(cache_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    sleep(MIN_REQUEST_INTERVAL)
    return cache_path


def _get_with_retry(url: str, sleep: Callable[[float], None]) -> str | None:
    """GET ``url``. Returns body text, ``None`` on 404, raises on persistent failure."""
    headers = {"User-Agent": USER_AGENT}
    for attempt in range(MAX_ATTEMPTS):
        with httpx.Client(timeout=REQUEST_TIMEOUT, headers=headers, follow_redirects=True) as client:
            resp = client.get(url)
        if resp.status_code == 404:
            return None
        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            if attempt == MAX_ATTEMPTS - 1:
                resp.raise_for_status()
            retry_after = resp.headers.get("Retry-After", "")
            wait = (
                float(retry_after)
                if retry_after and retry_after.replace(".", "", 1).isdigit()
                else BACKOFF_BASE * (attempt + 1)
            )
            sleep(wait)
            continue
        resp.raise_for_status()
        return resp.text
    raise RuntimeError("unreachable: MAX_ATTEMPTS exhausted without raising")
