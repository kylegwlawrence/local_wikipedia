"""Background worker: download → incremental refresh → FTS rebuild.

Spawned as a detached subprocess by the FastAPI app so the job survives
browser disconnects. All output goes to a per-wiki log file in dumps/.
"""
import argparse
import pathlib
import sys

# Ensure the project root is on sys.path regardless of invocation directory.
_ROOT = pathlib.Path(__file__).parent.resolve()
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import jobs
from _runner import run_worker
from download import download as downloader
from parse.cli import _find_latest_dump
from parse.refresh import refresh_dump
from parse.schema import create_schema
from paths import DUMPS_DIR, JOBS_DB, db_path_for


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wiki", required=True)
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument("--dump", type=pathlib.Path)
    args = parser.parse_args(argv)

    wiki: str = args.wiki
    job_id: int = args.job_id
    db_path = db_path_for(wiki)

    jobs_conn = jobs.connect_jobs(JOBS_DB)

    def mark_failed(error_message: str) -> None:
        jobs.update_job(jobs_conn, job_id, status="failed", error_message=error_message)

    def body() -> int:
        jobs.update_job(jobs_conn, job_id, log_path=str(DUMPS_DIR / f"{wiki}_refresh.log"))

        # --- Download --------------------------------------------------------
        jobs.update_job(jobs_conn, job_id, status="downloading")
        print(f"[worker] Downloading {wiki} dump…", flush=True)
        try:
            rc = downloader.main(["--wiki", wiki])
        except Exception as exc:
            raise RuntimeError(f"Download raised: {exc}") from exc
        if rc != 0:
            raise RuntimeError(f"Download failed (exit code {rc})")

        # --- Find dump -------------------------------------------------------
        dump_path = args.dump or _find_latest_dump(wiki, DUMPS_DIR)
        if not dump_path:
            raise RuntimeError(f"No dump file found for {wiki} after download")

        # --- Refresh parse ---------------------------------------------------
        # Mark FTS dirty before any row mutations. If the worker dies between
        # the article updates and the FTS rebuild, the lifespan hook in app.py
        # will detect this flag on next startup and rebuild.
        jobs.set_fts_dirty(jobs_conn, wiki, True)
        jobs.update_job(jobs_conn, job_id, status="parsing")
        print(f"[worker] Refreshing {wiki} database…", flush=True)
        result = refresh_dump(dump_path, db_path, job_id, JOBS_DB)

        # --- FTS rebuild -----------------------------------------------------
        jobs.update_job(jobs_conn, job_id, status="rebuilding")
        print("[worker] Rebuilding FTS5 index…", flush=True)
        wiki_conn = __import__("sqlite3").connect(db_path)
        create_schema(wiki_conn)
        wiki_conn.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
        wiki_conn.commit()
        count = wiki_conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        wiki_conn.execute(
            "INSERT OR REPLACE INTO db_metadata (key, value) VALUES ('article_count', ?)",
            (str(count),),
        )
        wiki_conn.commit()
        wiki_conn.close()
        jobs.set_fts_dirty(jobs_conn, wiki, False)

        # --- Done ------------------------------------------------------------
        jobs.update_job(
            jobs_conn,
            job_id,
            status="complete",
            articles_scanned=result["scanned"],
            articles_skipped=result["skipped"],
            articles_updated=result["updated"],
            articles_inserted=result["inserted"],
            articles_archived=result["archived"],
        )
        print(
            f"[worker] Done. scanned={result['scanned']:,} "
            f"skipped={result['skipped']:,} updated={result['updated']:,} "
            f"inserted={result['inserted']:,} archived={result['archived']:,}",
            flush=True,
        )
        return 0

    try:
        return run_worker(wiki, "refresh", mark_failed, body)
    finally:
        jobs_conn.close()


if __name__ == "__main__":
    sys.exit(main())
