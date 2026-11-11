"""Tests for tools/valuation.py."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from investor_research.clients.fmp import FmpSnapshot
from investor_research.models import FundamentalsSnapshot
from investor_research.models import ResearchEvidence, ResearchResult, Source
from investor_research.tools import valuation as v
from investor_research.tools.research import InvalidTickerError
from investor_research.vault.paths import create_company_folder
from investor_research.vault.snapshots import write_parsed_json


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
    fake_fmp.fetch_snapshot.side_effect = lambda t, **kwargs: _snap(t)
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
    fake_fmp.fetch_snapshot.side_effect = lambda t, **kwargs: subject if t == "MSFT" else peers[t]
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


def test_valuation_snapshot_uses_curated_peer_set(monkeypatch, settings_with_vault):
    create_company_folder(settings_with_vault, "MSFT")
    from investor_research.tools import vault_tools

    monkeypatch.setattr(vault_tools, "get_settings", lambda: settings_with_vault)
    vault_tools.write_peer_set_tool("software", ["AAPL", "GOOGL"])

    subject = _snap("MSFT", pe=35.0, peers=["ORCL"])
    peers = {"AAPL": _snap("AAPL", pe=28.0), "GOOGL": _snap("GOOGL", pe=22.0)}
    fake_fmp = MagicMock()
    fake_fmp.fetch_snapshot.side_effect = lambda t, **kwargs: subject if t == "MSFT" else peers[t]
    monkeypatch.setattr(v, "FmpClient", lambda settings: fake_fmp)

    fake_provider = MagicMock()
    fake_provider.search.return_value = _evidence()
    monkeypatch.setattr(v, "make_research_provider", lambda s: fake_provider)
    monkeypatch.setattr(v, "get_settings", lambda: settings_with_vault)

    out = v.valuation_snapshot("MSFT", peer_set="software")
    assert [row["ticker"] for row in out["peers"]] == ["AAPL", "GOOGL"]
    assert out["peer_set"] == "software"


def test_dcf_scenario_analysis_from_vault(monkeypatch, settings_with_vault):
    create_company_folder(settings_with_vault, "NKE")
    write_parsed_json(
        settings_with_vault,
        FundamentalsSnapshot(
            ticker="NKE",
            periods=["FY2025"],
            dcf={
                "target_price": 120.0,
                "intrinsic_value_per_share": 100.0,
                "current_share_price": 80.0,
                "fcf_growth_projection_10yr": 0.06,
            },
        ),
    )
    monkeypatch.setattr(v, "get_settings", lambda: settings_with_vault)

    out = v.dcf_scenario_analysis("NKE")
    assert out["ok"] is True
    assert out["scenarios"][1]["scenario"] == "base"
    assert out["scenarios"][1]["upside_to_current"] == pytest.approx(0.5)
    # No FCF series in the vault, so a real reverse DCF can't be solved.
    assert out["reverse_dcf"]["available"] is False


def test_dcf_scenario_reverse_dcf_reproduces_price(monkeypatch, settings_with_vault):
    create_company_folder(settings_with_vault, "NKE")
    write_parsed_json(
        settings_with_vault,
        FundamentalsSnapshot(
            ticker="NKE",
            units="millions",
            periods=["FY2024", "FY2025"],
            series={"free_cash_flow": [4500.0, 5000.0], "shares_outstanding": [1500.0, 1250.0]},
            dcf={"target_price": 120.0, "current_share_price": 80.0,
                 "discount_rate": 0.09, "terminal_growth_rate": 0.03},
        ),
    )
    monkeypatch.setattr(v, "get_settings", lambda: settings_with_vault)

    rev = v.dcf_scenario_analysis("NKE")["reverse_dcf"]
    assert rev["available"] is True
    assert rev["base_fcf_per_share"] == pytest.approx(4.0)
    g = rev["implied_growth_rate_yr1_5"]
    value = v._compute_dcf_per_share(4.0, g, g * 0.7, 0.09, 0.03)["intrinsic_value_per_share"]
    assert value == pytest.approx(80.0, rel=1e-3)


def test_data_cross_check_flags_large_difference(monkeypatch, settings_with_vault):
    create_company_folder(settings_with_vault, "NKE")
    write_parsed_json(
        settings_with_vault,
        FundamentalsSnapshot(
            ticker="NKE",
            periods=["FY2025"],
            units="millions",
            series={"revenue": [100.0], "net_income": [10.0]},
        ),
    )
    fake_fmp = MagicMock()
    fake_fmp.fetch_snapshot.return_value = FmpSnapshot(
        ticker="NKE",
        income_statement=[{"revenue": 80_000_000.0, "netIncome": 10_000_000.0}],
    )
    monkeypatch.setattr(v, "FmpClient", lambda settings: fake_fmp)
    monkeypatch.setattr(v, "get_settings", lambda: settings_with_vault)

    out = v.data_cross_check("NKE")
    revenue = next(row for row in out["rows"] if row["metric"] == "revenue")
    assert revenue["flag"] is True
