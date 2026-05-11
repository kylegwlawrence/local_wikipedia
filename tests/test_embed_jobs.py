"""Tests for the embed_jobs schema and CRUD helpers."""
import embed_jobs


def _conn(tmp_path):
    return embed_jobs.connect_embed_jobs(tmp_path / "jobs.db")


class TestSchema:
    def test_connect_creates_tables_idempotently(self, tmp_path):
        # Two consecutive opens should not raise — schema CREATE IF NOT EXISTS.
        c1 = embed_jobs.connect_embed_jobs(tmp_path / "j.db")
        c1.close()
        c2 = embed_jobs.connect_embed_jobs(tmp_path / "j.db")
        # Schema present:
        names = {r[0] for r in c2.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert {"embed_jobs", "embed_job_items"}.issubset(names)
        c2.close()


class TestJobsCrud:
    def test_create_and_get(self, tmp_path):
        conn = _conn(tmp_path)
        job_id = embed_jobs.create_job(conn, "simplewiki", "/tmp/x.log")
        job = embed_jobs.get_job(conn, job_id)
        assert job is not None
        assert job["wiki"] == "simplewiki"
        assert job["status"] == "running"
        assert job["cancel_requested"] == 0

    def test_get_active_job_only_running(self, tmp_path):
        conn = _conn(tmp_path)
        job_id = embed_jobs.create_job(conn, "simplewiki", "/tmp/x.log")
        assert embed_jobs.get_active_job(conn, "simplewiki")["id"] == job_id

        embed_jobs.mark_job(conn, job_id, "complete")
        assert embed_jobs.get_active_job(conn, "simplewiki") is None

    def test_request_cancel_excludes_from_active(self, tmp_path):
        conn = _conn(tmp_path)
        job_id = embed_jobs.create_job(conn, "simplewiki", "/tmp/x.log")
        embed_jobs.request_cancel(conn, job_id)
        # The worker is still technically running, but the route uses
        # get_active_job to decide whether to spawn a new worker. A
        # cancel-requested job should NOT be appended to.
        assert embed_jobs.get_active_job(conn, "simplewiki") is None


class TestItems:
    def test_append_items_dedupes(self, tmp_path):
        conn = _conn(tmp_path)
        job_id = embed_jobs.create_job(conn, "simplewiki", "/tmp/x.log")
        inserted = embed_jobs.append_items(conn, job_id, [
            ("Apple", "Fruits"),
            ("Banana", "Fruits"),
            ("Apple", "OtherSource"),  # duplicate title — should be ignored
        ])
        assert inserted == 2
        items = embed_jobs.get_items(conn, job_id)
        assert [i["title"] for i in items] == ["Apple", "Banana"]
        # source_title is preserved from first insertion.
        assert items[0]["source_title"] == "Fruits"

    def test_get_next_queued_oldest_first(self, tmp_path):
        conn = _conn(tmp_path)
        job_id = embed_jobs.create_job(conn, "simplewiki", "/tmp/x.log")
        embed_jobs.append_items(conn, job_id, [("A", "Src"), ("B", "Src")])
        first = embed_jobs.get_next_queued(conn, job_id)
        assert first["title"] == "A"

        embed_jobs.update_item(conn, first["id"], "complete", chunk_count=3)
        second = embed_jobs.get_next_queued(conn, job_id)
        assert second["title"] == "B"

    def test_update_item_terminal_sets_finished_at(self, tmp_path):
        conn = _conn(tmp_path)
        job_id = embed_jobs.create_job(conn, "simplewiki", "/tmp/x.log")
        embed_jobs.append_items(conn, job_id, [("A", "Src")])
        item = embed_jobs.get_next_queued(conn, job_id)

        embed_jobs.update_item(conn, item["id"], "in_progress")
        row = embed_jobs.get_items(conn, job_id)[0]
        assert row["status"] == "in_progress"
        assert row["finished_at"] is None

        embed_jobs.update_item(conn, item["id"], "complete", chunk_count=5)
        row = embed_jobs.get_items(conn, job_id)[0]
        assert row["status"] == "complete"
        assert row["chunk_count"] == 5
        assert row["finished_at"] is not None

    def test_count_items_by_status(self, tmp_path):
        conn = _conn(tmp_path)
        job_id = embed_jobs.create_job(conn, "simplewiki", "/tmp/x.log")
        embed_jobs.append_items(conn, job_id, [
            ("A", "Src"), ("B", "Src"), ("C", "Src"),
        ])
        items = embed_jobs.get_items(conn, job_id)
        embed_jobs.update_item(conn, items[0]["id"], "complete", chunk_count=1)
        embed_jobs.update_item(conn, items[1]["id"], "failed",
                               error_message="boom")

        counts = embed_jobs.count_items_by_status(conn, job_id)
        assert counts == {"complete": 1, "failed": 1, "queued": 1}
