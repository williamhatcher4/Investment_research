"""Phase 2 valuation tools: compare_peers and valuation_snapshot."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from statistics import mean
from typing import Any

from investor_research.clients.fmp import FmpClient, FmpSnapshot
from investor_research.clients.research_provider import make_research_provider
from investor_research.config import Settings, get_settings
from investor_research.prompts.research import COMPARE_PEERS_QUERY, VALUATION_PEERS_QUERY
from investor_research.tools.research import _validate_ticker

logger = logging.getLogger(__name__)


# ---- compare_peers -----------------------------------------------------


def compare_peers(tickers: list[str]) -> dict[str, Any]:
    """Side-by-side comparison across revenue growth, margins, valuation,
    capital allocation, and competitive moat.
    """
    validated = _validate_ticker_list(tickers)
    settings = get_settings()

    fmp_by_ticker = _parallel_fmp_snapshots(settings, validated)

    provider = make_research_provider(settings)
    query = COMPARE_PEERS_QUERY.format(tickers_csv=", ".join(validated))
    evidence = provider.search(query, max_results=10)

    rows = [_valuation_row(t, fmp_by_ticker[t]) for t in validated]

    return {
        "tickers": validated,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "provider": settings.research_provider,
        "structured_rows": rows,
        "query": evidence.query,
        "answer": evidence.answer,
        "results": [r.model_dump(mode="json") for r in evidence.results],
        "sources": [s.model_dump(mode="json") for s in evidence.sources],
    }


# ---- valuation_snapshot ------------------------------------------------


def valuation_snapshot(ticker: str) -> dict[str, Any]:
    """Return current valuation multiples for ``ticker`` plus peer averages."""
    ticker = _validate_ticker(ticker)
    settings = get_settings()
    fmp = FmpClient(settings)
    snapshot = fmp.fetch_snapshot(ticker)

    peer_tickers = (snapshot.peers or [])[:5]
    peer_snapshots: dict[str, FmpSnapshot] = {}
    if peer_tickers:
        peer_snapshots = _parallel_fmp_snapshots(settings, peer_tickers)

    own_row = _valuation_row(ticker, snapshot)
    peer_rows = [_valuation_row(t, peer_snapshots[t]) for t in peer_tickers]
    peer_avg = _average_row(peer_rows)

    provider = make_research_provider(settings)
    query = VALUATION_PEERS_QUERY.format(ticker=ticker)
    evidence = provider.search(query, max_results=8)

    return {
        "ticker": ticker,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "provider": settings.research_provider,
        "subject": own_row,
        "peers": peer_rows,
        "peer_averages": peer_avg,
        "query": evidence.query,
        "answer": evidence.answer,
        "sources": [s.model_dump(mode="json") for s in evidence.sources],
        "results": [r.model_dump(mode="json") for r in evidence.results],
        "notes": _snapshot_notes(snapshot, peer_tickers),
    }


# ---- helpers -----------------------------------------------------------


def _validate_ticker_list(tickers: list[str]) -> list[str]:
    if not isinstance(tickers, list) or not tickers:
        raise ValueError("tickers must be a non-empty list of symbols")
    if len(tickers) > 8:
        raise ValueError("at most 8 tickers per compare_peers call")
    return [_validate_ticker(t) for t in tickers]


def _parallel_fmp_snapshots(
    settings: Settings, tickers: list[str]
) -> dict[str, FmpSnapshot]:
    fmp = FmpClient(settings)
    out: dict[str, FmpSnapshot] = {}
    with ThreadPoolExecutor(max_workers=min(len(tickers), 5)) as pool:
        futures = {pool.submit(fmp.fetch_snapshot, t): t for t in tickers}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                out[ticker] = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.warning("fmp.snapshot_failed", extra={"ticker": ticker, "err": str(exc)[:200]})
                out[ticker] = FmpSnapshot(ticker=ticker)
    return out


def _valuation_row(ticker: str, snapshot: FmpSnapshot) -> dict[str, Any]:
    quote = snapshot.quote or {}
    metrics = snapshot.key_metrics_ttm or {}
    income = (snapshot.income_statement or [{}])[0] if snapshot.income_statement else {}
    revenue = _safe_get(income, "revenue")
    gross_profit = _safe_get(income, "grossProfit")
    operating_income = _safe_get(income, "operatingIncome")
    net_income = _safe_get(income, "netIncome")

    return {
        "ticker": ticker,
        "name": _safe_get(snapshot.profile or {}, "companyName"),
        "price": _safe_get(quote, "price"),
        "market_cap": _safe_get(quote, "marketCap"),
        "pe": _safe_get(quote, "pe"),
        "ev_to_ebitda": _safe_get(metrics, "enterpriseValueOverEBITDATTM"),
        "fcf_yield": _safe_get(metrics, "freeCashFlowYieldTTM"),
        "net_debt_to_ebitda": _safe_get(metrics, "netDebtToEBITDATTM"),
        "debt_to_equity": _safe_get(metrics, "debtToEquityTTM"),
        "gross_margin": _ratio(gross_profit, revenue),
        "operating_margin": _ratio(operating_income, revenue),
        "net_margin": _ratio(net_income, revenue),
        "revenue_ttm": revenue,
        "has_fmp_data": snapshot.has_data(),
    }


def _average_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    numeric_fields = (
        "pe",
        "ev_to_ebitda",
        "fcf_yield",
        "net_debt_to_ebitda",
        "debt_to_equity",
        "gross_margin",
        "operating_margin",
        "net_margin",
    )
    averages: dict[str, Any] = {}
    for field_name in numeric_fields:
        values = [r[field_name] for r in rows if isinstance(r.get(field_name), (int, float))]
        averages[field_name] = mean(values) if values else None
    averages["n_peers"] = len(rows)
    return averages


def _safe_get(d: dict[str, Any], key: str) -> Any:
    value = d.get(key)
    if isinstance(value, (int, float)) or isinstance(value, str):
        return value
    return None


def _ratio(numerator: Any, denominator: Any) -> float | None:
    if not isinstance(numerator, (int, float)) or not isinstance(denominator, (int, float)):
        return None
    if denominator == 0:
        return None
    return numerator / denominator


def _snapshot_notes(snapshot: FmpSnapshot, peer_tickers: list[str]) -> list[str]:
    notes: list[str] = []
    if not snapshot.has_data():
        notes.append("FMP data unavailable for subject ticker.")
    if not peer_tickers:
        notes.append("No FMP peer list available — peer averages omitted.")
    return notes
