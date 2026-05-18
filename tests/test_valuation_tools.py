"""Tests for tools/valuation.py."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from investor_research.clients.fmp import FmpSnapshot
from investor_research.models import ResearchEvidence, ResearchResult, Source
from investor_research.tools import valuation as v
from investor_research.tools.research import InvalidTickerError


def _snap(ticker: str, *, revenue=100.0, gross=40.0, op=25.0, net=15.0, pe=30.0, ev_ebitda=20.0, peers=None) -> FmpSnapshot:
    return FmpSnapshot(
        ticker=ticker,
        profile={"companyName": f"{ticker} Inc"},
        quote={"price": 100.0, "marketCap": 1_000_000_000, "pe": pe},
        income_statement=[{"date": "2026-03-31", "revenue": revenue, "grossProfit": gross, "operatingIncome": op, "netIncome": net}],
        balance_sheet=[{"date": "2026-03-31", "totalDebt": 50.0}],
        key_metrics_ttm={"enterpriseValueOverEBITDATTM": ev_ebitda, "freeCashFlowYieldTTM": 0.04, "netDebtToEBITDATTM": 1.5, "debtToEquityTTM": 0.5},
        peers=peers,
    )


def _evidence() -> ResearchEvidence:
    return ResearchEvidence(
        provider="tavily",
        query="q",
        answer="comp answer",
        results=[ResearchResult(url="https://sec.gov/a")],
        sources=[Source(url="https://sec.gov/a", publisher="sec.gov")],
    )


# ---- compare_peers -----------------------------------------------------


def test_compare_peers_requires_non_empty_list(monkeypatch, settings_tavily):
    monkeypatch.setattr(v, "get_settings", lambda: settings_tavily)
    with pytest.raises(ValueError):
        v.compare_peers([])


def test_compare_peers_rejects_too_many(monkeypatch, settings_tavily):
    monkeypatch.setattr(v, "get_settings", lambda: settings_tavily)
    with pytest.raises(ValueError):
        v.compare_peers([f"T{i}" for i in range(9)])


def test_compare_peers_propagates_invalid_ticker(monkeypatch, settings_tavily):
    monkeypatch.setattr(v, "get_settings", lambda: settings_tavily)
    with pytest.raises(InvalidTickerError):
        v.compare_peers(["MSFT", "1bad"])


def test_compare_peers_builds_rows(monkeypatch, settings_tavily):
    fake_fmp = MagicMock()
    fake_fmp.fetch_snapshot.side_effect = lambda t: _snap(t)
    monkeypatch.setattr(v, "FmpClient", lambda settings: fake_fmp)

    fake_provider = MagicMock()
    fake_provider.search.return_value = _evidence()
    monkeypatch.setattr(v, "make_research_provider", lambda s: fake_provider)
    monkeypatch.setattr(v, "get_settings", lambda: settings_tavily)

    out = v.compare_peers(["MSFT", "AAPL"])
    assert out["tickers"] == ["MSFT", "AAPL"]
    rows = {row["ticker"]: row for row in out["structured_rows"]}
    assert rows["MSFT"]["gross_margin"] == pytest.approx(0.4)
    assert rows["MSFT"]["operating_margin"] == pytest.approx(0.25)
    assert rows["AAPL"]["pe"] == 30.0
    assert out["answer"] == "comp answer"


# ---- valuation_snapshot ------------------------------------------------


def test_valuation_snapshot_computes_peer_averages(monkeypatch, settings_tavily):
    subject = _snap("MSFT", pe=35.0, ev_ebitda=25.0, peers=["AAPL", "GOOGL"])
    peers = {"AAPL": _snap("AAPL", pe=28.0, ev_ebitda=18.0), "GOOGL": _snap("GOOGL", pe=22.0, ev_ebitda=14.0)}

    fake_fmp = MagicMock()
    fake_fmp.fetch_snapshot.side_effect = lambda t: subject if t == "MSFT" else peers[t]
    monkeypatch.setattr(v, "FmpClient", lambda settings: fake_fmp)

    fake_provider = MagicMock()
    fake_provider.search.return_value = _evidence()
    monkeypatch.setattr(v, "make_research_provider", lambda s: fake_provider)
    monkeypatch.setattr(v, "get_settings", lambda: settings_tavily)

    out = v.valuation_snapshot("MSFT")
    assert out["subject"]["pe"] == 35.0
    assert out["peer_averages"]["pe"] == pytest.approx((28.0 + 22.0) / 2)
    assert out["peer_averages"]["ev_to_ebitda"] == pytest.approx((18.0 + 14.0) / 2)
    assert out["peer_averages"]["n_peers"] == 2
    assert len(out["peers"]) == 2


def test_valuation_snapshot_handles_no_peers(monkeypatch, settings_tavily):
    subject = _snap("MSFT", peers=None)

    fake_fmp = MagicMock()
    fake_fmp.fetch_snapshot.return_value = subject
    monkeypatch.setattr(v, "FmpClient", lambda settings: fake_fmp)

    fake_provider = MagicMock()
    fake_provider.search.return_value = _evidence()
    monkeypatch.setattr(v, "make_research_provider", lambda s: fake_provider)
    monkeypatch.setattr(v, "get_settings", lambda: settings_tavily)

    out = v.valuation_snapshot("MSFT")
    assert out["peers"] == []
    assert out["peer_averages"] == {}
    assert any("No FMP peer list" in n for n in out["notes"])
