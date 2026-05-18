"""Investment memo composition tool.

Runs the five Phase 1 research tools in parallel and bundles their evidence
under named topics, along with a deduped source list and a skeleton memo
outline for Claude Desktop to fill in.

The 11-section memo prose is intentionally NOT generated here — in
raw-evidence mode, Claude Desktop downstream writes the memo from this
bundle. This keeps the MCP-side cost to one provider call per topic.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable

from investor_research.config import get_settings
from investor_research.models import Source
from investor_research.tools.research import (
    _validate_ticker,
    get_competitors,
    get_earnings_summary,
    get_recent_news,
    get_risks,
    research_company,
)

logger = logging.getLogger(__name__)


MEMO_OUTLINE: tuple[str, ...] = (
    "Executive Summary",
    "Business Overview",
    "Financial Profile",
    "Recent Earnings",
    "Competitive Position",
    "Bull Case",
    "Bear Case",
    "Key Risks",
    "Valuation Discussion",
    "Open Questions",
    "Watchlist Verdict",
)


def build_investment_memo(ticker: str) -> dict[str, Any]:
    """Bundle the five upstream tools' evidence for an investment memo."""
    ticker = _validate_ticker(ticker)
    upstream = _run_upstream_tools(ticker)
    sources, _ = _dedupe_sources(upstream)
    vault_available = _vault_data_in_upstream(upstream)

    return {
        "ticker": ticker,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "provider": get_settings().research_provider,
        "memo_outline": list(MEMO_OUTLINE),
        "instructions": _instructions_for_claude(ticker, vault_available=vault_available),
        "evidence_by_topic": upstream,
        "sources": [s.model_dump(mode="json") for s in sources],
        "vault_data_used": vault_available,
    }


# ---- internals ---------------------------------------------------------


def _run_upstream_tools(ticker: str) -> dict[str, dict[str, Any]]:
    jobs: dict[str, Callable[[], dict[str, Any]]] = {
        "research_company": lambda: research_company(ticker),
        "recent_news": lambda: get_recent_news(ticker, days=30),
        "earnings_summary": lambda: get_earnings_summary(ticker),
        "competitors": lambda: get_competitors(ticker),
        "risks": lambda: get_risks(ticker),
    }
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        future_to_name = {pool.submit(fn): name for name, fn in jobs.items()}
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            try:
                results[name] = future.result()
            except Exception as exc:  # noqa: BLE001 - surface partial memos
                logger.warning(
                    "memo.upstream_failed",
                    extra={"tool": name, "err": str(exc)[:200]},
                )
                results[name] = {"error": str(exc)}
    return results


def _vault_data_in_upstream(upstream: dict[str, dict[str, Any]]) -> bool:
    """Return True if any upstream tool produced a vault-sourced fundamentals block."""
    for tool_result in upstream.values():
        if not isinstance(tool_result, dict):
            continue
        fundamentals = tool_result.get("fundamentals_snapshot")
        if isinstance(fundamentals, dict) and fundamentals.get("source") == "vault":
            return True
    return False


def _dedupe_sources(
    upstream: dict[str, dict[str, Any]],
) -> tuple[list[Source], dict[str, int]]:
    sources: list[Source] = []
    by_url: dict[str, int] = {}
    for tool_result in upstream.values():
        if not isinstance(tool_result, dict):
            continue
        for raw in tool_result.get("sources", []) or []:
            try:
                src = Source.model_validate(raw)
            except Exception:  # noqa: BLE001 - tolerate bad rows
                continue
            if src.url not in by_url:
                by_url[src.url] = len(sources)
                sources.append(src)
    return sources, by_url


def _instructions_for_claude(ticker: str, *, vault_available: bool = False) -> str:
    base = (
        f"Compose an institutional-quality investment memo on {ticker} using the "
        "evidence in `evidence_by_topic`. Produce all 11 sections from "
        "`memo_outline` in order. Distinguish sourced facts (cite using the URLs "
        "in `sources`) from your interpretation — Bull Case, Bear Case, and "
        "Watchlist Verdict are interpretive but each claim must rest on at least "
        "one cited fact from the evidence. Do not introduce facts or URLs that "
        "are not present in the bundle. End with a Watchlist Verdict of one of: "
        "AVOID, WATCH, CONSIDER, ATTRACTIVE — with a one-paragraph justification."
    )
    if not vault_available:
        return base
    return (
        base
        + " The user's own parsed Bloomberg/FactSet fundamentals live at "
        "`evidence_by_topic.research_company.fundamentals_snapshot` (source = 'vault'). "
        "Prefer that block for the 20-year history of revenue, margins, FCF, ROIC, and "
        "capital allocation in the Financial Profile section. Anchor the Valuation "
        "Discussion on `fundamentals_snapshot.dcf` — specifically `target_price`, "
        "`intrinsic_value_per_share`, `margin_of_safety`, `discount_rate`, and "
        "`terminal_growth_rate` — and reference the user's own DCF outputs rather than "
        "re-deriving multiples from web sources. Cite the vault source for any "
        "vault-sourced numbers."
    )
