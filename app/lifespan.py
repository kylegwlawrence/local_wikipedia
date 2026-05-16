"""FastAPI lifespan hook: clean up state left by a worker that died mid-flight.

Runs once at app startup before the first request. Two responsibilities:
  1. Mark any jobs stuck in non-terminal status as 'failed' so the UI stops
     showing perpetual progress and new requests aren't blocked.
  2. For any wiki whose FTS index was marked dirty by a crashed refresh,
     rebuild the FTS index synchronously before serving requests so search
     results don't silently return stale titles.
"""

import sqlite3
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

import paths
from jobs import embed as embed_jobs, refresh as refresh_jobs
from workers.spawn import spawn_worker


def recover_from_crash() -> None:
    conn = refresh_jobs.connect_jobs(paths.JOBS_DB)
    try:
        n_refresh = refresh_jobs.clear_orphaned_jobs(conn)
        if n_refresh:
            print(
                f"[startup] cleared {n_refresh} orphaned refresh job(s)",
                file=sys.stderr,
                flush=True,
            )
        dirty_wikis = refresh_jobs.get_fts_dirty_wikis(conn)
    finally:
        conn.close()

    embed_conn = embed_jobs.connect_embed_jobs(paths.JOBS_DB)
    try:
        n_embed = embed_jobs.clear_orphaned_jobs(embed_conn)
        if n_embed:
            print(
                f"[startup] cleared {n_embed} orphaned embed job(s)",
                file=sys.stderr,
                flush=True,
            )
        # Any queued jobs left over by a crashed dispatcher won't move on their
        # own. Promote one per wiki here so the queue resumes after restart.
        for wiki in embed_jobs.get_wikis_with_queued_jobs(embed_conn):
            nxt = embed_jobs.get_next_queued_for_wiki(embed_conn, wiki)
            if nxt is None:
                continue
            if embed_jobs.start_queued_job(embed_conn, nxt["id"]) == 0:
                continue
            print(
                f"[startup] resuming queued embed job {nxt['id']} for {wiki}",
                file=sys.stderr,
                flush=True,
            )
            spawn_worker("workers.embed", wiki, nxt["id"], "embed")
    finally:
        embed_conn.close()

    for wiki in dirty_wikis:
        db_path = paths.db_path_for(wiki)
        if not db_path.exists():
            # The wiki DB was deleted while the flag was set. Clear the flag
            # so we don't try to rebuild on every startup.
            conn = refresh_jobs.connect_jobs(paths.JOBS_DB)
            try:
                refresh_jobs.set_fts_dirty(conn, wiki, False)
            finally:
                conn.close()
            continue

        print(
            f"[startup] FTS index for {wiki} is dirty — rebuilding…",
            file=sys.stderr,
            flush=True,
        )
        wiki_conn = sqlite3.connect(db_path)
        try:
            wiki_conn.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
            wiki_conn.commit()
        finally:
            wiki_conn.close()

        conn = refresh_jobs.connect_jobs(paths.JOBS_DB)
        try:
            refresh_jobs.set_fts_dirty(conn, wiki, False)
        finally:
            conn.close()
        print(f"[startup] FTS rebuild complete for {wiki}", file=sys.stderr, flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    recover_from_crash()
    yield
