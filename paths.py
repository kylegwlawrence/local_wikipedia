"""Project paths, resolved relative to this file (not the working directory)."""
import pathlib

BASE_DIR = pathlib.Path(__file__).parent.resolve()
DUMPS_DIR = BASE_DIR / "dumps"
DEFAULT_WIKI = "enwiki"


def db_path_for(wiki: str) -> pathlib.Path:
    """Default SQLite path for a wiki name (``dumps/{wiki}.db``)."""
    return DUMPS_DIR / f"{wiki}.db"
