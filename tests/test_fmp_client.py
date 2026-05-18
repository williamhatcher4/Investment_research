"""Tests for the FMP client."""

from __future__ import annotations

from unittest.mock import MagicMock

import requests

from investor_research.clients.fmp import FmpClient


def _fake_response(json_body, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body
    response.text = "" if status_code < 400 else "error body"
    return response


def test_disabled_when_key_missing(settings_tavily):
    client = FmpClient(settings_tavily)
    assert client.enabled is False
    snap = client.fetch_snapshot("MSFT")
    assert snap.has_data() is False
    assert snap.to_source() is None


def test_fetch_snapshot_assembles_all_endpoints(settings_tavily_with_fmp):
    session = MagicMock(spec=requests.Session)
    session.get.side_effect = [
        _fake_response([{"companyName": "Microsoft", "sector": "Tech"}]),
        _fake_response([{"price": 400.0, "marketCap": 3_000_000_000_000}]),
        _fake_response([{"date": "2026-03-31", "revenue": 60_000}]),
        _fake_response([{"date": "2026-03-31", "totalDebt": 50_000}]),
        _fake_response([{"enterpriseValueOverEBITDATTM": 22.0}]),
        _fake_response([{"peersList": ["AAPL", "GOOGL"]}]),
    ]
    client = FmpClient(settings_tavily_with_fmp, session=session)

    snap = client.fetch_snapshot("MSFT")
    assert snap.profile["companyName"] == "Microsoft"
    assert snap.quote["price"] == 400.0
    assert snap.income_statement[0]["revenue"] == 60_000
    assert snap.balance_sheet[0]["totalDebt"] == 50_000
    assert snap.key_metrics_ttm["enterpriseValueOverEBITDATTM"] == 22.0
    assert snap.peers == ["AAPL", "GOOGL"]
    assert snap.has_data() is True
    assert snap.to_source().publisher == "Financial Modeling Prep"


def test_partial_failure_degrades_gracefully(settings_tavily_with_fmp):
    session = MagicMock(spec=requests.Session)
    session.get.side_effect = [
        _fake_response([{"companyName": "Microsoft"}]),
        _fake_response([], status_code=404),  # quote fails
        _fake_response([{"date": "2026-03-31", "revenue": 60_000}]),
        _fake_response([{"date": "2026-03-31"}]),
        _fake_response([{}]),
        _fake_response([{"peersList": []}]),
    ]
    client = FmpClient(settings_tavily_with_fmp, session=session)
    snap = client.fetch_snapshot("MSFT")
    assert snap.profile is not None
    assert snap.quote is None  # failed endpoint, others survived
    assert snap.income_statement is not None


def test_retries_on_transient_error(settings_tavily_with_fmp):
    session = MagicMock(spec=requests.Session)
    transient = requests.ConnectionError("temporary")
    success = _fake_response([{"companyName": "Microsoft"}])
    session.get.side_effect = [transient, success]

    client = FmpClient(settings_tavily_with_fmp, session=session)
    profile = client._safe_get("/profile/MSFT", first=True)
    assert profile == {"companyName": "Microsoft"}
    assert session.get.call_count == 2


def test_to_prompt_block_renders_human_readable(settings_tavily_with_fmp):
    session = MagicMock(spec=requests.Session)
    session.get.side_effect = [
        _fake_response([{"companyName": "Microsoft", "sector": "Tech", "description": "Software."}]),
        _fake_response([{"price": 400.0, "marketCap": 3_000_000_000_000, "pe": 35.0, "eps": 11.0}]),
        _fake_response([{"date": "2026-03-31", "period": "Q3", "revenue": 60_000, "eps": 2.8}]),
        _fake_response([{"date": "2026-03-31", "totalDebt": 50_000}]),
        _fake_response([{"enterpriseValueOverEBITDATTM": 22.0, "freeCashFlowYieldTTM": 0.03}]),
        _fake_response([{"peersList": ["AAPL"]}]),
    ]
    client = FmpClient(settings_tavily_with_fmp, session=session)
    snap = client.fetch_snapshot("MSFT")
    block = snap.to_prompt_block()
    assert "Microsoft" in block
    assert "revenue=60000" in block
    assert "Peers (FMP): AAPL" in block
