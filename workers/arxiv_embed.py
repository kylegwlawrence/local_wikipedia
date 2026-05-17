"""Background worker that drains an arXiv full-paper embed job queue.

Spawned as a detached subprocess by ``POST /arxiv/{id}/embed-paper``. Loops
over ``arxiv_embed_job_items`` rows for the given job, calling
``arxiv.embed_paper.embed_one_paper`` for each one and updating per-item
status. Checks the job's ``cancel_requested`` flag between items so a
long-running batch can be stopped cleanly.

Invoke as ``python -m workers.arxiv_embed --job-id N`` from the project root.
"""

import argparse
import sys

import paths
from arxiv import jobs as arxiv_jobs
from arxiv.embed_paper import embed_one_paper
from arxiv.schema import connect_arxiv_rag, connect_papers
from workers.runner import run_worker


def _process_item(item, jobs_conn, papers_conn, rag_conn) -> None:
    """Embed one queued item and update its status row."""
    item_id = item["id"]
    arxiv_id = item["arxiv_id"]
    arxiv_jobs.update_item(jobs_conn, item_id, "in_progress")
    try:
        result = embed_one_paper(arxiv_id, papers_conn=papers_conn, rag_conn=rag_conn)
    except KeyError as exc:
        arxiv_jobs.update_item(jobs_conn, item_id, "not_found", error_message=str(exc))
        return
    except Exception as exc:
        arxiv_jobs.update_item(jobs_conn, item_id, "failed", error_message=f"{type(exc).__name__}: {exc}")
        return
    arxiv_jobs.update_item(
        jobs_conn,
        item_id,
        result.status,
        chunk_count=result.chunk_count,
        error_message=result.error_message,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # ``--wiki`` accepted (default "arxiv") for compatibility with the shared
    # ``workers.spawn.spawn_worker`` helper, which always passes it. The arxiv
    # worker doesn't have a wiki dimension and the value is only used for the
    # log file path.
    parser.add_argument("--wiki", default="arxiv")
    parser.add_argument("--job-id", type=int, required=True)
    args = parser.parse_args(argv)
    job_id: int = args.job_id

    jobs_conn = arxiv_jobs.connect_arxiv_jobs(paths.JOBS_DB)

    def mark_failed(error_message: str) -> None:
        arxiv_jobs.mark_job(jobs_conn, job_id, "failed", error_message=error_message)

    def body() -> int:
        papers_conn = connect_papers(paths.ARXIV_DB)
        rag_conn = connect_arxiv_rag(paths.ARXIV_RAG_DB)
        try:
            print(f"[arxiv_embed] job {job_id} started", flush=True)
            while True:
                job = arxiv_jobs.get_job(jobs_conn, job_id)
                if job is None:
                    print(f"[arxiv_embed] job {job_id} disappeared", flush=True)
                    return 1
                if job["cancel_requested"]:
                    arxiv_jobs.mark_job(jobs_conn, job_id, "cancelled")
                    print(f"[arxiv_embed] job {job_id} cancelled", flush=True)
                    return 0
                item = arxiv_jobs.get_next_queued(jobs_conn, job_id)
                if item is None:
                    arxiv_jobs.mark_job(jobs_conn, job_id, "complete")
                    print(f"[arxiv_embed] job {job_id} complete", flush=True)
                    return 0
                print(
                    f"[arxiv_embed] processing {item['arxiv_id']!r} (item {item['id']})",
                    flush=True,
                )
                _process_item(item, jobs_conn, papers_conn, rag_conn)
                arxiv_jobs.touch_job(jobs_conn, job_id)
        finally:
            papers_conn.close()
            rag_conn.close()

    try:
        return run_worker("arxiv", "embed", mark_failed, body)
    finally:
        jobs_conn.close()


if __name__ == "__main__":
    sys.exit(main())
