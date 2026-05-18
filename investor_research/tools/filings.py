"""Phase 2 filing tools: get_sec_filings and extract_10k_risks."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from investor_research.clients.research_provider import make_research_provider
from investor_research.clients.sec_edgar import SecEdgarClient, SecEdgarError
from investor_research.config import get_settings
from investor_research.prompts.research import EXTRACT_10K_RISKS_QUERY
from investor_research.tools.research import _validate_ticker

logger = logging.getLogger(__name__)


def get_sec_filings(
    ticker: str,
    forms: list[str] | None = None,
    limit_per_form: int = 4,
) -> dict[str, Any]:
    """Return recent SEC filings (10-K, 10-Q, 8-K, proxy) for ``ticker``.

    Pulled directly from EDGAR — these are primary sources.
    """
    ticker = _validate_ticker(ticker)
    if not isinstance(limit_per_form, int) or limit_per_form <= 0 or limit_per_form > 50:
        raise ValueError("limit_per_form must be a positive integer ≤ 50")

    forms_tuple = tuple(forms) if forms else ("10-K", "10-Q", "8-K", "proxy")
    edgar = SecEdgarClient(get_settings())

    try:
        result = edgar.get_filings(ticker, forms=forms_tuple, limit_per_form=limit_per_form)
    except SecEdgarError as exc:
        return {
            "ticker": ticker,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "error": str(exc),
            "filings": {},
        }

    return {
        **result,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "source": "SEC EDGAR",
    }


def extract_10k_risks(ticker: str) -> dict[str, Any]:
    """Surface evidence about Item 1A Risk Factors in the most recent 10-K.

    Returns the primary 10-K filing URL (from EDGAR) plus a Tavily search
    constrained to sec.gov so Claude downstream can summarize risk factors
    grounded in the actual filing.
    """
    ticker = _validate_ticker(ticker)
    settings = get_settings()
    edgar = SecEdgarClient(settings)

    try:
        latest = edgar.latest_filing(ticker, "10-K")
    except SecEdgarError as exc:
        latest = None
        edgar_error = str(exc)
    else:
        edgar_error = None

    provider = make_research_provider(settings)
    query = EXTRACT_10K_RISKS_QUERY.format(ticker=ticker)
    evidence = provider.search(query, max_results=10)

    return {
        "ticker": ticker,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "provider": settings.research_provider,
        "primary_filing": latest.__dict__ if latest else None,
        "edgar_error": edgar_error,
        "query": evidence.query,
        "answer": evidence.answer,
        "results": [r.model_dump(mode="json") for r in evidence.results],
        "sources": [s.model_dump(mode="json") for s in evidence.sources],
        "notes": (
            ["No 10-K found on EDGAR for this ticker."]
            if latest is None and edgar_error is None
            else []
        ),
    }
