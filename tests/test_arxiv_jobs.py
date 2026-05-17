"""Tests for arxiv/jobs.py — schema and CRUD helpers."""

from arxiv import jobs as arxiv_jobs


def _conn(tmp_path):
    return arxiv_jobs.connect_arxiv_jobs(tmp_path / "jobs.db")


class TestSchema:
    def test_connect_creates_tables_idempotently(self, tmp_path):
        c1 = arxiv_jobs.connect_arxiv_jobs(tmp_path / "j.db")
        c1.close()
        c2 = arxiv_jobs.connect_arxiv_jobs(tmp_path / "j.db")
        names = {r[0] for r in c2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"arxiv_embed_jobs", "arxiv_embed_job_items"}.issubset(names)
        c2.close()

    def test_jobs_columns(self, tmp_path):
        conn = _conn(tmp_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(arxiv_embed_jobs)").fetchall()}
        for required in (
            "id",
            "status",
            "cancel_requested",
            "started_at",
            "updated_at",
            "finished_at",
            "log_path",
            "error_message",
            "triggered_by_arxiv_id",
        ):
            assert required in cols, f"missing column: {required}"

    def test_items_columns(self, tmp_path):
        conn = _conn(tmp_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(arxiv_embed_job_items)").fetchall()}
        for required in ("id", "job_id", "arxiv_id", "status", "chunk_count", "error_message"):
            assert required in cols, f"missing column: {required}"


class TestJobsCrud:
    def test_create_and_get(self, tmp_path):
        conn = _conn(tmp_path)
        job_id = arxiv_jobs.create_job(conn, "/tmp/x.log", "2401.0001")
        job = arxiv_jobs.get_job(conn, job_id)
        assert job is not None
        assert job["status"] == "running"
        assert job["cancel_requested"] == 0
        assert job["triggered_by_arxiv_id"] == "2401.0001"

    def test_create_without_trigger(self, tmp_path):
        conn = _conn(tmp_path)
        job_id = arxiv_jobs.create_job(conn, "/tmp/x.log")
        assert arxiv_jobs.get_job(conn, job_id)["triggered_by_arxiv_id"] is None

    def test_get_active_job_only_running(self, tmp_path):
        conn = _conn(tmp_path)
        job_id = arxiv_jobs.create_job(conn, "/tmp/x.log")
        assert arxiv_jobs.get_active_job(conn)["id"] == job_id

        arxiv_jobs.mark_job(conn, job_id, "complete")
        assert arxiv_jobs.get_active_job(conn) is None

    def test_request_cancel_excludes_from_active(self, tmp_path):
        conn = _conn(tmp_path)
        job_id = arxiv_jobs.create_job(conn, "/tmp/x.log")
        arxiv_jobs.request_cancel(conn, job_id)
        assert arxiv_jobs.get_active_job(conn) is None
        # The job row itself is still 'running' until the worker observes the
        # cancel flag and calls mark_job — but the gate function returns None.

    def test_mark_job_sets_finished_at(self, tmp_path):
        conn = _conn(tmp_path)
        job_id = arxiv_jobs.create_job(conn, "/tmp/x.log")
        assert arxiv_jobs.get_job(conn, job_id)["finished_at"] is None
        arxiv_jobs.mark_job(conn, job_id, "complete")
        assert arxiv_jobs.get_job(conn, job_id)["finished_at"] is not None

    def test_get_latest_jobs(self, tmp_path):
        conn = _conn(tmp_path)
        ids = [arxiv_jobs.create_job(conn, "/tmp/x.log") for _ in range(3)]
        latest = arxiv_jobs.get_latest_jobs(conn, limit=2)
        assert [r["id"] for r in latest] == [ids[2], ids[1]]


class TestItemsCrud:
    def test_append_dedups(self, tmp_path):
        conn = _conn(tmp_path)
        job_id = arxiv_jobs.create_job(conn, "/tmp/x.log")
        n1 = arxiv_jobs.append_items(conn, job_id, ["2401.0001", "2401.0002"])
        n2 = arxiv_jobs.append_items(conn, job_id, ["2401.0002", "2401.0003"])
        assert n1 == 2
        assert n2 == 1
        items = arxiv_jobs.get_items(conn, job_id)
        assert {i["arxiv_id"] for i in items} == {"2401.0001", "2401.0002", "2401.0003"}

    def test_append_empty_is_noop(self, tmp_path):
        conn = _conn(tmp_path)
        job_id = arxiv_jobs.create_job(conn, "/tmp/x.log")
        assert arxiv_jobs.append_items(conn, job_id, []) == 0

    def test_get_next_queued_returns_oldest(self, tmp_path):
        conn = _conn(tmp_path)
        job_id = arxiv_jobs.create_job(conn, "/tmp/x.log")
        arxiv_jobs.append_items(conn, job_id, ["a", "b", "c"])
        item = arxiv_jobs.get_next_queued(conn, job_id)
        assert item["arxiv_id"] == "a"

    def test_get_next_queued_skips_in_progress(self, tmp_path):
        conn = _conn(tmp_path)
        job_id = arxiv_jobs.create_job(conn, "/tmp/x.log")
        arxiv_jobs.append_items(conn, job_id, ["a", "b"])
        first = arxiv_jobs.get_next_queued(conn, job_id)
        arxiv_jobs.update_item(conn, first["id"], "in_progress")
        nxt = arxiv_jobs.get_next_queued(conn, job_id)
        assert nxt["arxiv_id"] == "b"

    def test_update_item_terminal_sets_finished_at(self, tmp_path):
        conn = _conn(tmp_path)
        job_id = arxiv_jobs.create_job(conn, "/tmp/x.log")
        arxiv_jobs.append_items(conn, job_id, ["a"])
        item = arxiv_jobs.get_next_queued(conn, job_id)
        arxiv_jobs.update_item(conn, item["id"], "embedded", chunk_count=5)
        refreshed = next(i for i in arxiv_jobs.get_items(conn, job_id) if i["id"] == item["id"])
        assert refreshed["status"] == "embedded"
        assert refreshed["chunk_count"] == 5
        assert refreshed["finished_at"] is not None

    def test_update_item_in_progress_leaves_finished_at_null(self, tmp_path):
        conn = _conn(tmp_path)
        job_id = arxiv_jobs.create_job(conn, "/tmp/x.log")
        arxiv_jobs.append_items(conn, job_id, ["a"])
        item = arxiv_jobs.get_next_queued(conn, job_id)
        arxiv_jobs.update_item(conn, item["id"], "in_progress")
        refreshed = next(i for i in arxiv_jobs.get_items(conn, job_id) if i["id"] == item["id"])
        assert refreshed["status"] == "in_progress"
        assert refreshed["finished_at"] is None

    def test_count_items_by_status(self, tmp_path):
        conn = _conn(tmp_path)
        job_id = arxiv_jobs.create_job(conn, "/tmp/x.log")
        arxiv_jobs.append_items(conn, job_id, ["a", "b", "c"])
        items = arxiv_jobs.get_items(conn, job_id)
        arxiv_jobs.update_item(conn, items[0]["id"], "embedded")
        arxiv_jobs.update_item(conn, items[1]["id"], "failed")
        # leave items[2] queued
        counts = arxiv_jobs.count_items_by_status(conn, job_id)
        assert counts == {"queued": 1, "embedded": 1, "failed": 1}


class TestOrphanedJobRecovery:
    def test_clear_orphaned_jobs_marks_running_as_failed(self, tmp_path):
        conn = _conn(tmp_path)
        job_id = arxiv_jobs.create_job(conn, "/tmp/x.log")
        arxiv_jobs.append_items(conn, job_id, ["a"])
        item = arxiv_jobs.get_next_queued(conn, job_id)
        arxiv_jobs.update_item(conn, item["id"], "in_progress")

        n = arxiv_jobs.clear_orphaned_jobs(conn)

        assert n == 1
        job = arxiv_jobs.get_job(conn, job_id)
        assert job["status"] == "failed"
        assert job["finished_at"] is not None
        # In-progress item also marked failed.
        refreshed = next(i for i in arxiv_jobs.get_items(conn, job_id) if i["id"] == item["id"])
        assert refreshed["status"] == "failed"

    def test_clear_orphaned_jobs_skips_completed(self, tmp_path):
        conn = _conn(tmp_path)
        job_id = arxiv_jobs.create_job(conn, "/tmp/x.log")
        arxiv_jobs.mark_job(conn, job_id, "complete")
        n = arxiv_jobs.clear_orphaned_jobs(conn)
        assert n == 0
        assert arxiv_jobs.get_job(conn, job_id)["status"] == "complete"
