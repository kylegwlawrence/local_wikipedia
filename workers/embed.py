"""Background worker that drains an embed-links job queue serially.

Spawned as a detached subprocess by ``POST /embed-links/{title}``. Loops over
``embed_job_items`` rows for the given job, calling ``rag.embed.embed_one`` for
each one and updating per-item status. Checks the job's ``cancel_requested``
flag between items so a long-running batch can be stopped cleanly.

Invoke as ``python -m workers.embed --wiki WIKI --job-id N`` from the
project root.
"""
import argparse
import sys

import db as wiki_db
import embed_jobs
from paths import JOBS_DB, db_path_for, rag_db_path_for
from rag import chunker
from rag.embed import embed_one
from rag.schema import connect_rag
from workers.runner import run_worker


def _process_item(
    item,
    jobs_conn,
    wiki_conn,
    rag_conn,
    already_embedded: dict[int, int],
) -> None:
    """Embed one queued item and update its status row.

    ``already_embedded`` is a ``{page_id: revision_id}`` snapshot loaded once
    at worker startup, used to skip articles whose embedded revision matches
    the current wiki-DB revision.
    """
    title = item["title"]
    item_id = item["id"]

    embed_jobs.update_item(jobs_conn, item_id, "in_progress")

    row = wiki_conn.execute(
        "SELECT page_id, title, revision_id, text_content "
        "FROM articles WHERE title = ?",
        (title,),
    ).fetchone()
    if row is None:
        embed_jobs.update_item(
            jobs_conn, item_id, "not_found",
            error_message=f"No article with title {title!r}",
        )
        return

    page_id = row["page_id"]
    revision_id = row["revision_id"]
    wikitext = row["text_content"]

    if chunker.is_redirect(wikitext):
        embed_jobs.update_item(jobs_conn, item_id, "skipped_redirect")
        return

    if already_embedded.get(page_id) == revision_id:
        embed_jobs.update_item(jobs_conn, item_id, "skipped_unchanged")
        return

    try:
        chunk_count = embed_one(
            rag_conn, page_id, row["title"], revision_id, wikitext,
        )
    except Exception as exc:
        embed_jobs.update_item(
            jobs_conn, item_id, "failed",
            error_message=f"{type(exc).__name__}: {exc}",
        )
        return

    already_embedded[page_id] = revision_id
    embed_jobs.update_item(
        jobs_conn, item_id, "complete", chunk_count=chunk_count,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wiki", required=True)
    parser.add_argument("--job-id", type=int, required=True)
    args = parser.parse_args(argv)

    wiki: str = args.wiki
    job_id: int = args.job_id

    jobs_conn = embed_jobs.connect_embed_jobs(JOBS_DB)

    def mark_failed(error_message: str) -> None:
        embed_jobs.mark_job(jobs_conn, job_id, "failed", error_message=error_message)

    def body() -> int:
        wiki_conn = wiki_db.connect(db_path_for(wiki))
        rag_conn = None
        try:
            rag_conn = connect_rag(rag_db_path_for(wiki))

            already_embedded = {
                r["page_id"]: r["revision_id"]
                for r in rag_conn.execute(
                    "SELECT page_id, revision_id FROM articles_meta"
                ).fetchall()
            }

            print(f"[embed_worker] job {job_id} started for {wiki}", flush=True)

            while True:
                job = embed_jobs.get_job(jobs_conn, job_id)
                if job is None:
                    print(f"[embed_worker] job {job_id} disappeared", flush=True)
                    return 1
                if job["cancel_requested"]:
                    embed_jobs.mark_job(jobs_conn, job_id, "cancelled")
                    print(f"[embed_worker] job {job_id} cancelled", flush=True)
                    return 0

                item = embed_jobs.get_next_queued(jobs_conn, job_id)
                if item is None:
                    if job["include_links"]:
                        source_titles = jobs_conn.execute(
                            "SELECT DISTINCT source_title FROM embed_job_items "
                            "WHERE job_id = ? AND status = 'complete'",
                            (job_id,),
                        ).fetchall()
                        for st_row in source_titles:
                            rag_conn.execute(
                                "UPDATE articles_meta SET links_embedded = 1 WHERE title = ?",
                                (st_row["source_title"],),
                            )
                        rag_conn.commit()
                    embed_jobs.mark_job(jobs_conn, job_id, "complete")
                    print(f"[embed_worker] job {job_id} complete", flush=True)
                    return 0

                print(
                    f"[embed_worker] processing {item['title']!r} "
                    f"(item {item['id']})",
                    flush=True,
                )
                _process_item(item, jobs_conn, wiki_conn, rag_conn, already_embedded)
                embed_jobs.touch_job(jobs_conn, job_id)
        finally:
            if rag_conn is not None:
                rag_conn.close()
            wiki_conn.close()

    try:
        return run_worker(wiki, "embed", mark_failed, body)
    finally:
        jobs_conn.close()


if __name__ == "__main__":
    sys.exit(main())
