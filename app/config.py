"""Module-level constants and the Jinja2 templates instance.

Kept separate from ``app/__init__.py`` so route modules can import
``templates`` without triggering a circular import on the FastAPI instance.
"""

from fastapi.templating import Jinja2Templates

from paths import BASE_DIR

WIKI_LABELS = {"enwiki": "EnWiki", "simplewiki": "SimpleWiki"}

# Longer human-readable names used by the external RAG API's /rag/info so
# chat apps can render a corpus picker without hardcoding labels.
WIKI_DISPLAY_NAMES = {
    "enwiki": "English Wikipedia",
    "simplewiki": "Simple English Wikipedia",
}

# Cap search results so the dropdown stays manageable and the LIKE scan
# can stop early.
SEARCH_LIMIT = 20

# Cap redirect-chain following so a cycle can't hang the request. MediaWiki's
# own limit is 5 hops; matching that is conservative.
REDIRECT_MAX_HOPS = 5

EMBED_PAGE_SIZE = 50

# External RAG API (see app/routes/rag.py and CLAUDE.md).
RAG_SERVER_NAME = "local-wikipedia"
RAG_SERVER_VERSION = "0.1.0"
RAG_DESCRIPTION = "Local Wikipedia RAG server. Hosts English and Simple English Wikipedia."
RAG_ARTICLE_URL_TEMPLATE = "/article/{title}"
RAG_DEFAULT_TOP_K = 5
RAG_MAX_TOP_K = 50

templates = Jinja2Templates(directory=BASE_DIR / "templates")
