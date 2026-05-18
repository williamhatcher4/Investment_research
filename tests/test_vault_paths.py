"""Tests for vault filesystem layout and folder lifecycle."""

from __future__ import annotations

from pathlib import Path

import pytest

from investor_research.tickers import InvalidTickerError
from investor_research.vault.paths import (
    COMPANY_SUBDIRS,
    TOP_LEVEL_DIRS,
    VaultNotConfiguredError,
    create_company_folder,
    get_company_path,
    get_latest_xlsx_export,
    get_vault_root,
    initialize_vault,
    list_companies,
    vault_enabled,
)


def test_vault_disabled_when_root_unset(settings_tavily) -> None:
    assert vault_enabled(settings_tavily) is False
    with pytest.raises(VaultNotConfiguredError):
        get_vault_root(settings_tavily)


def test_initialize_vault_creates_top_level_dirs(settings_with_vault) -> None:
    root = initialize_vault(settings_with_vault)
    assert root.is_dir()
    for sub in TOP_LEVEL_DIRS:
        assert (root / sub).is_dir(), f"missing top-level dir: {sub}"


def test_initialize_vault_is_idempotent(settings_with_vault) -> None:
    initialize_vault(settings_with_vault)
    # Second call must not raise and must not clobber anything.
    initialize_vault(settings_with_vault)
    assert settings_with_vault.vault_root is not None
    assert settings_with_vault.vault_root.is_dir()


def test_get_company_path_validates_ticker(settings_with_vault) -> None:
    initialize_vault(settings_with_vault)
    path = get_company_path(settings_with_vault, "nke")
    assert path.name == "NKE"
    assert path.parent.name == "Companies"


def test_get_company_path_rejects_bad_ticker(settings_with_vault) -> None:
    initialize_vault(settings_with_vault)
    with pytest.raises(InvalidTickerError):
        get_company_path(settings_with_vault, "not a ticker")


def test_create_company_folder_builds_full_tree(settings_with_vault) -> None:
    company_dir = create_company_folder(
        settings_with_vault, "NKE", company_name="Nike Inc", sector="Consumer Discretionary"
    )
    assert company_dir.is_dir()
    for sub in COMPANY_SUBDIRS:
        assert (company_dir / sub).is_dir(), f"missing subdir: {sub}"
    note_path = company_dir / "NKE.md"
    assert note_path.is_file()
    text = note_path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "ticker: NKE" in text
    assert "company_name: Nike Inc" in text
    assert "sector: Consumer Discretionary" in text


def test_create_company_folder_preserves_existing_note(settings_with_vault) -> None:
    create_company_folder(settings_with_vault, "NKE", company_name="Nike Inc")
    note_path = get_company_path(settings_with_vault, "NKE") / "NKE.md"
    original = note_path.read_text(encoding="utf-8")
    # Re-call must not overwrite the existing note.
    create_company_folder(settings_with_vault, "NKE", company_name="DIFFERENT")
    assert note_path.read_text(encoding="utf-8") == original


def test_get_latest_xlsx_export_returns_none_when_empty(settings_with_vault) -> None:
    create_company_folder(settings_with_vault, "NKE")
    assert get_latest_xlsx_export(settings_with_vault, "NKE") is None


def test_get_latest_xlsx_export_picks_most_recent(settings_with_vault) -> None:
    create_company_folder(settings_with_vault, "NKE")
    exports = get_company_path(settings_with_vault, "NKE") / "bloomberg_exports"
    old = exports / "old.xlsx"
    new = exports / "new.xlsx"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    # Stagger mtimes deterministically.
    import os
    import time

    older_time = time.time() - 1000
    os.utime(old, (older_time, older_time))
    result = get_latest_xlsx_export(settings_with_vault, "NKE")
    assert result == new


def test_list_companies_empty_when_no_vault(settings_tavily) -> None:
    assert list_companies(settings_tavily) == []


def test_list_companies_returns_metadata(settings_with_vault) -> None:
    create_company_folder(
        settings_with_vault, "NKE", company_name="Nike Inc", sector="Consumer"
    )
    create_company_folder(settings_with_vault, "MSFT", company_name="Microsoft")
    result = list_companies(settings_with_vault)
    tickers = [r["ticker"] for r in result]
    assert tickers == ["MSFT", "NKE"]
    nke = next(r for r in result if r["ticker"] == "NKE")
    assert nke["company_name"] == "Nike Inc"
    assert nke["sector"] == "Consumer"
    assert nke["has_parsed"] is False
    assert nke["has_exports"] is False


def test_list_companies_flags_parsed_and_exports(settings_with_vault) -> None:
    create_company_folder(settings_with_vault, "NKE")
    company_dir = get_company_path(settings_with_vault, "NKE")
    (company_dir / "parsed" / "financials.json").write_text("{}", encoding="utf-8")
    (company_dir / "bloomberg_exports" / "x.xlsx").write_bytes(b"x")
    [nke] = list_companies(settings_with_vault)
    assert nke["has_parsed"] is True
    assert nke["has_exports"] is True
