"""Tests for parsed JSON read/write and the import_xlsx pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import openpyxl
import pytest

from investor_research.models import FundamentalsSnapshot
from investor_research.vault.paths import create_company_folder, get_company_path
from investor_research.vault.snapshots import (
    FINANCIALS_FILE,
    METADATA_FILE,
    VALUATION_FILE,
    import_xlsx,
    load_parsed_json,
    refresh_from_latest_xlsx,
    write_parsed_json,
)


def _sample_snapshot(ticker: str = "MSFT") -> FundamentalsSnapshot:
    return FundamentalsSnapshot(
        ticker=ticker,
        company_name="Microsoft Corp",
        currency="USD",
        units="millions",
        periods=["FY2023", "FY2024", "FY2025"],
        series={"revenue": [200000.0, 220000.0, 240000.0]},
        ratios={"ebit_margin": [0.40, 0.41, 0.42]},
        valuation_history={"share_price_high": [340.0, 460.0, 470.0]},
        dcf={"target_price": 520.0, "intrinsic_value_per_share": 480.0},
        metadata={"sector": "Information Technology", "description": "Cloud + software."},
        source_file="/tmp/fake.xlsx",
        source_sha256="abc123",
        parsed_at=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
    )


def _build_minimal_msft_workbook(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Main"
    ws.cell(row=3, column=1, value="Cleaned Ticker")
    ws.cell(row=3, column=2, value="MSFT Equity")
    ws.cell(row=3, column=3, value="Microsoft Corp")
    ws.cell(row=5, column=1, value="Local Currency")
    ws.cell(row=5, column=2, value="USD")
    ws.cell(row=8, column=1, value="Scale")
    ws.cell(row=8, column=2, value=1_000_000)
    ws.cell(row=24, column=4, value="FISCAL_YEAR_PERIOD")
    ws.cell(row=24, column=5, value="2024 Y")
    ws.cell(row=24, column=6, value="2025 Y")
    ws.cell(row=27, column=3, value="Revenue")
    ws.cell(row=27, column=4, value="SALES_REV_TURN")
    ws.cell(row=27, column=5, value=220000)
    ws.cell(row=27, column=6, value=240000)
    wb.save(path)


def test_write_then_load_parsed_json_roundtrip(settings_with_vault) -> None:
    create_company_folder(settings_with_vault, "MSFT")
    snapshot = _sample_snapshot("MSFT")
    paths = write_parsed_json(settings_with_vault, snapshot)
    assert paths["financials"].name == FINANCIALS_FILE
    assert paths["valuation"].name == VALUATION_FILE
    assert paths["metadata"].name == METADATA_FILE
    for p in paths.values():
        assert p.is_file()

    loaded = load_parsed_json(settings_with_vault, "MSFT")
    assert loaded is not None
    assert loaded.ticker == "MSFT"
    assert loaded.company_name == "Microsoft Corp"
    assert loaded.currency == "USD"
    assert loaded.periods == ["FY2023", "FY2024", "FY2025"]
    assert loaded.series == {"revenue": [200000.0, 220000.0, 240000.0]}
    assert loaded.ratios == {"ebit_margin": [0.40, 0.41, 0.42]}
    assert loaded.valuation_history == {"share_price_high": [340.0, 460.0, 470.0]}
    assert loaded.dcf == {"target_price": 520.0, "intrinsic_value_per_share": 480.0}
    assert loaded.metadata["sector"] == "Information Technology"
    assert loaded.source_sha256 == "abc123"


def test_load_parsed_json_returns_none_when_missing(settings_with_vault) -> None:
    assert load_parsed_json(settings_with_vault, "MSFT") is None


def test_import_xlsx_full_pipeline(settings_with_vault, tmp_path: Path) -> None:
    src = tmp_path / "msft_export.xlsx"
    _build_minimal_msft_workbook(src)

    result = import_xlsx(settings_with_vault, "MSFT", src)
    assert result["ticker"] == "MSFT"
    assert result["deduped"] is False
    assert result["source_sha256"]
    assert Path(result["xlsx_path"]).parent.name == "bloomberg_exports"
    # The copied file lives in the vault, not at the original location.
    assert Path(result["xlsx_path"]).is_file()
    assert Path(result["xlsx_path"]).resolve() != src.resolve()
    # Parsed JSON written.
    parsed_dir = get_company_path(settings_with_vault, "MSFT") / "parsed"
    assert (parsed_dir / FINANCIALS_FILE).is_file()
    # And reloadable.
    snap = load_parsed_json(settings_with_vault, "MSFT")
    assert snap is not None
    assert snap.series["revenue"] == [220000.0, 240000.0]


def test_import_xlsx_dedupes_on_hash(settings_with_vault, tmp_path: Path) -> None:
    src = tmp_path / "msft_export.xlsx"
    _build_minimal_msft_workbook(src)
    first = import_xlsx(settings_with_vault, "MSFT", src)
    second = import_xlsx(settings_with_vault, "MSFT", src)
    assert second["deduped"] is True
    assert second["xlsx_path"] == first["xlsx_path"]
    # Only one file in bloomberg_exports.
    exports = get_company_path(settings_with_vault, "MSFT") / "bloomberg_exports"
    assert len(list(exports.glob("*.xlsx"))) == 1


def test_refresh_from_latest_xlsx_raises_when_empty(settings_with_vault) -> None:
    create_company_folder(settings_with_vault, "MSFT")
    with pytest.raises(FileNotFoundError):
        refresh_from_latest_xlsx(settings_with_vault, "MSFT")


def test_refresh_from_latest_xlsx_reparses(settings_with_vault, tmp_path: Path) -> None:
    src = tmp_path / "msft.xlsx"
    _build_minimal_msft_workbook(src)
    import_xlsx(settings_with_vault, "MSFT", src)
    # Mutate the parsed JSON so we can detect a re-parse overwrites it.
    parsed_path = (
        get_company_path(settings_with_vault, "MSFT") / "parsed" / FINANCIALS_FILE
    )
    parsed_path.write_text('{"ticker": "MSFT", "series": {}}', encoding="utf-8")

    refresh_from_latest_xlsx(settings_with_vault, "MSFT")
    snap = load_parsed_json(settings_with_vault, "MSFT")
    assert snap is not None
    assert snap.series.get("revenue") == [220000.0, 240000.0]
