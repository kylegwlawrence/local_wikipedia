"""Tests for parse.py."""
import bz2
import json
import pathlib
import sqlite3
import xml.etree.ElementTree as ET
import pytest
import parse.parse as parse_module
from parse.parse import (
    BATCH_SIZE,
    NAMESPACE_MAIN,
    _create_schema,
    _parse_page_element,
    _batch_insert_articles,
    parse_dump,
    query_database,
    verify_database,
    main,
)

WIKI = "simplewiki"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_page_xml(
    page_id: int,
    title: str,
    text: str,
    namespace: int = 0,
    revision_id: int = 1000,
    timestamp: str = "2026-01-01T00:00:00Z",
    username: str = "TestUser",
    user_id: int = 123,
) -> str:
    """Generate minimal valid Wikipedia page XML."""
    return f"""
    <page>
        <title>{title}</title>
        <ns>{namespace}</ns>
        <id>{page_id}</id>
        <revision>
            <id>{revision_id}</id>
            <parentid>{revision_id - 1}</parentid>
            <timestamp>{timestamp}</timestamp>
            <contributor>
                <username>{username}</username>
                <id>{user_id}</id>
            </contributor>
            <comment>Test edit</comment>
            <text bytes="{len(text)}">{text}</text>
        </revision>
    </page>
    """


def _create_test_dump(tmp_path: pathlib.Path, pages: list[dict]) -> pathlib.Path:
    """Create a minimal Wikipedia XML dump for testing.

    Args:
        tmp_path: Temporary directory path.
        pages: List of dicts with page data (page_id, title, text, namespace).

    Returns:
        Path to the created .xml.bz2 dump file.
    """
    # Build complete XML document
    xml_content = """<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">
    <siteinfo>
        <sitename>Test Wikipedia</sitename>
        <dbname>testwiki</dbname>
    </siteinfo>
"""
    for page in pages:
        xml_content += _minimal_page_xml(
            page_id=page["page_id"],
            title=page["title"],
            text=page["text"],
            namespace=page.get("namespace", 0),
            revision_id=page.get("revision_id", page["page_id"] * 10),
        )

    xml_content += "\n</mediawiki>"

    # Compress with bz2
    dump_path = tmp_path / f"{WIKI}-test-pages-articles-multistream.xml.bz2"
    with bz2.open(dump_path, "wb") as f:
        f.write(xml_content.encode("utf-8"))

    return dump_path


# ---------------------------------------------------------------------------
# _create_schema
# ---------------------------------------------------------------------------


class TestCreateSchema:
    def test_creates_articles_table(self, tmp_path: pathlib.Path) -> None:
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        _create_schema(conn)

        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='articles'")
        assert cursor.fetchone() is not None

        # Check columns exist
        cursor.execute("PRAGMA table_info(articles)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "page_id" in columns
        assert "title" in columns
        assert "text_content" in columns

        conn.close()

    def test_creates_indexes(self, tmp_path: pathlib.Path) -> None:
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        _create_schema(conn)

        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = {row[0] for row in cursor.fetchall()}

        assert "idx_articles_title" in indexes
        assert "idx_articles_namespace" in indexes
        assert "idx_articles_timestamp" in indexes

        conn.close()

    def test_creates_metadata_table(self, tmp_path: pathlib.Path) -> None:
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        _create_schema(conn)

        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='parse_metadata'")
        assert cursor.fetchone() is not None

        conn.close()


# ---------------------------------------------------------------------------
# _parse_page_element
# ---------------------------------------------------------------------------


class TestParsePageElement:
    def test_extracts_basic_fields(self) -> None:
        xml = _minimal_page_xml(
            page_id=42,
            title="Test Article",
            text="Article content here",
            namespace=0,
        )
        root = ET.fromstring(
            f'<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">{xml}</mediawiki>'
        )
        page_elem = root.find("{http://www.mediawiki.org/xml/export-0.11/}page")

        article = _parse_page_element(page_elem)

        assert article is not None
        assert article["page_id"] == 42
        assert article["title"] == "Test Article"
        assert article["namespace"] == 0
        assert article["text_content"] == "Article content here"
        assert article["revision_id"] == 1000

    def test_handles_missing_optional_fields(self) -> None:
        # Page without contributor info
        xml = """
        <page>
            <title>Minimal</title>
            <ns>0</ns>
            <id>1</id>
            <revision>
                <id>100</id>
                <timestamp>2026-01-01T00:00:00Z</timestamp>
                <text bytes="4">Test</text>
            </revision>
        </page>
        """
        root = ET.fromstring(
            f'<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">{xml}</mediawiki>'
        )
        page_elem = root.find("{http://www.mediawiki.org/xml/export-0.11/}page")

        article = _parse_page_element(page_elem)

        assert article is not None
        assert article["contributor_username"] is None
        assert article["contributor_id"] is None
        assert article["comment"] is None

    def test_returns_none_for_missing_required_fields(self) -> None:
        # Page without text element
        xml = """
        <page>
            <title>Broken</title>
            <ns>0</ns>
            <id>1</id>
            <revision>
                <id>100</id>
            </revision>
        </page>
        """
        root = ET.fromstring(
            f'<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">{xml}</mediawiki>'
        )
        page_elem = root.find("{http://www.mediawiki.org/xml/export-0.11/}page")

        article = _parse_page_element(page_elem)

        assert article is None

    def test_parses_different_namespaces(self) -> None:
        xml = _minimal_page_xml(
            page_id=99,
            title="Talk:Test",
            text="Discussion",
            namespace=1,  # Talk namespace
        )
        root = ET.fromstring(
            f'<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">{xml}</mediawiki>'
        )
        page_elem = root.find("{http://www.mediawiki.org/xml/export-0.11/}page")

        article = _parse_page_element(page_elem)

        assert article is not None
        assert article["namespace"] == 1


# ---------------------------------------------------------------------------
# _batch_insert_articles
# ---------------------------------------------------------------------------


class TestBatchInsertArticles:
    def test_inserts_multiple_articles(self, tmp_path: pathlib.Path) -> None:
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        _create_schema(conn)

        articles = [
            {
                "page_id": 1,
                "title": "Article 1",
                "namespace": 0,
                "revision_id": 10,
                "parent_revision_id": 9,
                "timestamp": "2026-01-01T00:00:00Z",
                "contributor_username": "User1",
                "contributor_id": 100,
                "comment": "Edit 1",
                "text_bytes": 10,
                "text_content": "Content 1",
            },
            {
                "page_id": 2,
                "title": "Article 2",
                "namespace": 0,
                "revision_id": 20,
                "parent_revision_id": None,
                "timestamp": "2026-01-02T00:00:00Z",
                "contributor_username": None,
                "contributor_id": None,
                "comment": None,
                "text_bytes": 10,
                "text_content": "Content 2",
            },
        ]

        _batch_insert_articles(conn, articles)
        conn.commit()

        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM articles")
        assert cursor.fetchone()[0] == 2

        cursor.execute("SELECT title FROM articles ORDER BY page_id")
        titles = [row[0] for row in cursor.fetchall()]
        assert titles == ["Article 1", "Article 2"]

        conn.close()

    def test_handles_empty_batch(self, tmp_path: pathlib.Path) -> None:
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        _create_schema(conn)

        _batch_insert_articles(conn, [])
        conn.commit()

        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM articles")
        assert cursor.fetchone()[0] == 0

        conn.close()


# ---------------------------------------------------------------------------
# parse_dump
# ---------------------------------------------------------------------------


class TestParseDump:
    def test_happy_path(self, tmp_path: pathlib.Path) -> None:
        # Create test dump with 3 articles
        pages = [
            {"page_id": 1, "title": "Article One", "text": "Content 1"},
            {"page_id": 2, "title": "Article Two", "text": "Content 2"},
            {"page_id": 3, "title": "Article Three", "text": "Content 3"},
        ]
        dump_path = _create_test_dump(tmp_path, pages)
        db_path = tmp_path / "test.db"

        total_pages, articles_inserted = parse_dump(dump_path, db_path, namespace_filter=0)

        assert total_pages == 3
        assert articles_inserted == 3
        assert db_path.exists()

        # Verify database content
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM articles")
        assert cursor.fetchone()[0] == 3

        cursor.execute("SELECT title FROM articles ORDER BY page_id")
        titles = [row[0] for row in cursor.fetchall()]
        assert titles == ["Article One", "Article Two", "Article Three"]

        conn.close()

    def test_namespace_filtering(self, tmp_path: pathlib.Path) -> None:
        # Mix of articles (ns=0) and talk pages (ns=1)
        pages = [
            {"page_id": 1, "title": "Article", "text": "Content", "namespace": 0},
            {"page_id": 2, "title": "Talk:Article", "text": "Discussion", "namespace": 1},
            {"page_id": 3, "title": "Another", "text": "More content", "namespace": 0},
        ]
        dump_path = _create_test_dump(tmp_path, pages)
        db_path = tmp_path / "test.db"

        total_pages, articles_inserted = parse_dump(dump_path, db_path, namespace_filter=0)

        assert total_pages == 3
        assert articles_inserted == 2  # Only namespace=0

        # Verify only articles stored
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT title FROM articles ORDER BY page_id")
        titles = [row[0] for row in cursor.fetchall()]
        assert titles == ["Article", "Another"]

        conn.close()

    def test_atomic_write(self, tmp_path: pathlib.Path) -> None:
        pages = [{"page_id": 1, "title": "Test", "text": "Content"}]
        dump_path = _create_test_dump(tmp_path, pages)
        db_path = tmp_path / "test.db"

        parse_dump(dump_path, db_path)

        # Temp file should be removed
        tmp_db = tmp_path / "test.db.tmp"
        assert not tmp_db.exists()
        assert db_path.exists()

    def test_batch_commits(self, tmp_path: pathlib.Path) -> None:
        # Create dump with more than BATCH_SIZE articles to test batching
        pages = [
            {"page_id": i, "title": f"Article {i}", "text": f"Content {i}"}
            for i in range(BATCH_SIZE + 500)
        ]
        dump_path = _create_test_dump(tmp_path, pages)
        db_path = tmp_path / "test.db"

        total_pages, articles_inserted = parse_dump(dump_path, db_path)

        assert total_pages == BATCH_SIZE + 500
        assert articles_inserted == BATCH_SIZE + 500

        # Verify all articles inserted
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM articles")
        assert cursor.fetchone()[0] == BATCH_SIZE + 500

        conn.close()

    def test_missing_dump_raises(self, tmp_path: pathlib.Path) -> None:
        dump_path = tmp_path / "nonexistent.xml.bz2"
        db_path = tmp_path / "test.db"

        with pytest.raises(RuntimeError, match="Dump file not found"):
            parse_dump(dump_path, db_path)

    def test_truncated_dump_saves_partial_results(self, tmp_path: pathlib.Path) -> None:
        # Simulate a partial download by writing a bz2 file whose XML is valid
        # for the first two articles but is cut off mid-document (no closing
        # </mediawiki>). The bz2 layer decompresses fine; the XML parser raises
        # ET.ParseError at the truncation point.
        ns = "http://www.mediawiki.org/xml/export-0.11/"
        complete_pages = "".join(
            _minimal_page_xml(page_id=i, title=f"Article {i}", text=f"Content {i}")
            for i in range(1, 3)
        )
        # Intentionally omit the closing </mediawiki> tag
        truncated_xml = f'<mediawiki xmlns="{ns}"><siteinfo></siteinfo>{complete_pages}'

        truncated_path = tmp_path / "enwiki-20260501-pages-articles.xml.bz2.tmp"
        with bz2.open(truncated_path, "wb") as f:
            f.write(truncated_xml.encode("utf-8"))

        db_path = tmp_path / "enwiki.db"

        # Should not raise — partial results are saved
        total_pages, articles_inserted = parse_dump(truncated_path, db_path)

        assert db_path.exists()
        assert articles_inserted == 2

        # Metadata row must be present
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM parse_metadata")
        assert cursor.fetchone()[0] == 1
        conn.close()


# ---------------------------------------------------------------------------
# query_database
# ---------------------------------------------------------------------------


class TestQueryDatabase:
    def test_table_format_default(self, tmp_path: pathlib.Path) -> None:
        # Create test database
        pages = [
            {"page_id": 1, "title": "Article One", "text": "Content 1"},
            {"page_id": 2, "title": "Article Two", "text": "Content 2"},
        ]
        dump_path = _create_test_dump(tmp_path, pages)
        db_path = tmp_path / "test.db"
        parse_dump(dump_path, db_path)

        result = query_database("SELECT title, page_id FROM articles ORDER BY page_id", db_path=db_path)

        assert isinstance(result, str)
        assert "Article One" in result
        assert "Article Two" in result
        assert "2 row(s) returned" in result

    def test_json_format(self, tmp_path: pathlib.Path) -> None:
        pages = [
            {"page_id": 1, "title": "Test Article", "text": "Test content"},
        ]
        dump_path = _create_test_dump(tmp_path, pages)
        db_path = tmp_path / "test.db"
        parse_dump(dump_path, db_path)

        result = query_database(
            "SELECT title, page_id, namespace FROM articles",
            db_path=db_path,
            format="json"
        )

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["title"] == "Test Article"
        assert result[0]["page_id"] == 1
        assert result[0]["namespace"] == 0

    def test_auto_discovers_database(self, monkeypatch, tmp_path: pathlib.Path) -> None:
        pages = [{"page_id": 1, "title": "Test", "text": "Content"}]
        dump_path = _create_test_dump(tmp_path, pages)
        db_path = tmp_path / f"{WIKI}.db"
        parse_dump(dump_path, db_path)

        # Patch DUMPS_DIR
        monkeypatch.setattr(parse_module, "DUMPS_DIR", tmp_path)

        result = query_database("SELECT COUNT(*) as count FROM articles", wiki=WIKI, format="json")

        assert len(result) == 1
        assert result[0]["count"] == 1

    def test_empty_result(self, tmp_path: pathlib.Path) -> None:
        pages = [{"page_id": 1, "title": "Test", "text": "Content"}]
        dump_path = _create_test_dump(tmp_path, pages)
        db_path = tmp_path / "test.db"
        parse_dump(dump_path, db_path)

        result = query_database(
            "SELECT * FROM articles WHERE title = 'NonExistent'",
            db_path=db_path
        )

        assert "No results found" in result

    def test_handles_null_values(self, tmp_path: pathlib.Path) -> None:
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        _create_schema(conn)

        # Insert article with NULL contributor
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO articles (page_id, title, namespace, revision_id, timestamp, text_bytes, text_content)
            VALUES (1, 'Test', 0, 100, '2026-01-01T00:00:00Z', 10, 'Content')
        """)
        conn.commit()
        conn.close()

        result = query_database(
            "SELECT title, contributor_username FROM articles",
            db_path=db_path
        )

        assert "Test" in result
        assert "NULL" in result

    def test_truncates_long_text(self, tmp_path: pathlib.Path) -> None:
        pages = [{"page_id": 1, "title": "Long Article", "text": "x" * 1000}]
        dump_path = _create_test_dump(tmp_path, pages)
        db_path = tmp_path / "test.db"
        parse_dump(dump_path, db_path)

        result = query_database(
            "SELECT text_content FROM articles",
            db_path=db_path
        )

        # Should truncate long content in table format
        assert "..." in result
        assert len(result.split("\n")[2]) < 100  # Row should be reasonably short

    def test_missing_database_raises(self, tmp_path: pathlib.Path) -> None:
        db_path = tmp_path / "nonexistent.db"

        with pytest.raises(RuntimeError, match="Database not found"):
            query_database("SELECT * FROM articles", db_path=db_path)

    def test_invalid_sql_raises(self, tmp_path: pathlib.Path) -> None:
        pages = [{"page_id": 1, "title": "Test", "text": "Content"}]
        dump_path = _create_test_dump(tmp_path, pages)
        db_path = tmp_path / "test.db"
        parse_dump(dump_path, db_path)

        with pytest.raises(RuntimeError, match="Query failed"):
            query_database("INVALID SQL QUERY", db_path=db_path)


# ---------------------------------------------------------------------------
# verify_database
# ---------------------------------------------------------------------------


class TestVerifyDatabase:
    def test_returns_statistics(self, tmp_path: pathlib.Path) -> None:
        pages = [
            {"page_id": 1, "title": "Article 1", "text": "Content 1"},
            {"page_id": 2, "title": "Article 2", "text": "Content 2"},
        ]
        dump_path = _create_test_dump(tmp_path, pages)
        db_path = tmp_path / "test.db"

        parse_dump(dump_path, db_path)
        stats = verify_database(db_path)

        assert stats["article_count"] == 2
        assert len(stats["samples"]) == 2
        assert stats["metadata"] is not None

    def test_missing_database_raises(self, tmp_path: pathlib.Path) -> None:
        db_path = tmp_path / "nonexistent.db"

        with pytest.raises(RuntimeError, match="Database not found"):
            verify_database(db_path)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain:
    def test_verify_only_flag(self, monkeypatch, tmp_path: pathlib.Path) -> None:
        # Create database first
        pages = [{"page_id": 1, "title": "Test", "text": "Content"}]
        dump_path = _create_test_dump(tmp_path, pages)
        db_path = tmp_path / "test.db"
        parse_dump(dump_path, db_path)

        # Run with --verify-only
        exit_code = main(["--database", str(db_path), "--verify-only"])

        assert exit_code == 0

    def test_explicit_paths(self, monkeypatch, tmp_path: pathlib.Path) -> None:
        pages = [{"page_id": 1, "title": "Test", "text": "Content"}]
        dump_path = _create_test_dump(tmp_path, pages)
        db_path = tmp_path / "output.db"

        exit_code = main(["--dump", str(dump_path), "--database", str(db_path)])

        assert exit_code == 0
        assert db_path.exists()

    def test_missing_dump_returns_one(self, monkeypatch, tmp_path: pathlib.Path) -> None:
        # Patch DUMPS_DIR to empty directory
        monkeypatch.setattr(parse_module, "DUMPS_DIR", tmp_path)

        exit_code = main(["--wiki", "nosuchwiki"])

        assert exit_code == 1

    def test_auto_discovers_dump(self, monkeypatch, tmp_path: pathlib.Path) -> None:
        # Create dump in tmp_path
        pages = [{"page_id": 1, "title": "Test", "text": "Content"}]
        _create_test_dump(tmp_path, pages)

        # Patch DUMPS_DIR
        monkeypatch.setattr(parse_module, "DUMPS_DIR", tmp_path)

        exit_code = main(["--wiki", WIKI])

        assert exit_code == 0
        assert (tmp_path / f"{WIKI}.db").exists()
