"""Tests for the SEC EDGAR client."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from investor_research.clients.sec_edgar import (
    SecEdgarClient,
    SecEdgarError,
    reset_ticker_map_cache,
)


@pytest.fixture(autouse=True)
def _reset_edgar_cache() -> None:
    reset_ticker_map_cache()
    yield
    reset_ticker_map_cache()


def _ok(json_body, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body
    response.text = "" if status_code < 400 else "error body"
    return response


_TICKER_MAP = {
    "0": {"ticker": "MSFT", "cik_str": 789019, "title": "MICROSOFT CORP"},
    "1": {"ticker": "AAPL", "cik_str": 320193, "title": "APPLE INC"},
}

_SUBMISSIONS = {
    "name": "MICROSOFT CORP",
    "filings": {
        "recent": {
            "form": ["10-K", "10-Q", "10-Q", "8-K", "DEF 14A", "S-1"],
            "accessionNumber": [
                "0001564590-24-031420",
                "0001564590-24-020112",
                "0001564590-24-001234",
                "0001564590-24-005555",
                "0001564590-23-099999",
                "0001234567-99-000000",
            ],
            "filingDate": ["2024-07-30", "2024-04-25", "2024-01-30", "2024-09-10", "2023-10-17", "1999-01-01"],
            "reportDate": ["2024-06-30", "2024-03-31", "2023-12-31", "", "2023-09-30", ""],
            "primaryDocument": [
                "msft-20240630.htm",
                "msft-20240331.htm",
                "msft-20231231.htm",
                "msft-8k.htm",
                "msft-proxy.htm",
                "msft-s1.htm",
            ],
        }
    },
}


def test_ticker_to_cik(settings_tavily):
    session = MagicMock(spec=requests.Session)
    session.headers = {}
    session.get.return_value = _ok(_TICKER_MAP)
    client = SecEdgarClient(settings_tavily, session=session)

    cik = client.ticker_to_cik("msft")
    assert cik == "0000789019"  # zero-padded to 10 digits


def test_unknown_ticker_raises(settings_tavily):
    session = MagicMock(spec=requests.Session)
    session.headers = {}
    session.get.return_value = _ok(_TICKER_MAP)
    client = SecEdgarClient(settings_tavily, session=session)
    with pytest.raises(SecEdgarError):
        client.ticker_to_cik("ZZZZ")


def test_get_filings_groups_by_form(settings_tavily):
    session = MagicMock(spec=requests.Session)
    session.headers = {}
    session.get.side_effect = [_ok(_TICKER_MAP), _ok(_SUBMISSIONS)]
    client = SecEdgarClient(settings_tavily, session=session)

    result = client.get_filings("MSFT", limit_per_form=4)
    grouped = result["filings"]
    assert len(grouped["10-K"]) == 1
    assert len(grouped["10-Q"]) == 2
    assert len(grouped["8-K"]) == 1
    assert len(grouped["proxy"]) == 1
    # S-1 should be excluded (not in requested forms)
    assert "S-1" not in grouped

    ten_k = grouped["10-K"][0]
    assert ten_k["form"] == "10-K"
    assert ten_k["filing_url"].endswith("msft-20240630.htm")
    assert "000156459024031420" in ten_k["filing_url"]
    assert ten_k["index_url"].endswith("-index.htm")
    assert result["company_name"] == "MICROSOFT CORP"


def test_latest_filing(settings_tavily):
    session = MagicMock(spec=requests.Session)
    session.headers = {}
    session.get.side_effect = [_ok(_TICKER_MAP), _ok(_SUBMISSIONS)]
    client = SecEdgarClient(settings_tavily, session=session)

    latest = client.latest_filing("MSFT", "10-K")
    assert latest is not None
    assert latest.filing_date == "2024-07-30"
    assert latest.primary_document == "msft-20240630.htm"


def test_user_agent_header_set(settings_tavily):
    session = MagicMock(spec=requests.Session)
    session.headers = {}
    SecEdgarClient(settings_tavily, session=session)
    assert "User-Agent" in session.headers
    assert "investor-research" in session.headers["User-Agent"]


def test_retries_on_transient_error(settings_tavily):
    session = MagicMock(spec=requests.Session)
    session.headers = {}
    session.get.side_effect = [requests.ConnectionError("flaky"), _ok(_TICKER_MAP)]
    client = SecEdgarClient(settings_tavily, session=session)
    cik = client.ticker_to_cik("MSFT")
    assert cik == "0000789019"
    assert session.get.call_count == 2
