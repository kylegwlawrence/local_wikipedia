"""Shared fixtures for the web app test suite.

pytest auto-loads ``conftest.py`` for tests in the same directory and its
subdirectories, so test modules can request these fixtures by name without
importing anything from here.
"""
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as web_app


# A handful of fixture articles. The wikitext is intentionally tiny but uses
# real wikitext markup so we exercise the full conversion pipeline.
FIXTURE_ARTICLES = [
    {
        "page_id": 1,
        "title": "April",
        "wikitext": (
            "'''April''' is the fourth [[month]] of the year.\n"
            "== Events ==\n"
            "* Spring begins.\n"
        ),
    },
    {
        "page_id": 2,
        "title": "Apple",
        "wikitext": "An '''apple''' is a [[fruit]].",
    },
    {
        "page_id": 3,
        "title": "Python (programming language)",
        "wikitext": "'''Python''' is a [[programming language]].",
    },
    # Single-hop redirect — content is just the redirect stub.
    {
        "page_id": 4,
        "title": "Apples",
        "wikitext": "#REDIRECT [[Apple]]",
    },
    # Two-hop redirect: tests the chain follows past one hop.
    {
        "page_id": 5,
        "title": "Pyton",
        "wikitext": "#REDIRECT [[Python (programming language)]]",
    },
    # Cyclic redirect to test the cycle guard.
    {
        "page_id": 6,
        "title": "LoopA",
        "wikitext": "#REDIRECT [[LoopB]]",
    },
    {
        "page_id": 7,
        "title": "LoopB",
        "wikitext": "#REDIRECT [[LoopA]]",
    },
]


def build_fixture_db(path: Path) -> None:
    """Create a minimal SQLite database matching the parser's schema.

    Only the columns the web app actually reads are populated; the rest get
    placeholder values so the NOT NULL constraints are satisfied.
    """
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE articles (
            page_id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            namespace INTEGER NOT NULL DEFAULT 0,
            revision_id INTEGER NOT NULL,
            parent_revision_id INTEGER,
            timestamp TEXT NOT NULL,
            contributor_username TEXT,
            contributor_id INTEGER,
            comment TEXT,
            text_bytes INTEGER,
            text_content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("CREATE INDEX idx_articles_title ON articles(title)")
    conn.executemany(
        """
        INSERT INTO articles (
            page_id, title, namespace, revision_id, timestamp,
            text_bytes, text_content
        ) VALUES (?, ?, 0, 1, '2026-01-01T00:00:00Z', ?, ?)
        """,
        [
            (a["page_id"], a["title"], len(a["wikitext"]), a["wikitext"])
            for a in FIXTURE_ARTICLES
        ],
    )
    conn.execute("""
        CREATE VIRTUAL TABLE articles_fts USING fts5(
            title,
            content=articles,
            content_rowid=page_id,
            tokenize='trigram'
        )
    """)
    conn.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()


@pytest.fixture
def wiki_db_path(tmp_path):
    """Path to a fresh hermetic articles DB, rebuilt per test."""
    db = tmp_path / "test.db"
    build_fixture_db(db)
    return db


@pytest.fixture
def client(wiki_db_path, monkeypatch):
    """``TestClient`` wired to a fresh fixture database via ``WIKI_DB``."""
    monkeypatch.setenv("WIKI_DB", str(wiki_db_path))
    with TestClient(web_app.app) as c:
        yield c


@pytest.fixture
def embed_client(wiki_db_path, tmp_path, monkeypatch):
    """``TestClient`` with embed-links plumbing isolated.

    Points ``paths.JOBS_DB`` / ``paths.BASE_DIR`` at the per-test tmp dir so
    refresh/embed job rows don't leak between tests, and stubs
    ``subprocess.Popen`` so no worker subprocess is actually spawned.
    """
    import subprocess
    import paths
    from jobs import embed as embed_jobs

    monkeypatch.setenv("WIKI_DB", str(wiki_db_path))

    jobs_db = tmp_path / "jobs.db"
    monkeypatch.setattr(paths, "JOBS_DB", jobs_db)
    # The embed-links route uses BASE_DIR to derive the log_path string; the
    # subprocess.Popen call itself is stubbed below.
    monkeypatch.setattr(paths, "BASE_DIR", tmp_path)

    spawned: list[list[str]] = []

    def fake_popen(args, **kwargs):
        spawned.append(args)

        class _P:
            pass

        return _P()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    with TestClient(web_app.app) as c:
        c.spawned = spawned
        c.jobs_db = jobs_db
        c.embed_jobs = embed_jobs
        yield c


@pytest.fixture
def crash_recovery_env(tmp_path, monkeypatch):
    """Tmp dirs + monkeypatches for the lifespan startup-recovery tests.

    Yields a dict of paths and the ``jobs``/``embed_jobs`` modules. The
    lifespan startup hook reads from ``paths.JOBS_DB`` and
    ``paths.db_path_for(...)``, so both are redirected here before the
    TestClient enters the lifespan context.
    """
    import paths
    from jobs import refresh as refresh_jobs
    from jobs import embed as embed_jobs

    dumps = tmp_path / "dumps"
    dumps.mkdir()
    jobs_db = dumps / "jobs.db"

    monkeypatch.setattr(paths, "JOBS_DB", jobs_db)
    monkeypatch.setattr(paths, "db_path_for", lambda wiki: dumps / f"{wiki}.db")

    # Fixture wiki DB so the FTS rebuild path has something to operate on.
    wiki_db_path = dumps / "enwiki.db"
    build_fixture_db(wiki_db_path)
    # WIKI_DB lets non-recovery routes find the same DB if they're exercised.
    monkeypatch.setenv("WIKI_DB", str(wiki_db_path))

    return {
        "jobs_db": jobs_db,
        "dumps": dumps,
        "wiki_db": wiki_db_path,
        "jobs": refresh_jobs,
        "embed_jobs": embed_jobs,
    }
