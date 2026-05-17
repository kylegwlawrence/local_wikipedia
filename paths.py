"""Project paths, resolved relative to this file (not the working directory)."""

import os
import pathlib

BASE_DIR = pathlib.Path(__file__).parent.resolve()
DUMPS_DIR = BASE_DIR / "dumps"
DEFAULT_WIKI = "enwiki"
JOBS_DB = DUMPS_DIR / "jobs.db"
KNOWN_WIKIS: frozenset[str] = frozenset({"enwiki", "simplewiki"})

ARXIV_DB = DUMPS_DIR / "arxiv.db"
ARXIV_RAG_DB = DUMPS_DIR / "arxiv_rag.db"
ARXIV_OAI_CACHE_DIR = DUMPS_DIR / "arxiv_oai_cache"
ARXIV_PAPERS_DIR = DUMPS_DIR / "arxiv" / "papers"
ARXIV_EMBED_LOG = DUMPS_DIR / "arxiv_embed.log"

# Cap redirect-chain following so a cycle can't hang. MediaWiki's own limit is 5.
# Lives here (not app/config.py) so worker subprocesses can import it without
# pulling in FastAPI / Jinja2.
REDIRECT_MAX_HOPS = 5


def db_path_for(wiki: str) -> pathlib.Path:
    """Default SQLite path for a wiki name (``dumps/{wiki}.db``)."""
    return DUMPS_DIR / f"{wiki}.db"


def rag_db_path_for(wiki: str) -> pathlib.Path:
    """RAG database path for a wiki (``dumps/{wiki}_rag.db``)."""
    return DUMPS_DIR / f"{wiki}_rag.db"


def remote_url_for(wiki: str) -> str | None:
    """Return the configured remote base URL for ``wiki``, or ``None`` if local.

    Env var format: ``WIKI_REMOTE_<WIKI_UPPER>``, e.g.
    ``WIKI_REMOTE_ENWIKI=http://192.168.1.10:8000``. Read on each call so tests
    (and a live config reload) see the current environment. Trailing slashes
    are stripped so callers can append ``/api/v1/...`` consistently.
    """
    url = os.environ.get(f"WIKI_REMOTE_{wiki.upper()}")
    if not url:
        return None
    return url.rstrip("/")


def is_remote(wiki: str) -> bool:
    """Return ``True`` if ``wiki`` is configured to use a remote backend."""
    return remote_url_for(wiki) is not None
