"""Tests for arxiv/ingest.py — upsert, datestamp handling, state, CLI modes."""

import json

import httpx
import pytest
import respx

import paths
from arxiv import oai
from arxiv.ingest import (
    ingest_records,
    main,
    reset_papers,
    upsert_paper,
)
from arxiv.schema import connect_papers, get_ingest_state, set_ingest_state


def _record(
    arxiv_id: str = "2401.12345",
    oai_datestamp: str = "2024-01-22",
    title: str = "Test",
    categories: str = "cs.CL cs.LG",
    authors: list[str] | None = None,
):
    return {
        "id": arxiv_id,
        "oai_datestamp": oai_datestamp,
        "title": title,
        "abstract": "Abstract.",
        "authors": authors if authors is not None else ["Alice Smith"],
        "categories": categories,
        "primary_category": categories.split()[0] if categories else "",
        "submitted_date": "2024-01-22",
        "updated_date": None,
        "doi": None,
        "journal_ref": None,
        "comments": None,
    }


@pytest.fixture
def conn(tmp_path):
    c = connect_papers(tmp_path / "arxiv.db")
    yield c
    c.close()


class TestUpsertPaper:
    def test_insert_new(self, conn):
        action = upsert_paper(conn, _record())
        assert action == "inserted"
        row = conn.execute("SELECT title, authors FROM papers WHERE id = ?", ("2401.12345",)).fetchone()
        assert row["title"] == "Test"
        assert json.loads(row["authors"]) == ["Alice Smith"]

    def test_skip_unchanged_datestamp(self, conn):
        upsert_paper(conn, _record())
        action = upsert_paper(conn, _record(title="Should Not Change"))
        assert action == "skipped"
        row = conn.execute("SELECT title FROM papers WHERE id = ?", ("2401.12345",)).fetchone()
        assert row["title"] == "Test"  # unchanged

    def test_update_on_new_datestamp(self, conn):
        upsert_paper(conn, _record(oai_datestamp="2024-01-22"))
        action = upsert_paper(conn, _record(oai_datestamp="2024-01-25", title="Updated"))
        assert action == "updated"
        row = conn.execute("SELECT title, oai_datestamp FROM papers WHERE id = ?", ("2401.12345",)).fetchone()
        assert row["title"] == "Updated"
        assert row["oai_datestamp"] == "2024-01-25"

    def test_authors_stored_as_json_list(self, conn):
        upsert_paper(conn, _record(authors=["A", "B", "C"]))
        row = conn.execute("SELECT authors FROM papers WHERE id = ?", ("2401.12345",)).fetchone()
        assert json.loads(row["authors"]) == ["A", "B", "C"]


class TestIngestRecords:
    def test_returns_action_stats(self, conn):
        records = [
            _record(arxiv_id="1"),
            _record(arxiv_id="2"),
            _record(arxiv_id="1"),  # skipped — same datestamp
        ]
        stats = ingest_records(conn, records)
        assert stats == {"inserted": 2, "updated": 0, "skipped": 1}

    def test_commits_at_batch_boundary(self, conn):
        records = [_record(arxiv_id=str(i)) for i in range(5)]
        stats = ingest_records(conn, records, batch_size=2)
        assert stats["inserted"] == 5
        # Final commit happens after the loop too.
        assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 5


class TestResetPapers:
    def test_drops_papers_and_state(self, conn):
        upsert_paper(conn, _record())
        set_ingest_state(conn, "last_harvested_date", "2024-01-22")
        conn.commit()
        reset_papers(conn)
        assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 0
        assert get_ingest_state(conn, "last_harvested_date") is None


def _oai_xml(arxiv_id: str = "2401.12345") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <ListRecords>
    <record>
      <header>
        <identifier>oai:arXiv.org:{arxiv_id}</identifier>
        <datestamp>2024-01-22</datestamp>
      </header>
      <metadata>
        <arXiv xmlns="http://arxiv.org/OAI/arXiv/">
          <id>{arxiv_id}</id>
          <created>2024-01-22</created>
          <authors><author><keyname>Smith</keyname><forenames>A</forenames></author></authors>
          <title>Title</title>
          <categories>cs.CL</categories>
          <abstract>Body.</abstract>
        </arXiv>
      </metadata>
    </record>
  </ListRecords>
</OAI-PMH>"""


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    """Redirect arxiv DB + cache to tmp_path; neutralize the OAI rate-limit sleep."""
    monkeypatch.setattr(paths, "DUMPS_DIR", tmp_path)
    monkeypatch.setattr(paths, "ARXIV_DB", tmp_path / "arxiv.db")
    monkeypatch.setattr(paths, "ARXIV_OAI_CACHE_DIR", tmp_path / "cache")
    # fetch_page's default sleep is bound at function-definition time, so the
    # cheapest way to skip the 3-second pause is to zero the interval.
    monkeypatch.setattr(oai, "MIN_REQUEST_INTERVAL", 0)
    return tmp_path


class TestMain:
    @respx.mock
    def test_network_mode_inserts_and_updates_state(self, isolated_paths):
        respx.get(oai.OAI_ENDPOINT).mock(return_value=httpx.Response(200, text=_oai_xml()))

        rc = main(["--from", "2024-01-01", "--until", "2024-01-31"])
        assert rc == 0

        conn = connect_papers(isolated_paths / "arxiv.db")
        try:
            assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1
            assert get_ingest_state(conn, "last_harvested_date") == "2024-01-31"
        finally:
            conn.close()

    def test_from_cache_mode_reads_local_xml(self, isolated_paths):
        cache_dir = isolated_paths / "cache"
        cache_dir.mkdir()
        (cache_dir / "page1.xml").write_text(_oai_xml(arxiv_id="2401.0001"), encoding="utf-8")
        (cache_dir / "page2.xml").write_text(_oai_xml(arxiv_id="2401.0002"), encoding="utf-8")

        # No respx — must not touch the network.
        rc = main(["--from-cache"])
        assert rc == 0

        conn = connect_papers(isolated_paths / "arxiv.db")
        try:
            ids = {r[0] for r in conn.execute("SELECT id FROM papers").fetchall()}
            assert ids == {"2401.0001", "2401.0002"}
            # last_harvested_date must NOT be updated when replaying cache.
            assert get_ingest_state(conn, "last_harvested_date") is None
        finally:
            conn.close()

    def test_from_cache_missing_dir_returns_1(self, isolated_paths, capsys):
        rc = main(["--from-cache"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "Cache dir not found" in err

    @respx.mock
    def test_reset_flag_clears_existing_rows(self, isolated_paths):
        # Pre-populate a row.
        conn = connect_papers(isolated_paths / "arxiv.db")
        upsert_paper(conn, _record(arxiv_id="old", oai_datestamp="old"))
        conn.commit()
        conn.close()

        respx.get(oai.OAI_ENDPOINT).mock(return_value=httpx.Response(200, text=_oai_xml(arxiv_id="new")))
        rc = main(["--reset", "--from", "2024-01-01", "--until", "2024-01-31"])
        assert rc == 0

        conn = connect_papers(isolated_paths / "arxiv.db")
        try:
            ids = {r[0] for r in conn.execute("SELECT id FROM papers").fetchall()}
            assert ids == {"new"}
        finally:
            conn.close()

    @respx.mock
    def test_default_from_uses_ingest_state(self, isolated_paths):
        # Seed last_harvested_date.
        conn = connect_papers(isolated_paths / "arxiv.db")
        set_ingest_state(conn, "last_harvested_date", "2025-03-15")
        conn.commit()
        conn.close()

        route = respx.get(oai.OAI_ENDPOINT).mock(return_value=httpx.Response(200, text=_oai_xml()))
        rc = main(["--until", "2025-03-31"])
        assert rc == 0
        url = str(route.calls[0].request.url)
        assert "from=2025-03-15" in url
