"""Module-level constants and the Jinja2 templates instance.

Kept separate from ``app/__init__.py`` so route modules can import
``templates`` without triggering a circular import on the FastAPI instance.
"""
from fastapi.templating import Jinja2Templates

from paths import BASE_DIR

WIKI_LABELS = {"enwiki": "EnWiki", "simplewiki": "SimpleWiki"}

# Cap search results so the dropdown stays manageable and the LIKE scan
# can stop early.
SEARCH_LIMIT = 20

# Cap redirect-chain following so a cycle can't hang the request. MediaWiki's
# own limit is 5 hops; matching that is conservative.
REDIRECT_MAX_HOPS = 5

EMBED_PAGE_SIZE = 50

templates = Jinja2Templates(directory=BASE_DIR / "templates")
