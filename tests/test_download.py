"""Tests for download.py."""
import hashlib
import pathlib

import httpx
import pytest
import respx

import download.download as download
from download.download import (
    BASE_URL,
    CHUNK_BYTES,
    FALLBACK_SUFFIXES,
    TARGET_SUFFIXES,
    download_with_verify,
    fetch_sha1sums,
    hash_file,
    main,
    verify_existing,
)

WIKI = "simplewiki"
DATE = "20251101"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _dump_filename(wiki: str, suffix: str) -> str:
    """Realistic Wikimedia dump filename, e.g. simplewiki-20251101-pages-…-bz2."""
    return f"{wiki}-{DATE}{suffix}"


def _manifest_text(wiki: str, overrides: dict[str, str] | None = None) -> str:
    """Build a valid sha1sums manifest for the two multistream target files."""
    lines = []
    for suffix in TARGET_SUFFIXES:
        filename = _dump_filename(wiki, suffix)
        sha1 = (overrides or {}).get(filename, _sha1(filename.encode()))
        lines.append(f"{sha1}  {filename}")
    return "\n".join(lines) + "\n"


def _fallback_manifest_text(wiki: str) -> str:
    """Build a manifest containing only the monolithic articles dump (no multistream)."""
    lines = []
    for suffix in FALLBACK_SUFFIXES:
        filename = _dump_filename(wiki, suffix)
        lines.append(f"{_sha1(filename.encode())}  {filename}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# hash_file
# ---------------------------------------------------------------------------

class TestHashFile:
    def test_known_content(self, tmp_path: pathlib.Path) -> None:
        data = b"hello wikipedia"
        f = tmp_path / "file.bin"
        f.write_bytes(data)
        assert hash_file(f) == _sha1(data)

    def test_multi_chunk_matches_single_hash(self, tmp_path: pathlib.Path) -> None:
        # Write a file larger than one chunk so multiple reads are exercised
        data = b"x" * (CHUNK_BYTES * 2 + 512)
        f = tmp_path / "big.bin"
        f.write_bytes(data)
        assert hash_file(f) == _sha1(data)

    def test_empty_file(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        assert hash_file(f) == _sha1(b"")


# ---------------------------------------------------------------------------
# verify_existing
# ---------------------------------------------------------------------------

class TestVerifyExisting:
    def test_missing_file_returns_false(self, tmp_path: pathlib.Path) -> None:
        assert verify_existing(tmp_path / "no_such_file.bin", "abc123") is False

    def test_matching_hash_returns_true(self, tmp_path: pathlib.Path) -> None:
        data = b"good content"
        f = tmp_path / "dump.bz2"
        f.write_bytes(data)
        assert verify_existing(f, _sha1(data)) is True

    def test_mismatched_hash_returns_false(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "dump.bz2"
        f.write_bytes(b"real content")
        assert verify_existing(f, "0" * 40) is False


# ---------------------------------------------------------------------------
# fetch_sha1sums
# ---------------------------------------------------------------------------

class TestFetchSha1sums:
    def _manifest_url(self, wiki: str) -> str:
        return f"{BASE_URL}/{wiki}/latest/{wiki}-latest-sha1sums.txt"

    @respx.mock
    def test_happy_path(self) -> None:
        body = _manifest_text(WIKI)
        respx.get(self._manifest_url(WIKI)).mock(return_value=httpx.Response(200, text=body))

        result = fetch_sha1sums(WIKI)

        assert len(result) == 2
        for suffix in TARGET_SUFFIXES:
            filename = _dump_filename(WIKI, suffix)
            assert filename in result
            assert result[filename] == _sha1(filename.encode())

    @respx.mock
    def test_unrelated_files_are_filtered_out(self) -> None:
        body = _manifest_text(WIKI) + f"{'a' * 40}  {WIKI}-something-else.sql.gz\n"
        respx.get(self._manifest_url(WIKI)).mock(return_value=httpx.Response(200, text=body))

        result = fetch_sha1sums(WIKI)

        assert all(name.endswith(tuple(TARGET_SUFFIXES)) for name in result)

    @respx.mock
    def test_missing_target_raises(self) -> None:
        # Only include the first target file, omit the second
        first = f"{WIKI}{TARGET_SUFFIXES[0]}"
        body = f"{_sha1(first.encode())}  {first}\n"
        respx.get(self._manifest_url(WIKI)).mock(return_value=httpx.Response(200, text=body))

        with pytest.raises(RuntimeError, match="missing target file"):
            fetch_sha1sums(WIKI)

    @respx.mock
    def test_http_error_raises(self) -> None:
        respx.get(self._manifest_url(WIKI)).mock(return_value=httpx.Response(404))

        with pytest.raises(httpx.HTTPStatusError):
            fetch_sha1sums(WIKI)

    @respx.mock
    def test_fallback_to_monolithic_when_no_multistream(self) -> None:
        body = _fallback_manifest_text(WIKI)
        respx.get(self._manifest_url(WIKI)).mock(return_value=httpx.Response(200, text=body))

        result = fetch_sha1sums(WIKI)

        assert len(result) == len(FALLBACK_SUFFIXES)
        for suffix in FALLBACK_SUFFIXES:
            filename = _dump_filename(WIKI, suffix)
            assert filename in result

    @respx.mock
    def test_no_article_files_raises(self) -> None:
        body = f"{'a' * 40}  {WIKI}-something-else.sql.gz\n"
        respx.get(self._manifest_url(WIKI)).mock(return_value=httpx.Response(200, text=body))

        with pytest.raises(RuntimeError, match="no article dump files"):
            fetch_sha1sums(WIKI)


# ---------------------------------------------------------------------------
# download_with_verify
# ---------------------------------------------------------------------------

class TestDownloadWithVerify:
    FILE_URL = f"{BASE_URL}/{WIKI}/latest/dump.bz2"

    @respx.mock
    def test_happy_path_writes_dest(self, tmp_path: pathlib.Path) -> None:
        data = b"wikipedia dump content"
        respx.get(self.FILE_URL).mock(return_value=httpx.Response(200, content=data))
        dest = tmp_path / "dump.bz2"

        download_with_verify(self.FILE_URL, dest, _sha1(data))

        assert dest.read_bytes() == data
        assert not dest.with_suffix(dest.suffix + ".tmp").exists()

    @respx.mock
    def test_hash_mismatch_raises_and_cleans_tmp(self, tmp_path: pathlib.Path) -> None:
        data = b"wikipedia dump content"
        respx.get(self.FILE_URL).mock(return_value=httpx.Response(200, content=data))
        dest = tmp_path / "dump.bz2"

        with pytest.raises(RuntimeError, match="SHA-1 mismatch"):
            download_with_verify(self.FILE_URL, dest, "0" * 40)

        assert not dest.exists()
        assert not dest.with_suffix(dest.suffix + ".tmp").exists()

    @respx.mock
    def test_http_error_raises(self, tmp_path: pathlib.Path) -> None:
        respx.get(self.FILE_URL).mock(return_value=httpx.Response(503))
        dest = tmp_path / "dump.bz2"

        with pytest.raises(httpx.HTTPStatusError):
            download_with_verify(self.FILE_URL, dest, "0" * 40)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

class TestMain:
    def _patch_manifest(self, monkeypatch: pytest.MonkeyPatch, wiki: str, tmp_path: pathlib.Path) -> dict[str, str]:
        """Wire fetch_sha1sums and DUMPS_DIR so main() uses tmp_path."""
        manifest = {}
        for suffix in TARGET_SUFFIXES:
            filename = _dump_filename(wiki, suffix)
            data = filename.encode()
            (tmp_path / filename).write_bytes(data)
            manifest[filename] = _sha1(data)

        monkeypatch.setattr(download, "fetch_sha1sums", lambda w: manifest)
        monkeypatch.setattr(download, "DUMPS_DIR", tmp_path)
        return manifest

    def test_all_verified_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        self._patch_manifest(monkeypatch, WIKI, tmp_path)
        assert main(["--wiki", WIKI]) == 0

    def test_download_succeeds_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        manifest = {}
        for suffix in TARGET_SUFFIXES:
            filename = _dump_filename(WIKI, suffix)
            data = filename.encode()
            manifest[filename] = _sha1(data)
            # Pre-stage the file so download_with_verify is not actually called
            (tmp_path / filename).write_bytes(data)

        monkeypatch.setattr(download, "fetch_sha1sums", lambda w: manifest)
        monkeypatch.setattr(download, "DUMPS_DIR", tmp_path)

        assert main(["--wiki", WIKI]) == 0

    def test_failed_download_returns_one(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        manifest = {}
        for suffix in TARGET_SUFFIXES:
            filename = _dump_filename(WIKI, suffix)
            manifest[filename] = "0" * 40  # hash won't match anything

        monkeypatch.setattr(download, "fetch_sha1sums", lambda w: manifest)
        monkeypatch.setattr(
            download,
            "download_with_verify",
            lambda url, dest, sha1: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        monkeypatch.setattr(download, "DUMPS_DIR", tmp_path)

        assert main(["--wiki", WIKI]) == 1

    def test_wiki_flag_is_forwarded(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        received = []

        def fake_fetch(wiki: str) -> dict:
            received.append(wiki)
            # Return valid manifest so main doesn't try to download anything
            manifest = {}
            for suffix in TARGET_SUFFIXES:
                filename = _dump_filename(wiki, suffix)
                data = filename.encode()
                (tmp_path / filename).write_bytes(data)
                manifest[filename] = _sha1(data)
            return manifest

        monkeypatch.setattr(download, "fetch_sha1sums", fake_fetch)
        monkeypatch.setattr(download, "DUMPS_DIR", tmp_path)

        main(["--wiki", "enwiki"])

        assert received == ["enwiki"]
