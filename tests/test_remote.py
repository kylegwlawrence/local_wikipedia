"""Tests for the remote-wiki SQL-proxy placeholder.

Covers env-driven config, the ``RemoteSqliteConnection`` wire contract via
``httpx.MockTransport``, the row / cursor surface that mimics
``sqlite3.Row`` / ``sqlite3.Cursor``, and the dispatch in
``app.deps.connect``.
"""

import json
from unittest.mock import MagicMock

import httpx
import pytest

import paths
from app.deps import connect
from remote import RemoteCursor, RemoteRow, RemoteSqliteConnection, RemoteSqliteError


def _mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


class TestRemoteConfig:
    def test_unset_returns_none(self, monkeypatch):
        monkeypatch.delenv("WIKI_REMOTE_ENWIKI", raising=False)
        assert paths.remote_url_for("enwiki") is None
        assert paths.is_remote("enwiki") is False

    def test_set_returns_url(self, monkeypatch):
        monkeypatch.setenv("WIKI_REMOTE_ENWIKI", "http://host:8000")
        assert paths.remote_url_for("enwiki") == "http://host:8000"
        assert paths.is_remote("enwiki") is True

    def test_trailing_slash_stripped(self, monkeypatch):
        monkeypatch.setenv("WIKI_REMOTE_ENWIKI", "http://host:8000/")
        assert paths.remote_url_for("enwiki") == "http://host:8000"

    def test_per_wiki_isolation(self, monkeypatch):
        monkeypatch.setenv("WIKI_REMOTE_ENWIKI", "http://a:8000")
        monkeypatch.delenv("WIKI_REMOTE_SIMPLEWIKI", raising=False)
        assert paths.is_remote("enwiki") is True
        assert paths.is_remote("simplewiki") is False


class TestRemoteRow:
    def test_string_access(self):
        row = RemoteRow(["title", "page_id"], ["Apple", 42])
        assert row["title"] == "Apple"
        assert row["page_id"] == 42

    def test_case_insensitive_column_lookup(self):
        # sqlite3.Row is case-insensitive for column names.
        row = RemoteRow(["Title"], ["Apple"])
        assert row["title"] == "Apple"
        assert row["TITLE"] == "Apple"

    def test_index_access(self):
        row = RemoteRow(["title", "page_id"], ["Apple", 42])
        assert row[0] == "Apple"
        assert row[1] == 42

    def test_keys(self):
        row = RemoteRow(["title", "page_id"], ["Apple", 42])
        assert row.keys() == ["title", "page_id"]

    def test_len(self):
        row = RemoteRow(["a", "b", "c"], [1, 2, 3])
        assert len(row) == 3

    def test_iter_yields_values(self):
        # sqlite3.Row iterates values, not column names.
        row = RemoteRow(["a", "b"], [1, 2])
        assert list(row) == [1, 2]

    def test_dict_conversion(self):
        row = RemoteRow(["title", "page_id"], ["Apple", 42])
        assert dict(zip(row.keys(), list(row), strict=True)) == {"title": "Apple", "page_id": 42}

    def test_missing_column_raises_indexerror(self):
        row = RemoteRow(["title"], ["Apple"])
        with pytest.raises(IndexError):
            _ = row["nonexistent"]


class TestRemoteCursor:
    def test_fetchone_iterates(self):
        cur = RemoteCursor(["title"], [["Apple"], ["Banana"]])
        assert cur.fetchone()["title"] == "Apple"
        assert cur.fetchone()["title"] == "Banana"
        assert cur.fetchone() is None

    def test_fetchall_after_partial_fetchone(self):
        cur = RemoteCursor(["title"], [["Apple"], ["Banana"], ["Cherry"]])
        cur.fetchone()
        rest = cur.fetchall()
        assert [r["title"] for r in rest] == ["Banana", "Cherry"]
        assert cur.fetchone() is None

    def test_fetchall_empty(self):
        cur = RemoteCursor([], [])
        assert cur.fetchall() == []
        assert cur.fetchone() is None

    def test_iteration(self):
        cur = RemoteCursor(["x"], [[1], [2], [3]])
        assert [r["x"] for r in cur] == [1, 2, 3]


class TestRemoteSqliteConnection:
    def test_execute_posts_sql_and_params(self):
        captured = {}

        def handler(request):
            captured["path"] = request.url.path
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"columns": ["title"], "rows": [["Apple"]]})

        conn = RemoteSqliteConnection("http://remote.test", "enwiki", client=_mock_client(handler))
        cur = conn.execute("SELECT title FROM articles WHERE page_id = ?", (42,))
        assert captured["path"] == "/api/sql/enwiki"
        assert captured["body"] == {
            "sql": "SELECT title FROM articles WHERE page_id = ?",
            "params": [42],
        }
        assert cur.fetchone()["title"] == "Apple"

    def test_execute_default_empty_params(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"columns": [], "rows": []})

        conn = RemoteSqliteConnection("http://remote.test", "enwiki", client=_mock_client(handler))
        conn.execute("CREATE TABLE IF NOT EXISTS x (k TEXT)")
        assert captured["body"]["params"] == []

    def test_execute_returns_rows_with_row_access(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "columns": ["title", "text_bytes"],
                    "rows": [["Apple", 100], ["Banana", 200]],
                },
            )

        conn = RemoteSqliteConnection("http://remote.test", "enwiki", client=_mock_client(handler))
        rows = conn.execute("SELECT title, text_bytes FROM articles").fetchall()
        assert len(rows) == 2
        assert rows[0]["title"] == "Apple"
        assert rows[0]["text_bytes"] == 100
        assert rows[1][0] == "Banana"

    def test_execute_network_error_raises(self):
        def handler(request):
            raise httpx.ConnectError("unreachable")

        conn = RemoteSqliteConnection("http://remote.test", "enwiki", client=_mock_client(handler))
        with pytest.raises(RemoteSqliteError, match="network error"):
            conn.execute("SELECT 1")

    def test_execute_4xx_raises(self):
        def handler(request):
            return httpx.Response(400, text='{"error": "bad sql"}')

        conn = RemoteSqliteConnection("http://remote.test", "enwiki", client=_mock_client(handler))
        with pytest.raises(RemoteSqliteError, match="returned 400"):
            conn.execute("SELECT broken")

    def test_execute_5xx_raises(self):
        def handler(request):
            return httpx.Response(500, text="kaboom")

        conn = RemoteSqliteConnection("http://remote.test", "enwiki", client=_mock_client(handler))
        with pytest.raises(RemoteSqliteError, match="returned 500"):
            conn.execute("SELECT 1")

    def test_commit_is_noop(self):
        # Each statement autocommits on the remote — local commit() shouldn't
        # raise or generate traffic.
        called = []

        def handler(request):
            called.append(request.url.path)
            return httpx.Response(200, json={"columns": [], "rows": []})

        conn = RemoteSqliteConnection("http://remote.test", "enwiki", client=_mock_client(handler))
        conn.commit()
        assert called == []

    def test_rollback_raises(self):
        # rollback should surface a clear error rather than silently doing
        # nothing — protects callers who rely on transactional behaviour.
        conn = RemoteSqliteConnection(
            "http://remote.test",
            "enwiki",
            client=_mock_client(lambda r: httpx.Response(200, json={"columns": [], "rows": []})),
        )
        with pytest.raises(RemoteSqliteError, match="rollback"):
            conn.rollback()

    def test_base_url_trailing_slash_normalised(self):
        captured = {}

        def handler(request):
            captured["path"] = request.url.path
            return httpx.Response(200, json={"columns": [], "rows": []})

        conn = RemoteSqliteConnection("http://remote.test/", "enwiki", client=_mock_client(handler))
        conn.execute("SELECT 1")
        assert captured["path"] == "/api/sql/enwiki"

    def test_context_manager_closes_owned_client(self):
        with RemoteSqliteConnection("http://remote.test", "enwiki") as c:
            inner = c._client
            assert not inner.is_closed
        assert inner.is_closed

    def test_context_manager_does_not_close_injected_client(self):
        injected = _mock_client(lambda r: httpx.Response(200, json={"columns": [], "rows": []}))
        with RemoteSqliteConnection("http://remote.test", "enwiki", client=injected):
            pass
        assert not injected.is_closed
        injected.close()


class TestConnectDispatch:
    def test_returns_remote_connection_when_wiki_is_remote(self, monkeypatch):
        monkeypatch.delenv("WIKI_DB", raising=False)
        monkeypatch.setenv("WIKI_REMOTE_ENWIKI", "http://host:8000")
        request = MagicMock()
        request.cookies = {"wiki_pref": "enwiki"}
        conn = connect(request)
        try:
            assert isinstance(conn, RemoteSqliteConnection)
            assert conn.base_url == "http://host:8000"
            assert conn.wiki == "enwiki"
        finally:
            conn.close()

    def test_wiki_db_env_overrides_remote(self, monkeypatch, tmp_path):
        # WIKI_DB is the test/dev override and must win even if a remote is
        # configured — otherwise the test suite couldn't run with both set.
        db_file = tmp_path / "fake.db"
        db_file.touch()
        monkeypatch.setenv("WIKI_DB", str(db_file))
        monkeypatch.setenv("WIKI_REMOTE_ENWIKI", "http://host:8000")
        request = MagicMock()
        request.cookies = {"wiki_pref": "enwiki"}
        conn = connect(request)
        try:
            assert not isinstance(conn, RemoteSqliteConnection)
        finally:
            conn.close()

    def test_falls_back_to_local_when_no_remote_configured(self, monkeypatch, tmp_path):
        monkeypatch.delenv("WIKI_REMOTE_ENWIKI", raising=False)
        db_file = tmp_path / "fake.db"
        db_file.touch()
        monkeypatch.setenv("WIKI_DB", str(db_file))
        request = MagicMock()
        request.cookies = {"wiki_pref": "enwiki"}
        conn = connect(request)
        try:
            assert not isinstance(conn, RemoteSqliteConnection)
        finally:
            conn.close()
