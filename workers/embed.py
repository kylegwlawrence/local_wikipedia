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
from jobs import embed as embed_jobs
from paths import JOBS_DB, REDIRECT_MAX_HOPS, db_path_for, rag_db_path_for
from rag import chunker
from rag.embed import embed_one
from rag.links import extract_article_links
from rag.schema import connect_rag
from workers.runner import run_worker


def _expand_links(
    item,
    canonical_title: str,
    wikitext: str,
    jobs_conn,
    wiki_conn,
) -> None:
    """If ``item`` has hops left, append its wikilink targets to the job queue.

    Resolves redirects against ``wiki_conn`` so the queue stores canonical
    titles, mirroring what the route handler does. Each appended row's
    ``source_title`` is ``canonical_title`` so the end-of-job finalizer marks
    this parent as ``links_embedded=1``. Children get ``hops_remaining`` one
    less than the parent (terminal at 0).

    Any failure here is logged but does not change the parent item's status —
    a successful embed must not be voided by a link-extraction error.
    """
    hops = item["hops_remaining"]
    if hops <= 0:
        return
    try:
        raw_targets = extract_article_links(wikitext, source_title=canonical_title)
        next_hops = hops - 1
        children: list[tuple[str, str, int]] = []
        seen: set[str] = set()
        for target in raw_targets:
            resolved = wiki_db.resolve_redirect(wiki_conn, target, REDIRECT_MAX_HOPS)
            picked = resolved or target
            if picked == canonical_title or picked in seen:
                continue
            seen.add(picked)
            children.append((picked, canonical_title, next_hops))
        if children:
            embed_jobs.append_items(jobs_conn, item["job_id"], children)
            print(
                f"[embed_worker] expanded {canonical_title!r} → {len(children)} item(s) at hops={next_hops}",
                flush=True,
            )
    except Exception as exc:
        print(
            f"[embed_worker] link expansion failed for {canonical_title!r}: {type(exc).__name__}: {exc}",
            flush=True,
        )


def _finalize_links_embedded(jobs_conn, rag_conn, job_id: int) -> None:
    """Mark articles whose links were embedded as part of this job.

    ``links_embedded`` is set on every distinct ``source_title`` that reached a
    terminal status — that includes the original trigger article and any 1-hop
    link that was itself the source of a 2-hop expansion.

    ``links_embedded_2hop`` is set only on source_titles that had an item
    enqueued at ``hops_remaining >= 1``: that flag is exclusive to the
    article(s) whose trigger was a 2-hop click, since ``_expand_links`` adds
    depth-1 children at ``hops_remaining=0``.
    """
    source_titles = jobs_conn.execute(
        "SELECT DISTINCT source_title FROM embed_job_items "
        "WHERE job_id = ? AND status NOT IN ('queued', 'in_progress')",
        (job_id,),
    ).fetchall()
    for st_row in source_titles:
        rag_conn.execute(
            "UPDATE articles_meta SET links_embedded = 1 WHERE title = ?",
            (st_row["source_title"],),
        )
    two_hop_sources = jobs_conn.execute(
        "SELECT DISTINCT source_title FROM embed_job_items "
        "WHERE job_id = ? AND hops_remaining >= 1 "
        "AND status NOT IN ('queued', 'in_progress')",
        (job_id,),
    ).fetchall()
    for st_row in two_hop_sources:
        rag_conn.execute(
            "UPDATE articles_meta SET links_embedded_2hop = 1 WHERE title = ?",
            (st_row["source_title"],),
        )
    rag_conn.commit()


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
        "SELECT page_id, title, revision_id, text_content FROM articles WHERE title = ?",
        (title,),
    ).fetchone()
    if row is None:
        embed_jobs.update_item(
            jobs_conn,
            item_id,
            "not_found",
            error_message=f"No article with title {title!r}",
        )
        return

    page_id = row["page_id"]
    revision_id = row["revision_id"]
    wikitext = row["text_content"]

    if chunker.is_redirect(wikitext):
        # Redirect articles have no meaningful links of their own; skip expansion.
        embed_jobs.update_item(jobs_conn, item_id, "skipped_redirect")
        return

    if already_embedded.get(page_id) == revision_id:
        embed_jobs.update_item(jobs_conn, item_id, "skipped_unchanged")
        # Still expand: re-triggering 2-hop on a previously 1-hop'd article
        # must still reach hop 2 even though we don't re-embed this article.
        _expand_links(item, row["title"], wikitext, jobs_conn, wiki_conn)
        return

    try:
        chunk_count = embed_one(
            rag_conn,
            page_id,
            row["title"],
            revision_id,
            wikitext,
        )
    except Exception as exc:
        embed_jobs.update_item(
            jobs_conn,
            item_id,
            "failed",
            error_message=f"{type(exc).__name__}: {exc}",
        )
        return

    already_embedded[page_id] = revision_id
    embed_jobs.update_item(
        jobs_conn,
        item_id,
        "complete",
        chunk_count=chunk_count,
    )
    _expand_links(item, row["title"], wikitext, jobs_conn, wiki_conn)


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
                for r in rag_conn.execute("SELECT page_id, revision_id FROM articles_meta").fetchall()
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
                        _finalize_links_embedded(jobs_conn, rag_conn, job_id)
                    embed_jobs.mark_job(jobs_conn, job_id, "complete")
                    print(f"[embed_worker] job {job_id} complete", flush=True)
                    return 0

                print(
                    f"[embed_worker] processing {item['title']!r} (item {item['id']})",
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
