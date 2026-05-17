"""Tests for arxiv/oai.py — OAI-PMH parsing, resumption tokens, caching, retry."""

import httpx
import pytest
import respx

from arxiv.oai import (
    OAI_ENDPOINT,
    OAIError,
    cache_filename,
    fetch_page,
    harvest_records,
    parse_record,
)

_no_sleep = lambda _w: None  # noqa: E731


def _wrap(body: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <responseDate>2024-01-22T00:00:00Z</responseDate>
  <request verb="ListRecords" metadataPrefix="arXiv" from="2024-01-01">https://export.arxiv.org/oai2</request>
  {body}
</OAI-PMH>"""


def _record(
    arxiv_id: str = "2401.12345",
    datestamp: str = "2024-01-22",
    title: str = "Test Paper",
    abstract: str = "Abstract body.",
    categories: str = "cs.CL cs.LG",
    created: str = "2024-01-22",
    updated: str | None = "2024-01-25",
    deleted: bool = False,
) -> str:
    if deleted:
        return f"""
    <record>
      <header status="deleted">
        <identifier>oai:arXiv.org:{arxiv_id}</identifier>
        <datestamp>{datestamp}</datestamp>
      </header>
    </record>"""
    updated_el = f"<updated>{updated}</updated>" if updated else ""
    return f"""
    <record>
      <header>
        <identifier>oai:arXiv.org:{arxiv_id}</identifier>
        <datestamp>{datestamp}</datestamp>
        <setSpec>cs</setSpec>
      </header>
      <metadata>
        <arXiv xmlns="http://arxiv.org/OAI/arXiv/">
          <id>{arxiv_id}</id>
          <created>{created}</created>
          {updated_el}
          <authors>
            <author>
              <keyname>Smith</keyname>
              <forenames>Alice</forenames>
            </author>
            <author>
              <keyname>Jones</keyname>
              <forenames>Bob C.</forenames>
            </author>
          </authors>
          <title>{title}</title>
          <categories>{categories}</categories>
          <comments>9 pages</comments>
          <abstract>{abstract}</abstract>
        </arXiv>
      </metadata>
    </record>"""


def _page(records_xml: str, resumption_token: str | None = None) -> str:
    token_el = f"<resumptionToken>{resumption_token}</resumptionToken>" if resumption_token else ""
    return _wrap(f"<ListRecords>{records_xml}{token_el}</ListRecords>")


def _error_page(code: str, message: str = "") -> str:
    return _wrap(f'<error code="{code}">{message}</error>')


class TestCacheFilename:
    def test_stable_across_param_order(self):
        a = {"verb": "ListRecords", "metadataPrefix": "arXiv", "from": "2024-01-01"}
        b = {"from": "2024-01-01", "metadataPrefix": "arXiv", "verb": "ListRecords"}
        assert cache_filename(a) == cache_filename(b)

    def test_different_params_different_name(self):
        a = {"verb": "ListRecords", "from": "2024-01-01"}
        b = {"verb": "ListRecords", "from": "2024-01-02"}
        assert cache_filename(a) != cache_filename(b)

    def test_ends_in_xml(self):
        assert cache_filename({"verb": "ListRecords"}).endswith(".xml")


class TestParseRecord:
    def test_parses_basic_fields(self):
        import xml.etree.ElementTree as ET

        root = ET.fromstring(_wrap("<ListRecords>" + _record() + "</ListRecords>"))
        record_el = root.find(
            "{http://www.openarchives.org/OAI/2.0/}ListRecords/{http://www.openarchives.org/OAI/2.0/}record"
        )
        parsed = parse_record(record_el)
        assert parsed["id"] == "2401.12345"
        assert parsed["title"] == "Test Paper"
        assert parsed["abstract"] == "Abstract body."
        assert parsed["categories"] == "cs.CL cs.LG"
        assert parsed["primary_category"] == "cs.CL"
        assert parsed["submitted_date"] == "2024-01-22"
        assert parsed["updated_date"] == "2024-01-25"
        assert parsed["oai_datestamp"] == "2024-01-22"

    def test_parses_authors_as_list(self):
        import xml.etree.ElementTree as ET

        root = ET.fromstring(_wrap("<ListRecords>" + _record() + "</ListRecords>"))
        record_el = root.find(
            "{http://www.openarchives.org/OAI/2.0/}ListRecords/{http://www.openarchives.org/OAI/2.0/}record"
        )
        parsed = parse_record(record_el)
        assert parsed["authors"] == ["Alice Smith", "Bob C. Jones"]

    def test_returns_none_for_deleted(self):
        import xml.etree.ElementTree as ET

        root = ET.fromstring(_wrap("<ListRecords>" + _record(deleted=True) + "</ListRecords>"))
        record_el = root.find(
            "{http://www.openarchives.org/OAI/2.0/}ListRecords/{http://www.openarchives.org/OAI/2.0/}record"
        )
        assert parse_record(record_el) is None

    def test_collapses_whitespace_in_title_and_abstract(self):
        import xml.etree.ElementTree as ET

        body = _record(title="  Many   spaces\n  here  ", abstract="Multi\n  line\n abstract  text")
        root = ET.fromstring(_wrap("<ListRecords>" + body + "</ListRecords>"))
        record_el = root.find(
            "{http://www.openarchives.org/OAI/2.0/}ListRecords/{http://www.openarchives.org/OAI/2.0/}record"
        )
        parsed = parse_record(record_el)
        assert parsed["title"] == "Many spaces here"
        assert parsed["abstract"] == "Multi line abstract text"

    def test_missing_updated_becomes_none(self):
        import xml.etree.ElementTree as ET

        body = _record(updated=None)
        root = ET.fromstring(_wrap("<ListRecords>" + body + "</ListRecords>"))
        record_el = root.find(
            "{http://www.openarchives.org/OAI/2.0/}ListRecords/{http://www.openarchives.org/OAI/2.0/}record"
        )
        parsed = parse_record(record_el)
        assert parsed["updated_date"] is None


class TestHarvestRecords:
    @respx.mock
    def test_single_page(self):
        respx.get(OAI_ENDPOINT).mock(return_value=httpx.Response(200, text=_page(_record())))
        records = list(harvest_records("2024-01-01", sleep=_no_sleep))
        assert len(records) == 1
        assert records[0]["id"] == "2401.12345"

    @respx.mock
    def test_walks_resumption_token(self):
        first = _page(_record(arxiv_id="2401.0001"), resumption_token="token-abc")
        second = _page(_record(arxiv_id="2401.0002"))
        respx.get(OAI_ENDPOINT).mock(side_effect=[httpx.Response(200, text=first), httpx.Response(200, text=second)])
        records = list(harvest_records("2024-01-01", sleep=_no_sleep))
        assert [r["id"] for r in records] == ["2401.0001", "2401.0002"]

    @respx.mock
    def test_empty_resumption_token_terminates(self):
        page = _wrap(f"<ListRecords>{_record()}<resumptionToken></resumptionToken></ListRecords>")
        respx.get(OAI_ENDPOINT).mock(return_value=httpx.Response(200, text=page))
        records = list(harvest_records("2024-01-01", sleep=_no_sleep))
        assert len(records) == 1

    @respx.mock
    def test_deleted_records_skipped(self):
        body = _record(arxiv_id="2401.0001") + _record(arxiv_id="2401.0002", deleted=True)
        respx.get(OAI_ENDPOINT).mock(return_value=httpx.Response(200, text=_page(body)))
        records = list(harvest_records("2024-01-01", sleep=_no_sleep))
        assert [r["id"] for r in records] == ["2401.0001"]

    @respx.mock
    def test_no_records_match_returns_empty(self):
        respx.get(OAI_ENDPOINT).mock(return_value=httpx.Response(200, text=_error_page("noRecordsMatch", "no match")))
        records = list(harvest_records("2024-01-01", sleep=_no_sleep))
        assert records == []

    @respx.mock
    def test_other_oai_error_raises(self):
        respx.get(OAI_ENDPOINT).mock(return_value=httpx.Response(200, text=_error_page("badArgument", "bad")))
        with pytest.raises(OAIError) as exc:
            list(harvest_records("2024-01-01", sleep=_no_sleep))
        assert exc.value.code == "badArgument"

    @respx.mock
    def test_until_date_added_to_params(self):
        route = respx.get(OAI_ENDPOINT).mock(return_value=httpx.Response(200, text=_page(_record())))
        list(harvest_records("2024-01-01", until_date="2024-01-31", sleep=_no_sleep))
        call_url = str(route.calls[0].request.url)
        assert "until=2024-01-31" in call_url
        assert "from=2024-01-01" in call_url

    @respx.mock
    def test_resumption_continues_without_other_params(self):
        first = _page(_record(arxiv_id="2401.0001"), resumption_token="abc")
        second = _page(_record(arxiv_id="2401.0002"))
        route = respx.get(OAI_ENDPOINT).mock(
            side_effect=[httpx.Response(200, text=first), httpx.Response(200, text=second)]
        )
        list(harvest_records("2024-01-01", sleep=_no_sleep))
        second_url = str(route.calls[1].request.url)
        # On resumption, only verb + resumptionToken should be present
        assert "resumptionToken=abc" in second_url
        assert "metadataPrefix" not in second_url
        assert "from=" not in second_url


class TestFetchPage:
    @respx.mock
    def test_writes_to_cache(self, tmp_path):
        respx.get(OAI_ENDPOINT).mock(return_value=httpx.Response(200, text=_page(_record())))
        cache = tmp_path / "cache"
        fetch_page(
            OAI_ENDPOINT,
            {"verb": "ListRecords", "from": "2024-01-01"},
            cache_dir=cache,
            sleep=_no_sleep,
        )
        files = list(cache.glob("*.xml"))
        assert len(files) == 1

    def test_reads_from_cache_without_network(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        params = {"verb": "ListRecords", "from": "2024-01-01"}
        (cache / cache_filename(params)).write_text(_page(_record()), encoding="utf-8")
        # No respx mock — any HTTP call would raise.
        text = fetch_page(OAI_ENDPOINT, params, cache_dir=cache, sleep=_no_sleep)
        assert "2401.12345" in text

    @respx.mock
    def test_sleeps_after_network_fetch(self, tmp_path):
        respx.get(OAI_ENDPOINT).mock(return_value=httpx.Response(200, text=_page(_record())))
        sleeps: list[float] = []
        fetch_page(
            OAI_ENDPOINT,
            {"verb": "ListRecords", "from": "2024-01-01"},
            cache_dir=tmp_path / "cache",
            sleep=sleeps.append,
        )
        # Rate-limit sleep should have fired once at the standard interval.
        assert 3.0 in sleeps


class TestRetry:
    @respx.mock
    def test_retries_on_503_then_succeeds(self):
        respx.get(OAI_ENDPOINT).mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(200, text=_page(_record())),
            ]
        )
        sleeps: list[float] = []
        records = list(harvest_records("2024-01-01", sleep=sleeps.append))
        assert len(records) == 1
        # At least one backoff sleep happened before the success.
        assert sleeps

    @respx.mock
    def test_retries_on_429_honoring_retry_after(self):
        respx.get(OAI_ENDPOINT).mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "7"}),
                httpx.Response(200, text=_page(_record())),
            ]
        )
        sleeps: list[float] = []
        records = list(harvest_records("2024-01-01", sleep=sleeps.append))
        assert len(records) == 1
        assert 7.0 in sleeps

    @respx.mock
    def test_raises_after_all_attempts_fail(self):
        respx.get(OAI_ENDPOINT).mock(return_value=httpx.Response(503))
        with pytest.raises(httpx.HTTPStatusError):
            list(harvest_records("2024-01-01", sleep=_no_sleep))
