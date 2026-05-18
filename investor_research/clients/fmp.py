"""Financial Modeling Prep client.

Provides authoritative structured statement data for the fields where we
prefer not to rely on Perplexity's qualitative extraction (revenue, EPS,
margins, basic valuation ratios, peer tickers).

When ``FMP_API_KEY`` is unset, every method returns ``None`` and callers
fall back to Perplexity for those fields.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import requests
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from investor_research.config import Settings
from investor_research.models import Source

logger = logging.getLogger(__name__)

FMP_BASE_URL = "https://financialmodelingprep.com/api/v3"


class FmpError(RuntimeError):
    """Raised when an FMP call fails after retries."""


@dataclass
class FmpSnapshot:
    """The bundle of FMP data injected into Perplexity prompts."""

    ticker: str
    profile: dict[str, Any] | None = None
    quote: dict[str, Any] | None = None
    income_statement: list[dict[str, Any]] | None = None
    balance_sheet: list[dict[str, Any]] | None = None
    key_metrics_ttm: dict[str, Any] | None = None
    peers: list[str] | None = None

    def to_prompt_block(self) -> str:
        """Render a compact, deterministic summary for use inside a prompt."""
        if not self.has_data():
            return "(no structured financial data available)"
        lines: list[str] = []
        if self.profile:
            p = self.profile
            lines.append(
                f"Profile: {p.get('companyName')} | sector={p.get('sector')} | "
                f"industry={p.get('industry')} | exchange={p.get('exchangeShortName')}"
            )
            if p.get("description"):
                desc = str(p["description"])[:600]
                lines.append(f"Description: {desc}")
        if self.quote:
            q = self.quote
            lines.append(
                f"Quote: price={q.get('price')} | marketCap={q.get('marketCap')} | "
                f"pe={q.get('pe')} | eps={q.get('eps')}"
            )
        if self.income_statement:
            for row in self.income_statement[:4]:
                lines.append(
                    f"Income {row.get('date')} ({row.get('period')}): "
                    f"revenue={row.get('revenue')} | "
                    f"grossProfit={row.get('grossProfit')} | "
                    f"operatingIncome={row.get('operatingIncome')} | "
                    f"netIncome={row.get('netIncome')} | "
                    f"eps={row.get('eps')}"
                )
        if self.balance_sheet:
            bs = self.balance_sheet[0]
            lines.append(
                f"Balance {bs.get('date')}: cash={bs.get('cashAndCashEquivalents')} | "
                f"totalDebt={bs.get('totalDebt')} | totalEquity={bs.get('totalEquity')}"
            )
        if self.key_metrics_ttm:
            m = self.key_metrics_ttm
            lines.append(
                f"TTM metrics: evToEbitda={m.get('enterpriseValueOverEBITDATTM')} | "
                f"freeCashFlowYield={m.get('freeCashFlowYieldTTM')} | "
                f"roeTTM={m.get('roeTTM')} | netDebtToEbitda={m.get('netDebtToEBITDATTM')}"
            )
        if self.peers:
            lines.append(f"Peers (FMP): {', '.join(self.peers)}")
        return "\n".join(lines)

    def has_data(self) -> bool:
        return any(
            value
            for value in (
                self.profile,
                self.quote,
                self.income_statement,
                self.balance_sheet,
                self.key_metrics_ttm,
                self.peers,
            )
        )

    def to_source(self) -> Source | None:
        if not self.has_data():
            return None
        return Source(
            url=f"https://financialmodelingprep.com/financial-statements/{self.ticker}",
            title=f"FMP structured financials — {self.ticker}",
            publisher="Financial Modeling Prep",
        )


class FmpClient:
    def __init__(
        self,
        settings: Settings,
        session: requests.Session | None = None,
    ) -> None:
        self._settings = settings
        self._session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self._settings.fmp_api_key)

    def fetch_snapshot(self, ticker: str) -> FmpSnapshot:
        """Fetch all Phase 1 endpoints for ``ticker``.

        Returns an empty snapshot if FMP is disabled (no key). Individual
        endpoint failures degrade gracefully — we keep whatever fields we
        managed to retrieve.
        """
        snapshot = FmpSnapshot(ticker=ticker)
        if not self.enabled:
            return snapshot

        snapshot.profile = self._safe_get(f"/profile/{ticker}", first=True)
        snapshot.quote = self._safe_get(f"/quote/{ticker}", first=True)
        snapshot.income_statement = self._safe_get(
            f"/income-statement/{ticker}", params={"limit": 4}
        )
        snapshot.balance_sheet = self._safe_get(
            f"/balance-sheet-statement/{ticker}", params={"limit": 1}
        )
        snapshot.key_metrics_ttm = self._safe_get(
            f"/key-metrics-ttm/{ticker}", first=True
        )
        peers_payload = self._safe_get(
            "/stock_peers", params={"symbol": ticker}, first=True
        )
        if peers_payload and isinstance(peers_payload, dict):
            peers = peers_payload.get("peersList") or []
            snapshot.peers = [p for p in peers if isinstance(p, str)]
        return snapshot

    # ---- internals -----------------------------------------------------

    def _safe_get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        first: bool = False,
    ) -> Any:
        try:
            return self._get(path, params=params, first=first)
        except (FmpError, RetryError) as exc:
            logger.warning(
                "fmp.endpoint_failed",
                extra={"path": path, "err": str(exc)[:200]},
            )
            return None

    def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        first: bool = False,
    ) -> Any:
        params = dict(params or {})
        params["apikey"] = self._settings.fmp_api_key
        url = f"{FMP_BASE_URL}{path}"
        try:
            data = _retry_get(self._session, url, params, self._settings.request_timeout_seconds)
        except RetryError as exc:
            cause = exc.last_attempt.exception() if exc.last_attempt else exc
            raise FmpError(f"GET {path} failed: {cause}") from cause
        if first and isinstance(data, list):
            return data[0] if data else None
        return data


_RETRYABLE_REQUESTS = (requests.Timeout, requests.ConnectionError)


@retry(
    reraise=False,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(_RETRYABLE_REQUESTS),
)
def _retry_get(
    session: requests.Session,
    url: str,
    params: dict[str, Any],
    timeout: float,
) -> Any:
    response = session.get(url, params=params, timeout=timeout)
    if response.status_code == 429 or 500 <= response.status_code < 600:
        raise requests.ConnectionError(
            f"FMP transient {response.status_code}: {response.text[:120]}"
        )
    if response.status_code >= 400:
        raise FmpError(f"FMP {response.status_code}: {response.text[:200]}")
    return response.json()
