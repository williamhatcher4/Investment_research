"""Phase 1 research tools (raw-evidence mode).

Each tool:
1. Validates input.
2. Fetches an FMP snapshot (best effort, optional).
3. Issues one search via the configured ResearchProvider.
4. Returns the combined evidence — Claude Desktop downstream does the
   facts/interpretation structuring.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from investor_research.clients.fmp import FmpClient, FmpSnapshot
from investor_research.clients.research_provider import (
    ResearchProvider,
    make_research_provider,
)
from investor_research.config import Settings, get_settings
from investor_research.models import ResearchEvidence, Source
from investor_research.prompts.research import (
    COMPETITORS_QUERY,
    EARNINGS_SUMMARY_QUERY,
    FOLLOW_UP_QUERY,
    RECENT_NEWS_QUERY,
    RESEARCH_COMPANY_QUERY,
    RISKS_QUERY,
)
from investor_research.source_quality import build_evidence_claims, enrich_source
from investor_research.tickers import InvalidTickerError, validate_ticker as _validate_ticker

# Re-exports preserved for backwards compatibility with the rest of the
# project, which imports these from this module.
__all__ = [
    "InvalidTickerError",
    "_validate_ticker",
    "research_company",
    "get_recent_news",
    "get_earnings_summary",
    "get_competitors",
    "get_risks",
    "research_follow_up",
]

logger = logging.getLogger(__name__)


def _maybe_load_vault_snapshot(settings: Settings, ticker: str):
    """Return the parsed ``FundamentalsSnapshot`` for ``ticker`` or ``None``.

    Returns ``None`` whenever the vault is unconfigured, the company folder
    is missing, parsed JSON is absent, or any error occurs while loading.
    Never raises.
    """
    if settings.vault_root is None:
        return None
    try:
        from investor_research.vault.snapshots import load_parsed_json

        return load_parsed_json(settings, ticker)
    except Exception as exc:  # noqa: BLE001 - vault is best-effort
        logger.warning(
            "research.vault_load_failed",
            extra={"ticker": ticker, "err": str(exc)[:200]},
        )
        return None


def _make_clients(settings: Settings | None = None) -> tuple[ResearchProvider, FmpClient, Settings]:
    settings = settings or get_settings()
    return make_research_provider(settings), FmpClient(settings), settings


def _envelope(
    *,
    ticker: str,
    settings: Settings,
    snapshot: FmpSnapshot,
    evidence: ResearchEvidence,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    vault_snapshot = _maybe_load_vault_snapshot(settings, ticker)
    sources = _combine_sources(snapshot, evidence.sources, vault_snapshot)
    fundamentals_block = _fundamentals_block(snapshot, vault_snapshot)
    final_notes = notes or _snapshot_notes(snapshot)
    if vault_snapshot is not None and vault_snapshot.has_data():
        final_notes = list(final_notes) + [
            f"vault: parsed fundamentals available "
            f"({len(vault_snapshot.periods)} periods, parsed_at="
            f"{vault_snapshot.parsed_at.isoformat()})"
        ]
    return {
        "ticker": ticker,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "provider": settings.research_provider,
        "query": evidence.query,
        "answer": evidence.answer,
        "results": [r.model_dump(mode="json") for r in evidence.results],
        "sources": [s.model_dump(mode="json") for s in sources],
        "evidence_claims": build_evidence_claims(evidence.results, sources),
        "fmp_snapshot": _snapshot_dump(snapshot),
        "fundamentals_snapshot": fundamentals_block,
        "notes": final_notes,
    }


def _fundamentals_block(
    fmp_snapshot: FmpSnapshot, vault_snapshot
) -> dict[str, Any] | None:
    """Unified fundamentals block.

    Prefers the user's parsed vault data when available (richer 20yr history
    + DCF), and surfaces the FMP snapshot as a cross-check. Returns ``None``
    when neither source has any data.
    """
    if vault_snapshot is not None and vault_snapshot.has_data():
        return {
            "source": "vault",
            "ticker": vault_snapshot.ticker,
            "company_name": vault_snapshot.company_name,
            "currency": vault_snapshot.currency,
            "units": vault_snapshot.units,
            "periods": vault_snapshot.periods,
            "series": vault_snapshot.series,
            "ratios": vault_snapshot.ratios,
            "valuation_history": vault_snapshot.valuation_history,
            "dcf": vault_snapshot.dcf,
            "metadata": vault_snapshot.metadata,
            "source_file": vault_snapshot.source_file,
            "source_sha256": vault_snapshot.source_sha256,
            "parsed_at": vault_snapshot.parsed_at.isoformat(),
            "warnings": vault_snapshot.warnings,
            "prompt_block": vault_snapshot.to_prompt_block(recent_years=5),
            "fmp_cross_check": _snapshot_dump(fmp_snapshot),
        }
    fmp_dump = _snapshot_dump(fmp_snapshot)
    if fmp_dump is None:
        return None
    return {"source": "fmp", **fmp_dump}


def _combine_sources(
    snapshot: FmpSnapshot,
    provider_sources: list[Source],
    vault_snapshot=None,
) -> list[Source]:
    out: list[Source] = []
    if vault_snapshot is not None and vault_snapshot.has_data():
        vault_source = vault_snapshot.to_source()
        if vault_source is not None:
            out.append(enrich_source(vault_source))
    fmp_source = snapshot.to_source()
    if fmp_source is not None:
        out.append(enrich_source(fmp_source))
    seen = {s.url for s in out}
    for src in provider_sources:
        if src.url not in seen:
            out.append(enrich_source(src))
            seen.add(src.url)
    return out


def _snapshot_dump(snapshot: FmpSnapshot) -> dict[str, Any] | None:
    if not snapshot.has_data():
        return None
    return {
        "profile": snapshot.profile,
        "quote": snapshot.quote,
        "income_statement": snapshot.income_statement,
        "balance_sheet": snapshot.balance_sheet,
        "key_metrics_ttm": snapshot.key_metrics_ttm,
        "peers": snapshot.peers,
        "prompt_block": snapshot.to_prompt_block(),
    }


def _snapshot_notes(snapshot: FmpSnapshot) -> list[str]:
    if not snapshot.has_data():
        return ["FMP data unavailable — qualitative fields from research provider only."]
    missing: list[str] = []
    for attr in ("profile", "quote", "income_statement", "balance_sheet", "key_metrics_ttm"):
        if not getattr(snapshot, attr):
            missing.append(attr)
    return [f"FMP partial: missing {', '.join(missing)}."] if missing else []


# ---- Phase 1 tools ------------------------------------------------------


def research_company(ticker: str) -> dict[str, Any]:
    """Return a broad research evidence bundle for ``ticker``."""
    ticker = _validate_ticker(ticker)
    provider, fmp, settings = _make_clients()
    snapshot = fmp.fetch_snapshot(ticker)

    query = RESEARCH_COMPANY_QUERY.format(ticker=ticker)
    evidence = provider.search(query, max_results=10)
    return _envelope(ticker=ticker, settings=settings, snapshot=snapshot, evidence=evidence)


def get_recent_news(ticker: str, days: int = 30) -> dict[str, Any]:
    """Return material company-specific events over the trailing ``days`` window."""
    ticker = _validate_ticker(ticker)
    if not isinstance(days, int) or days <= 0 or days > 365:
        raise ValueError("days must be a positive integer ≤ 365")

    provider, fmp, settings = _make_clients()
    snapshot = FmpSnapshot(ticker=ticker)  # news doesn't need FMP

    query = RECENT_NEWS_QUERY.format(ticker=ticker, days=days)
    evidence = provider.search(query, recency_days=days, max_results=10)
    return _envelope(
        ticker=ticker, settings=settings, snapshot=snapshot, evidence=evidence,
        notes=[f"news_window_days={days}"],
    )


def get_earnings_summary(ticker: str) -> dict[str, Any]:
    """Summarize the most recently reported quarter for ``ticker``."""
    ticker = _validate_ticker(ticker)
    provider, fmp, settings = _make_clients()
    snapshot = fmp.fetch_snapshot(ticker)

    query = EARNINGS_SUMMARY_QUERY.format(ticker=ticker)
    evidence = provider.search(query, max_results=8)
    return _envelope(ticker=ticker, settings=settings, snapshot=snapshot, evidence=evidence)


def get_competitors(ticker: str) -> dict[str, Any]:
    """Return primary public competitors with a side-by-side comparison."""
    ticker = _validate_ticker(ticker)
    provider, fmp, settings = _make_clients()
    snapshot = fmp.fetch_snapshot(ticker)
    peers_hint = (
        f"Candidate peers (from FMP, may be incomplete): {', '.join(snapshot.peers)}."
        if snapshot.peers
        else ""
    )
    query = COMPETITORS_QUERY.format(ticker=ticker, peers_hint=peers_hint).strip()
    evidence = provider.search(query, max_results=10)
    return _envelope(ticker=ticker, settings=settings, snapshot=snapshot, evidence=evidence)


def get_risks(ticker: str) -> dict[str, Any]:
    """Catalog material risks across six categories."""
    ticker = _validate_ticker(ticker)
    provider, fmp, settings = _make_clients()
    snapshot = FmpSnapshot(ticker=ticker)  # risk content from filings, not FMP

    query = RISKS_QUERY.format(ticker=ticker)
    evidence = provider.search(query, max_results=10)
    return _envelope(ticker=ticker, settings=settings, snapshot=snapshot, evidence=evidence)


def research_follow_up(
    ticker: str,
    question: str,
    recency_days: int | None = None,
) -> dict[str, Any]:
    """Answer an arbitrary follow-up with a fresh evidence search.

    This tool exists for conversational follow-ups that do not fit neatly
    into the fixed Phase 1/2 tools. It always calls the configured research
    provider, so Claude can ground the follow-up in fresh MCP evidence rather
    than only reusing prior chat context.
    """
    ticker = _validate_ticker(ticker)
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")
    if recency_days is not None:
        if not isinstance(recency_days, int) or recency_days <= 0 or recency_days > 3650:
            raise ValueError("recency_days must be a positive integer <= 3650")

    provider, fmp, settings = _make_clients()
    snapshot = fmp.fetch_snapshot(ticker)
    query = FOLLOW_UP_QUERY.format(ticker=ticker, question=question.strip())
    evidence = provider.search(query, recency_days=recency_days, max_results=10)
    notes = ["follow_up_question=" + question.strip()]
    if recency_days is not None:
        notes.append(f"recency_days={recency_days}")
    return _envelope(
        ticker=ticker,
        settings=settings,
        snapshot=snapshot,
        evidence=evidence,
        notes=notes,
    )


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import json
    import sys

    from investor_research.logging_setup import configure_logging

    settings = get_settings()
    configure_logging(settings.log_level, secrets=settings.secrets)
    arg_ticker = sys.argv[1] if len(sys.argv) > 1 else "MSFT"
    result = research_company(arg_ticker)
    print(json.dumps(result, indent=2, default=str))
