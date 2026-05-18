"""Tests for the SQLite index over parsed vault JSON."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import openpyxl
import pytest

from investor_research.models import FundamentalsSnapshot
from investor_research.vault.database import (
    connect,
    db_path,
    list_indexed_companies,
    query_metric,
    reindex_vault,
)
from investor_research.vault.notes import write_memo_to_vault
from investor_research.vault.paths import (
    VaultNotConfiguredError,
    create_company_folder,
    get_company_path,
)
from investor_research.vault.snapshots import import_xlsx, write_parsed_json


def _build_minimal_workbook(path: Path, ticker: str, revenue: list[float]) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Main"
    ws.cell(row=3, column=1, value="Cleaned Ticker")
    ws.cell(row=3, column=2, value=f"{ticker} Equity")
    ws.cell(row=3, column=3, value=f"{ticker} Inc")
    ws.cell(row=5, column=1, value="Local Currency")
    ws.cell(row=5, column=2, value="USD")
    ws.cell(row=8, column=1, value="Scale")
    ws.cell(row=8, column=2, value=1_000_000)
    ws.cell(row=24, column=4, value="FISCAL_YEAR_PERIOD")
    for i, year in enumerate([2023, 2024, 2025]):
        ws.cell(row=24, column=5 + i, value=f"{year} Y")
    ws.cell(row=27, column=3, value="Revenue")
    ws.cell(row=27, column=4, value="SALES_REV_TURN")
    for i, value in enumerate(revenue):
        ws.cell(row=27, column=5 + i, value=value)
    ws.cell(row=91, column=3, value="EBIT margin")
    for i, value in enumerate([0.20, 0.22, 0.21]):
        ws.cell(row=91, column=5 + i, value=value)
    wb.save(path)


def test_connect_raises_without_vault(settings_tavily) -> None:
    with pytest.raises(VaultNotConfiguredError):
        connect(settings_tavily)


def test_connect_disabled_when_flag_false(settings_with_vault) -> None:
    disabled = replace(settings_with_vault, enable_sqlite=False)
    with pytest.raises(RuntimeError):
        connect(disabled)


def test_reindex_populates_companies_and_metrics(
    settings_with_vault, tmp_path: Path
) -> None:
    src = tmp_path / "MSFT.xlsx"
    _build_minimal_workbook(src, "MSFT", [200000, 220000, 240000])
    import_xlsx(settings_with_vault, "MSFT", src)
    src2 = tmp_path / "GOOGL.xlsx"
    _build_minimal_workbook(src2, "GOOGL", [280000, 305000, 330000])
    import_xlsx(settings_with_vault, "GOOGL", src2)

    counts = reindex_vault(settings_with_vault)
    assert counts["companies"] == 2
    assert counts["source_files"] == 2
    assert counts["metrics"] > 0
    assert db_path(settings_with_vault).is_file()

    rows = list_indexed_companies(settings_with_vault)
    tickers = [r["ticker"] for r in rows]
    assert tickers == ["GOOGL", "MSFT"]


def test_reindex_is_idempotent(settings_with_vault, tmp_path: Path) -> None:
    src = tmp_path / "MSFT.xlsx"
    _build_minimal_workbook(src, "MSFT", [200000, 220000, 240000])
    import_xlsx(settings_with_vault, "MSFT", src)

    first = reindex_vault(settings_with_vault)
    second = reindex_vault(settings_with_vault)
    assert first == second


def test_query_metric_filters_and_returns_rows(
    settings_with_vault, tmp_path: Path
) -> None:
    src = tmp_path / "MSFT.xlsx"
    _build_minimal_workbook(src, "MSFT", [200000, 220000, 240000])
    import_xlsx(settings_with_vault, "MSFT", src)
    reindex_vault(settings_with_vault)

    rows = query_metric(settings_with_vault, "revenue", tickers=["MSFT"])
    periods = [r["period"] for r in rows]
    values = [r["value"] for r in rows]
    assert periods == ["FY2023", "FY2024", "FY2025"]
    assert values == [200000.0, 220000.0, 240000.0]

    fy25 = query_metric(settings_with_vault, "revenue", period="FY2025")
    assert len(fy25) == 1
    assert fy25[0]["value"] == 240000.0

    filtered = query_metric(
        settings_with_vault, "revenue", min_value=210000, max_value=230000
    )
    assert len(filtered) == 1
    assert filtered[0]["period"] == "FY2024"


def test_query_metric_returns_empty_when_disabled(settings_with_vault) -> None:
    disabled = replace(settings_with_vault, enable_sqlite=False)
    assert query_metric(disabled, "revenue") == []
    assert list_indexed_companies(disabled) == []


def test_reindex_includes_memos(settings_with_vault, tmp_path: Path) -> None:
    src = tmp_path / "MSFT.xlsx"
    _build_minimal_workbook(src, "MSFT", [200000, 220000, 240000])
    import_xlsx(settings_with_vault, "MSFT", src)
    write_memo_to_vault(
        settings_with_vault,
        "MSFT",
        "# Memo",
        memo_type="investment_memo",
        date="2026-05-18",
    )
    counts = reindex_vault(settings_with_vault)
    assert counts["memos"] == 1


def test_reindex_includes_ad_hoc_notes(settings_with_vault, tmp_path: Path) -> None:
    src = tmp_path / "MSFT.xlsx"
    _build_minimal_workbook(src, "MSFT", [200000, 220000, 240000])
    import_xlsx(settings_with_vault, "MSFT", src)
    notes_dir = get_company_path(settings_with_vault, "MSFT") / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / "azure_acceleration.md").write_text("body", encoding="utf-8")
    counts = reindex_vault(settings_with_vault)
    assert counts["notes"] == 1
