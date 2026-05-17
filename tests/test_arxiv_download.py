"""Tests for arxiv/download.py — caching, 404 handling, retry."""

import httpx
import pytest
import respx

import paths
from arxiv.download import (
    HTML_URL_TEMPLATE,
    USER_AGENT,
    download_html,
    html_cache_path,
)

_no_sleep = lambda _w: None  # noqa: E731


@pytest.fixture
def isolated_papers_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ARXIV_PAPERS_DIR", tmp_path / "papers")
    return tmp_path / "papers"


def _url(arxiv_id: str) -> str:
    return HTML_URL_TEMPLATE.format(arxiv_id=arxiv_id)


class TestHtmlCachePath:
    def test_uses_paths_dir(self, isolated_papers_dir):
        assert html_cache_path("2310.06825") == isolated_papers_dir / "2310.06825.html"


class TestDownloadHtml:
    @respx.mock
    def test_returns_path_and_writes_body_on_200(self, isolated_papers_dir):
        respx.get(_url("2310.06825")).mock(return_value=httpx.Response(200, text="<html>body</html>"))
        path = download_html("2310.06825", sleep=_no_sleep)
        assert path is not None
        assert path.read_text(encoding="utf-8") == "<html>body</html>"
        assert path.parent == isolated_papers_dir

    @respx.mock
    def test_returns_none_on_404(self, isolated_papers_dir):
        respx.get(_url("0000.00000")).mock(return_value=httpx.Response(404))
        assert download_html("0000.00000", sleep=_no_sleep) is None
        assert not (isolated_papers_dir / "0000.00000.html").exists()

    def test_returns_cache_without_network_when_present(self, isolated_papers_dir):
        isolated_papers_dir.mkdir(parents=True)
        cached = isolated_papers_dir / "2310.06825.html"
        cached.write_text("<html>cached</html>", encoding="utf-8")
        # No respx mock — any HTTP would raise.
        path = download_html("2310.06825", sleep=_no_sleep)
        assert path == cached
        assert path.read_text(encoding="utf-8") == "<html>cached</html>"

    @respx.mock
    def test_force_redownloads_over_cache(self, isolated_papers_dir):
        isolated_papers_dir.mkdir(parents=True)
        (isolated_papers_dir / "2310.06825.html").write_text("<html>stale</html>", encoding="utf-8")
        respx.get(_url("2310.06825")).mock(return_value=httpx.Response(200, text="<html>fresh</html>"))
        path = download_html("2310.06825", force=True, sleep=_no_sleep)
        assert path.read_text(encoding="utf-8") == "<html>fresh</html>"

    @respx.mock
    def test_creates_parent_dir(self, tmp_path, monkeypatch):
        nested = tmp_path / "a" / "b" / "papers"
        monkeypatch.setattr(paths, "ARXIV_PAPERS_DIR", nested)
        respx.get(_url("2310.06825")).mock(return_value=httpx.Response(200, text="x"))
        path = download_html("2310.06825", sleep=_no_sleep)
        assert path is not None
        assert nested.exists()

    @respx.mock
    def test_sends_user_agent(self, isolated_papers_dir):
        route = respx.get(_url("2310.06825")).mock(return_value=httpx.Response(200, text="x"))
        download_html("2310.06825", sleep=_no_sleep)
        assert route.calls[0].request.headers["User-Agent"] == USER_AGENT

    @respx.mock
    def test_no_tmp_file_left_on_success(self, isolated_papers_dir):
        respx.get(_url("2310.06825")).mock(return_value=httpx.Response(200, text="x"))
        download_html("2310.06825", sleep=_no_sleep)
        leftovers = list(isolated_papers_dir.glob("*.tmp"))
        assert not leftovers

    @respx.mock
    def test_sleeps_after_network_fetch(self, isolated_papers_dir):
        respx.get(_url("2310.06825")).mock(return_value=httpx.Response(200, text="x"))
        sleeps: list[float] = []
        download_html("2310.06825", sleep=sleeps.append)
        assert 3.0 in sleeps  # MIN_REQUEST_INTERVAL

    def test_no_sleep_when_returning_cache(self, isolated_papers_dir):
        isolated_papers_dir.mkdir(parents=True)
        (isolated_papers_dir / "2310.06825.html").write_text("x", encoding="utf-8")
        sleeps: list[float] = []
        download_html("2310.06825", sleep=sleeps.append)
        assert sleeps == []


class TestRetry:
    @respx.mock
    def test_retries_on_503_then_succeeds(self, isolated_papers_dir):
        respx.get(_url("2310.06825")).mock(side_effect=[httpx.Response(503), httpx.Response(200, text="ok")])
        sleeps: list[float] = []
        path = download_html("2310.06825", sleep=sleeps.append)
        assert path is not None
        # Backoff + MIN_REQUEST_INTERVAL both fired.
        assert any(s > 0 for s in sleeps)

    @respx.mock
    def test_retries_on_429_honouring_retry_after(self, isolated_papers_dir):
        respx.get(_url("2310.06825")).mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "7"}),
                httpx.Response(200, text="ok"),
            ]
        )
        sleeps: list[float] = []
        download_html("2310.06825", sleep=sleeps.append)
        assert 7.0 in sleeps

    @respx.mock
    def test_raises_after_all_attempts_fail(self, isolated_papers_dir):
        respx.get(_url("2310.06825")).mock(return_value=httpx.Response(503))
        with pytest.raises(httpx.HTTPStatusError):
            download_html("2310.06825", sleep=_no_sleep)
