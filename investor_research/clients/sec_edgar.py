"""SEC EDGAR client — primary source for 10-K, 10-Q, 8-K, and proxy filings.

EDGAR is free and requires only a contact ``User-Agent`` header. Endpoints
used:

- ``https://www.sec.gov/files/company_tickers.json`` — full ticker → CIK
  map. ~10MB; cached in-process after the first call.
- ``https://data.sec.gov/submissions/CIK{cik}.json`` — recent filings for
  one company.
"""

from __future__ import annotations

import logging
import threading
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

logger = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data"

FORM_ALIASES: dict[str, tuple[str, ...]] = {
    "10-K": ("10-K", "10-K/A"),
    "10-Q": ("10-Q", "10-Q/A"),
    "8-K": ("8-K", "8-K/A"),
    "proxy": ("DEF 14A", "DEFA14A", "PRE 14A"),
}


class SecEdgarError(RuntimeError):
    """Raised when an EDGAR call fails or a ticker can't be resolved."""


@dataclass
class FilingEntry:
    form: str
    filing_date: str
    report_date: str | None
    accession_number: str
    primary_document: str | None
    filing_url: str
    index_url: str


class SecEdgarClient:
    _ticker_map_lock = threading.Lock()
    _ticker_map: dict[str, str] | None = None  # ticker -> 10-digit CIK

    def __init__(
        self,
        settings: Settings,
        session: requests.Session | None = None,
    ) -> None:
        self._settings = settings
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "User-Agent": settings.sec_edgar_user_agent,
                "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
            }
        )

    # ---- public API ----------------------------------------------------

    def ticker_to_cik(self, ticker: str) -> str:
        """Return a 10-digit, zero-padded CIK for ``ticker``."""
        ticker_upper = ticker.upper()
        mapping = self._ensure_ticker_map()
        cik = mapping.get(ticker_upper)
        if not cik:
            raise SecEdgarError(f"No SEC CIK found for ticker {ticker!r}")
        return cik

    def get_filings(
        self,
        ticker: str,
        *,
        forms: tuple[str, ...] = ("10-K", "10-Q", "8-K", "proxy"),
        limit_per_form: int = 4,
    ) -> dict[str, Any]:
        """Return recent filings grouped by form for ``ticker``."""
        cik = self.ticker_to_cik(ticker)
        submissions = self._fetch_submissions(cik)
        recent = submissions.get("filings", {}).get("recent", {}) or {}
        forms_arr = recent.get("form") or []
        accs_arr = recent.get("accessionNumber") or []
        dates_arr = recent.get("filingDate") or []
        reports_arr = recent.get("reportDate") or []
        primary_arr = recent.get("primaryDocument") or []

        cik_int_str = str(int(cik))  # archive URLs use the un-padded CIK

        grouped: dict[str, list[FilingEntry]] = {key: [] for key in forms}
        for form, acc, fdate, rdate, primary in zip(
            forms_arr, accs_arr, dates_arr, reports_arr, primary_arr
        ):
            for key in forms:
                if form in FORM_ALIASES.get(key, (key,)):
                    if len(grouped[key]) >= limit_per_form:
                        break
                    acc_clean = acc.replace("-", "")
                    folder = f"{ARCHIVE_BASE}/{cik_int_str}/{acc_clean}"
                    grouped[key].append(
                        FilingEntry(
                            form=form,
                            filing_date=fdate,
                            report_date=rdate or None,
                            accession_number=acc,
                            primary_document=primary or None,
                            filing_url=f"{folder}/{primary}" if primary else folder,
                            index_url=f"{folder}/{acc}-index.htm",
                        )
                    )
                    break

        return {
            "ticker": ticker.upper(),
            "cik": cik,
            "company_name": submissions.get("name"),
            "filings": {key: [e.__dict__ for e in entries] for key, entries in grouped.items()},
        }

    def latest_filing(self, ticker: str, form: str) -> FilingEntry | None:
        """Return the most recent filing of ``form`` for ``ticker``, if any."""
        grouped = self.get_filings(ticker, forms=(form,), limit_per_form=1)
        entries = grouped["filings"].get(form, [])
        if not entries:
            return None
        entry = entries[0]
        return FilingEntry(**entry)

    def fetch_filing_text(self, filing_url: str) -> str:
        """Fetch a primary filing document as text/HTML from EDGAR."""
        try:
            return _retry_get_text(
                self._session, filing_url, self._settings.request_timeout_seconds
            )
        except RetryError as exc:
            cause = exc.last_attempt.exception() if exc.last_attempt else exc
            raise SecEdgarError(f"GET {filing_url} failed: {cause}") from cause

    # ---- internals -----------------------------------------------------

    def _ensure_ticker_map(self) -> dict[str, str]:
        with self._ticker_map_lock:
            if SecEdgarClient._ticker_map is None:
                SecEdgarClient._ticker_map = self._fetch_ticker_map()
            return SecEdgarClient._ticker_map

    def _fetch_ticker_map(self) -> dict[str, str]:
        raw = self._safe_json(TICKERS_URL)
        if not isinstance(raw, dict):
            raise SecEdgarError("Unexpected company_tickers.json shape")
        mapping: dict[str, str] = {}
        for entry in raw.values():
            if not isinstance(entry, dict):
                continue
            ticker = entry.get("ticker")
            cik = entry.get("cik_str")
            if ticker and cik is not None:
                mapping[str(ticker).upper()] = f"{int(cik):010d}"
        return mapping

    def _fetch_submissions(self, cik: str) -> dict[str, Any]:
        url = SUBMISSIONS_URL.format(cik=cik)
        data = self._safe_json(url)
        if not isinstance(data, dict):
            raise SecEdgarError(f"Unexpected submissions shape for CIK {cik}")
        return data

    def _safe_json(self, url: str) -> Any:
        try:
            return _retry_get(self._session, url, self._settings.request_timeout_seconds)
        except RetryError as exc:
            cause = exc.last_attempt.exception() if exc.last_attempt else exc
            raise SecEdgarError(f"GET {url} failed: {cause}") from cause


_RETRYABLE = (requests.Timeout, requests.ConnectionError)


@retry(
    reraise=False,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(_RETRYABLE),
)
def _retry_get(session: requests.Session, url: str, timeout: float) -> Any:
    response = session.get(url, timeout=timeout)
    if response.status_code == 429 or 500 <= response.status_code < 600:
        raise requests.ConnectionError(
            f"SEC EDGAR transient {response.status_code}: {response.text[:120]}"
        )
    if response.status_code >= 400:
        raise SecEdgarError(f"EDGAR {response.status_code}: {response.text[:200]}")
    return response.json()


@retry(
    reraise=False,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(_RETRYABLE),
)
def _retry_get_text(session: requests.Session, url: str, timeout: float) -> str:
    response = session.get(url, timeout=timeout)
    if response.status_code == 429 or 500 <= response.status_code < 600:
        raise requests.ConnectionError(
            f"SEC EDGAR transient {response.status_code}: {response.text[:120]}"
        )
    if response.status_code >= 400:
        raise SecEdgarError(f"EDGAR {response.status_code}: {response.text[:200]}")
    return response.text


def reset_ticker_map_cache() -> None:
    """Test helper — clears the module-level ticker→CIK cache."""
    with SecEdgarClient._ticker_map_lock:
        SecEdgarClient._ticker_map = None
