"""Tests for the Phase 1 research tools (raw-evidence shape)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from investor_research.clients.fmp import FmpSnapshot
from investor_research.models import ResearchEvidence, ResearchResult, Source
from investor_research.tools.research import (
    InvalidTickerError,
    _validate_ticker,
    get_competitors,
    get_earnings_summary,
    get_recent_news,
    get_risks,
    research_company,
)

_PROVIDER_PATH = "investor_research.tools.research.make_research_provider"
_FMP_PATH = "investor_research.tools.research.FmpClient"


# ---- ticker validation -------------------------------------------------


@pytest.mark.parametrize("good", ["MSFT", "brk.b", "GOOG", "AAPL", "JPM"])
def test_valid_tickers_normalize(good: str) -> None:
    assert _validate_ticker(good) == good.upper()


@pytest.mark.parametrize("bad", ["", "   ", "1MSFT", "TOOLONGTICKER!", "MS FT", None])
def test_invalid_tickers_raise(bad) -> None:
    with pytest.raises(InvalidTickerError):
        _validate_ticker(bad)  # type: ignore[arg-type]


# ---- helpers -----------------------------------------------------------


def _patched(monkeypatch, settings, evidence, snapshot):
    fake_provider = MagicMock()
    fake_provider.search.return_value = evidence
    fake_fmp = MagicMock()
    fake_fmp.fetch_snapshot.return_value = snapshot

    monkeypatch.setattr(_PROVIDER_PATH, lambda s: fake_provider)
    monkeypatch.setattr(_FMP_PATH, lambda *a, **kw: fake_fmp)
    monkeypatch.setattr(
        "investor_research.tools.research.get_settings", lambda: settings
    )
    return fake_provider, fake_fmp


def _evidence(urls=("https://sec.gov/foo",), answer="An answer.") -> ResearchEvidence:
    return ResearchEvidence(
        provider="tavily",
        query="test",
        answer=answer,
        results=[ResearchResult(url=u, content="snippet") for u in urls],
        sources=[Source(url=u, publisher="sec.gov") for u in urls],
    )


# ---- research_company --------------------------------------------------


def test_research_company_happy_path(monkeypatch, settings_tavily_with_fmp):
    snap = FmpSnapshot(
        ticker="MSFT", profile={"companyName": "Microsoft"}, quote={"price": 400.0}
    )
    _patched(monkeypatch, settings_tavily_with_fmp, _evidence(), snap)

    out = research_company("msft")
    assert out["ticker"] == "MSFT"
    assert out["provider"] == "tavily"
    assert out["answer"] == "An answer."
    assert out["results"][0]["url"] == "https://sec.gov/foo"
    urls = [s["url"] for s in out["sources"]]
    assert urls[0].startswith("https://financialmodelingprep.com/")
    assert "https://sec.gov/foo" in urls
    assert out["fmp_snapshot"]["profile"]["companyName"] == "Microsoft"
    assert json.dumps(out)  # serializable


def test_research_company_propagates_invalid_ticker():
    with pytest.raises(InvalidTickerError):
        research_company("1bad")


# ---- get_recent_news ---------------------------------------------------


def test_get_recent_news_validates_days(monkeypatch, settings_tavily):
    monkeypatch.setattr(
        "investor_research.tools.research.get_settings", lambda: settings_tavily
    )
    with pytest.raises(ValueError):
        get_recent_news("MSFT", days=0)
    with pytest.raises(ValueError):
        get_recent_news("MSFT", days=999)


def test_get_recent_news_passes_days_to_provider(monkeypatch, settings_tavily):
    snap = FmpSnapshot(ticker="MSFT")
    fake_provider, _ = _patched(monkeypatch, settings_tavily, _evidence(), snap)
    out = get_recent_news("MSFT", days=14)
    fake_provider.search.assert_called_once()
    assert fake_provider.search.call_args.kwargs["recency_days"] == 14
    assert "news_window_days=14" in out["notes"]


# ---- earnings / competitors / risks ------------------------------------


def test_get_earnings_summary_includes_fmp_source(monkeypatch, settings_tavily_with_fmp):
    snap = FmpSnapshot(ticker="MSFT", quote={"price": 400.0})
    _patched(monkeypatch, settings_tavily_with_fmp, _evidence(), snap)
    out = get_earnings_summary("MSFT")
    publishers = [s.get("publisher") for s in out["sources"]]
    assert "Financial Modeling Prep" in publishers


def test_get_competitors_passes_peers_hint(monkeypatch, settings_tavily_with_fmp):
    snap = FmpSnapshot(ticker="MSFT", peers=["AAPL", "GOOGL"])
    fake_provider, _ = _patched(monkeypatch, settings_tavily_with_fmp, _evidence(), snap)
    get_competitors("MSFT")
    query = fake_provider.search.call_args.args[0]
    assert "AAPL, GOOGL" in query


def test_get_risks_returns_evidence(monkeypatch, settings_tavily):
    fake_provider, _ = _patched(
        monkeypatch, settings_tavily, _evidence(), FmpSnapshot(ticker="MSFT")
    )
    out = get_risks("MSFT")
    issued_query = fake_provider.search.call_args.args[0]
    assert issued_query.startswith("Material investment risks")
    assert out["sources"][0]["url"] == "https://sec.gov/foo"


# ---- failure modes -----------------------------------------------------


def test_provider_failure_propagates(monkeypatch, settings_tavily):
    fake_provider = MagicMock()
    fake_provider.search.side_effect = RuntimeError("upstream down")
    fake_fmp = MagicMock()
    fake_fmp.fetch_snapshot.return_value = FmpSnapshot(ticker="MSFT")
    monkeypatch.setattr(_PROVIDER_PATH, lambda s: fake_provider)
    monkeypatch.setattr(_FMP_PATH, lambda *a, **kw: fake_fmp)
    monkeypatch.setattr(
        "investor_research.tools.research.get_settings", lambda: settings_tavily
    )
    with pytest.raises(RuntimeError):
        research_company("MSFT")
