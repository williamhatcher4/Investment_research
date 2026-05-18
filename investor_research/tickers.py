"""Ticker symbol validation.

Shared between the research tools and the vault layer so neither has to
depend on the other for input normalization.
"""

from __future__ import annotations

import re

_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class InvalidTickerError(ValueError):
    """Raised when an input ticker fails validation."""


def validate_ticker(ticker: str) -> str:
    """Normalize and validate a ticker symbol.

    Returns the uppercased, stripped ticker. Raises ``InvalidTickerError``
    when the input is not a string or does not match the canonical regex.
    """
    if not isinstance(ticker, str):
        raise InvalidTickerError("ticker must be a string")
    normalized = ticker.strip().upper()
    if not _TICKER_RE.match(normalized):
        raise InvalidTickerError(f"invalid ticker symbol: {ticker!r}")
    return normalized
