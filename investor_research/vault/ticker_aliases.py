"""Bloomberg-ticker -> normalized-ticker alias mapping for the watchlist importer.

Bloomberg tickers in the watchlist scorecard look like
``"aapl us equity"``, ``"ASML NA Equity"``, ``"BF/B US Equity"``. The
vault stores companies under canonical short tickers (``"AAPL"``,
``"ASML"``, ``"BF.B"``). This module owns the mapping.

Resolution strategy:

- ``auto_seed_aliases()`` walks the row set and, for every Bloomberg
  ticker of the form ``"<stem> <country> equity"``, derives the vault
  ticker by upper-casing the stem and converting ``"/"`` -> ``"."`` (the
  BBG class convention, e.g. ``BF/B`` -> ``BF.B``). The country suffix
  becomes ISO-ish metadata for diagnostics. Country is recorded but does
  not affect the ticker — non-US names (``NA`` / ``LN`` / ``SS`` etc.)
  resolve the same way as US ones.

- Anything that doesn't fit the ``"<stem> <country> equity"`` shape, or
  whose stem fails the vault's ticker regex, is left unmapped and surfaced
  in the import tool's ``unresolved_tickers`` output.

- The YAML is authoritative. Any auto-seeded entry can be overridden by
  hand (e.g. to switch ``"asml na equity"`` from ``ASML`` to ``ASML.AS``).
  The auto-seed never overwrites an existing entry.

The on-disk YAML lives at ``{VAULT_ROOT}/Watchlists/ticker_aliases.yml``::

    aapl us equity:
      ticker: AAPL
      country: US
      source: auto
    asml na equity:
      ticker: ASML
      country: NL
      source: auto
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Iterable

import yaml

from investor_research.config import Settings
from investor_research.tickers import InvalidTickerError, validate_ticker
from investor_research.vault.paths import get_vault_root

logger = logging.getLogger(__name__)


ALIASES_FILE = "ticker_aliases.yml"

_BB_TICKER_RE = re.compile(r"^(?P<stem>\S+)\s+(?P<country>[A-Za-z]{2})\s+equity\s*$", re.IGNORECASE)


_COUNTRY_BY_SUFFIX = {
    "US": "US",
    "CN": "CA",   # CN suffix = Canada (TSX) in BBG, not China
    "NA": "NL",
    "LN": "GB",
    "SS": "SE",
    "DC": "DK",
    "SW": "CH",
    "FP": "FR",
    "GR": "DE",
    "GY": "DE",
    "AV": "AT",
    "IM": "IT",
    "BB": "BE",
    "SM": "ES",
    "PL": "PT",
    "JP": "JP",
    "HK": "HK",
    "AU": "AU",
}


def _aliases_path(settings: Settings) -> Path:
    return get_vault_root(settings) / "Watchlists" / ALIASES_FILE


def _norm_key(bb_ticker: str) -> str:
    """Lowercase + collapse internal whitespace for stable YAML lookup."""
    return re.sub(r"\s+", " ", bb_ticker.strip().lower())


def load_aliases(settings: Settings) -> dict[str, dict[str, Any]]:
    """Return the alias map keyed by the normalized Bloomberg ticker."""
    path = _aliases_path(settings)
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        logger.warning("vault.aliases.yaml_parse_failed", extra={"path": str(path)})
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, value in data.items():
        if isinstance(key, str) and isinstance(value, dict):
            out[_norm_key(key)] = dict(value)
    return out


def save_aliases(settings: Settings, mapping: dict[str, dict[str, Any]]) -> None:
    """Persist ``mapping`` to the YAML file, creating parents as needed."""
    path = _aliases_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Sort keys for stable diffs.
    ordered = {k: mapping[k] for k in sorted(mapping)}
    path.write_text(yaml.safe_dump(ordered, sort_keys=False), encoding="utf-8")
    logger.info("vault.aliases.saved", extra={"path": str(path), "count": len(ordered)})


def _parse_bb(bb_ticker: str) -> tuple[str, str] | None:
    """Return ``(stem, country_suffix_upper)`` or ``None`` if it doesn't match."""
    if not isinstance(bb_ticker, str):
        return None
    match = _BB_TICKER_RE.match(bb_ticker.strip())
    if not match:
        return None
    return match.group("stem"), match.group("country").upper()


def _normalize_stem(stem: str) -> str | None:
    """Convert a BBG ticker stem (``"BF/B"``, ``"aapl"``, ``"BNZL"``) to a vault ticker.

    Returns ``None`` if the resulting candidate cannot be validated.
    """
    candidate = stem.strip().upper().replace("/", ".")
    try:
        return validate_ticker(candidate)
    except InvalidTickerError:
        return None


def resolve(
    aliases: dict[str, dict[str, Any]],
    bb_ticker: str,
) -> str | None:
    """Return the normalized vault ticker for ``bb_ticker``, or ``None``.

    Reads from the in-memory ``aliases`` dict only — auto-seeding is
    handled by ``auto_seed_us_aliases`` so the YAML is updated once per
    import rather than once per row.
    """
    entry = aliases.get(_norm_key(bb_ticker))
    if not entry:
        return None
    ticker = entry.get("ticker")
    return ticker if isinstance(ticker, str) and ticker else None


def auto_seed_aliases(
    settings: Settings,
    bb_tickers: Iterable[str],
) -> tuple[dict[str, dict[str, Any]], int]:
    """Add ``"<stem> <country> equity"`` -> ``stem`` entries for every parseable row.

    Country suffix is recorded as metadata but does not affect the
    derived ticker — non-US Bloomberg tickers resolve via the same
    stem-upper + ``/`` -> ``.`` normalization as US ones.

    Returns ``(aliases_after, n_added)``. Idempotent: existing entries
    (regardless of ``source``) are not overwritten, so user-edited
    aliases survive re-imports. Writes back to disk only if at least one
    entry was added.
    """
    aliases = load_aliases(settings)
    added = 0
    for raw in bb_tickers:
        parsed = _parse_bb(raw)
        if parsed is None:
            continue
        stem, country = parsed
        key = _norm_key(raw)
        if key in aliases:
            continue
        ticker = _normalize_stem(stem)
        if ticker is None:
            continue
        aliases[key] = {
            "ticker": ticker,
            "country": _COUNTRY_BY_SUFFIX.get(country, country),
            "source": "auto",
        }
        added += 1
    if added:
        save_aliases(settings, aliases)
    return aliases, added


# Back-compat alias for callers that imported the old US-only name.
auto_seed_us_aliases = auto_seed_aliases


def unresolved(
    aliases: dict[str, dict[str, Any]],
    bb_tickers: Iterable[str],
) -> list[str]:
    """Return the input Bloomberg tickers that do not resolve via ``aliases``."""
    out: list[str] = []
    for raw in bb_tickers:
        if resolve(aliases, raw) is None:
            out.append(raw)
    return out
