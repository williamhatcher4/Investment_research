"""Shared helpers used by both xlsx parsers (BBV2 history + watchlist scorecard)."""

from __future__ import annotations

from typing import Any


def coerce_number(value: Any) -> float | None:
    """Lenient numeric coercion.

    Returns ``None`` for empty cells, booleans, NaN, Excel error strings
    (``#NAME?``, ``#REF!``, ``#VALUE!``, ``#DIV/0!``...), and anything
    that doesn't parse as a float. Strips thousands separators.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if value != value:  # NaN guard
            return None
        return float(value)
    if isinstance(value, str):
        stripped = value.strip().replace(",", "")
        if not stripped or stripped.startswith("#"):
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None
