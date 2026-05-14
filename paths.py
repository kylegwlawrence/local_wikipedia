"""Project paths, resolved relative to this file (not the working directory)."""

import pathlib

BASE_DIR = pathlib.Path(__file__).parent.resolve()
DUMPS_DIR = BASE_DIR / "dumps"
DEFAULT_WIKI = "enwiki"
JOBS_DB = DUMPS_DIR / "jobs.db"
KNOWN_WIKIS: frozenset[str] = frozenset({"enwiki", "simplewiki"})

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
