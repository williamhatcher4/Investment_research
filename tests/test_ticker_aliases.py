"""Tests for the Bloomberg-ticker alias map."""

from __future__ import annotations

from pathlib import Path

import yaml

from investor_research.vault.paths import initialize_vault
from investor_research.vault.ticker_aliases import (
    ALIASES_FILE,
    _aliases_path,
    auto_seed_aliases,
    load_aliases,
    resolve,
    save_aliases,
    unresolved,
)


def test_auto_seed_handles_all_countries(settings_with_vault) -> None:
    initialize_vault(settings_with_vault)
    aliases, n_added = auto_seed_aliases(
        settings_with_vault,
        ["aapl us equity", "ASML NA Equity", "BNZL LN Equity"],
    )
    assert n_added == 3
    assert aliases["aapl us equity"]["ticker"] == "AAPL"
    assert aliases["aapl us equity"]["country"] == "US"
    assert aliases["aapl us equity"]["source"] == "auto"
    assert aliases["asml na equity"]["ticker"] == "ASML"
    assert aliases["asml na equity"]["country"] == "NL"
    assert aliases["bnzl ln equity"]["ticker"] == "BNZL"
    assert aliases["bnzl ln equity"]["country"] == "GB"


def test_auto_seed_skips_unparseable_tickers(settings_with_vault) -> None:
    """Bloomberg strings that don't fit ``<stem> <country> equity`` are skipped."""
    initialize_vault(settings_with_vault)
    aliases, n_added = auto_seed_aliases(
        settings_with_vault,
        ["not a ticker", "garbage", "msft us equity"],
    )
    assert n_added == 1
    assert "msft us equity" in aliases


def test_auto_seed_converts_slash_to_dot(settings_with_vault) -> None:
    initialize_vault(settings_with_vault)
    aliases, n_added = auto_seed_aliases(
        settings_with_vault, ["BF/B US Equity"]
    )
    assert n_added == 1
    assert aliases["bf/b us equity"]["ticker"] == "BF.B"


def test_auto_seed_idempotent(settings_with_vault) -> None:
    initialize_vault(settings_with_vault)
    auto_seed_aliases(settings_with_vault, ["aapl us equity"])
    aliases, n_added = auto_seed_aliases(
        settings_with_vault, ["aapl us equity", "msft us equity"]
    )
    assert n_added == 1   # only MSFT added the second time
    assert aliases["aapl us equity"]["ticker"] == "AAPL"
    assert aliases["msft us equity"]["ticker"] == "MSFT"


def test_resolve_returns_none_for_unseeded(settings_with_vault) -> None:
    initialize_vault(settings_with_vault)
    aliases, _ = auto_seed_aliases(settings_with_vault, ["aapl us equity"])
    assert resolve(aliases, "aapl us equity") == "AAPL"
    # Not in the seeded set -> still unresolved until seeded or manually added.
    assert resolve(aliases, "ASML NA Equity") is None


def test_resolve_is_whitespace_tolerant(settings_with_vault) -> None:
    initialize_vault(settings_with_vault)
    aliases, _ = auto_seed_aliases(settings_with_vault, ["AAPL US Equity"])
    assert resolve(aliases, "  aapl  us  equity ") == "AAPL"


def test_save_and_load_round_trip(settings_with_vault) -> None:
    initialize_vault(settings_with_vault)
    save_aliases(
        settings_with_vault,
        {"asml na equity": {"ticker": "ASML", "country": "NL", "source": "manual"}},
    )
    aliases = load_aliases(settings_with_vault)
    assert aliases["asml na equity"]["ticker"] == "ASML"

    # Confirm on-disk YAML is human-readable.
    path = _aliases_path(settings_with_vault)
    assert path.name == ALIASES_FILE
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["asml na equity"]["ticker"] == "ASML"


def test_unresolved_filters_to_only_unmapped(settings_with_vault) -> None:
    initialize_vault(settings_with_vault)
    aliases, _ = auto_seed_aliases(settings_with_vault, ["aapl us equity"])
    # Only AAPL has been seeded; the others are still unresolved until seeded.
    leftover = unresolved(aliases, ["aapl us equity", "ASML NA Equity", "garbage"])
    assert leftover == ["ASML NA Equity", "garbage"]


def test_load_aliases_returns_empty_when_missing(settings_with_vault) -> None:
    initialize_vault(settings_with_vault)
    assert load_aliases(settings_with_vault) == {}
