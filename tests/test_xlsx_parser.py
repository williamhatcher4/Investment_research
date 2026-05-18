"""Tests for the Bloomberg/FactSet xlsx parser.

Combines:
- A real-file integration test against the user's Nike workbook
  (skipped automatically when the file is absent).
- Synthetic-workbook unit tests covering header/period parsing,
  unknown labels, and aggregate-column rejection.
"""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from investor_research.vault.xlsx_parser import (
    XlsxParseError,
    _normalize_period_label,
    parse_workbook,
    sha256_of,
)


NIKE_XLSX = (
    Path(__file__).resolve().parents[1] / "CompanyHistoryBBV2 - Nike.xlsx"
)


# ---- normalize_period_label --------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2024 Y", "FY2024"),
        ("2024", "FY2024"),
        ("2024 y", "FY2024"),
        ("2024 Q1", "FY2024_Q1"),
        ("2024 Q4", "FY2024_Q4"),
    ],
)
def test_normalize_period_label_accepts_year_forms(raw: str, expected: str) -> None:
    assert _normalize_period_label(raw) == expected


@pytest.mark.parametrize(
    "bad",
    ["20 years", "-20 to -15", "Last 10", "5 yr AVE", "Previous 10", ""],
)
def test_normalize_period_label_rejects_aggregates(bad: str) -> None:
    assert _normalize_period_label(bad) is None


# ---- Synthetic workbook -------------------------------------------------


def _build_minimal_workbook(path: Path) -> None:
    """Write a tiny CompanyHistoryBBV2-style workbook for unit testing."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Main"

    # Header block.
    ws.cell(row=2, column=1, value="Enter Ticker")
    ws.cell(row=2, column=2, value="msft")
    ws.cell(row=3, column=1, value="Cleaned Ticker")
    ws.cell(row=3, column=2, value="MSFT Equity")
    ws.cell(row=3, column=3, value="Microsoft Corp")
    ws.cell(row=5, column=1, value="Local Currency")
    ws.cell(row=5, column=2, value="USD")
    ws.cell(row=8, column=1, value="Scale")
    ws.cell(row=8, column=2, value=1_000_000)

    # Period row: 3 years + a trailing aggregate column we expect to be skipped.
    ws.cell(row=24, column=4, value="FISCAL_YEAR_PERIOD")
    ws.cell(row=24, column=5, value="2023 Y")
    ws.cell(row=24, column=6, value="2024 Y")
    ws.cell(row=24, column=7, value="2025 Y")
    ws.cell(row=24, column=8, value="20 years")  # aggregate — should be ignored

    # Series rows.
    ws.cell(row=27, column=3, value="Revenue")
    ws.cell(row=27, column=4, value="SALES_REV_TURN")
    ws.cell(row=27, column=5, value=200000)
    ws.cell(row=27, column=6, value=220000)
    ws.cell(row=27, column=7, value=240000)
    ws.cell(row=27, column=8, value=999999)  # aggregate; not loaded

    ws.cell(row=39, column=3, value="EBIT")
    ws.cell(row=39, column=4, value="EBIT")
    ws.cell(row=39, column=5, value=80000)
    ws.cell(row=39, column=6, value=90000)
    ws.cell(row=39, column=7, value=100000)

    # An unknown label that must be ignored.
    ws.cell(row=40, column=3, value="Something the parser doesn't know")
    ws.cell(row=40, column=4, value="UNKNOWN_CODE")
    ws.cell(row=40, column=5, value=1.0)

    # A ratio row.
    ws.cell(row=91, column=3, value="EBIT margin")
    ws.cell(row=91, column=5, value=0.40)
    ws.cell(row=91, column=6, value=0.41)
    ws.cell(row=91, column=7, value=0.42)

    # Business description block.
    ws.cell(row=170, column=3, value="UNITED STATES")
    ws.cell(row=172, column=3, value="Exchange:")
    ws.cell(row=172, column=6, value="NASDAQ")
    ws.cell(row=174, column=3, value="Sector:")
    ws.cell(row=174, column=6, value="Information Technology")
    ws.cell(row=175, column=3, value="Description:")
    ws.cell(row=175, column=6, value="Microsoft makes software and cloud services.")

    # DCF scalar in the right-hand block.
    ws.cell(row=190, column=10, value="Intrinsic value per share")
    ws.cell(row=190, column=11, value=480.0)
    ws.cell(row=193, column=10, value="Target price")
    ws.cell(row=193, column=11, value=520.0)

    wb.save(path)


def test_parse_minimal_workbook(tmp_path: Path) -> None:
    path = tmp_path / "MSFT_minimal.xlsx"
    _build_minimal_workbook(path)

    snap = parse_workbook(path)
    assert snap.ticker == "MSFT"
    assert snap.company_name == "Microsoft Corp"
    assert snap.currency == "USD"
    assert snap.units == "millions"
    assert snap.periods == ["FY2023", "FY2024", "FY2025"]
    assert snap.series["revenue"] == [200000.0, 220000.0, 240000.0]
    assert snap.series["ebit"] == [80000.0, 90000.0, 100000.0]
    assert snap.ratios["ebit_margin"] == [0.40, 0.41, 0.42]
    assert "Something the parser doesn't know" not in snap.series
    assert snap.metadata.get("exchange") == "NASDAQ"
    assert snap.metadata.get("sector") == "Information Technology"
    assert "Microsoft makes software" in snap.metadata.get("description", "")
    assert snap.dcf.get("intrinsic_value_per_share") == 480.0
    assert snap.dcf.get("target_price") == 520.0
    assert snap.source_sha256 == sha256_of(path)
    assert snap.warnings == []


def test_parse_workbook_missing_file(tmp_path: Path) -> None:
    with pytest.raises(XlsxParseError):
        parse_workbook(tmp_path / "nope.xlsx")


def test_parse_workbook_warns_when_no_period_row(tmp_path: Path) -> None:
    path = tmp_path / "no_periods.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Main"
    ws.cell(row=3, column=1, value="Cleaned Ticker")
    ws.cell(row=3, column=2, value="ZZZZ Equity")
    wb.save(path)

    snap = parse_workbook(path)
    assert snap.ticker == "ZZZZ"
    assert snap.periods == []
    assert any("FISCAL_YEAR_PERIOD" in w for w in snap.warnings)


# ---- Real Nike file (skipped when absent) ------------------------------


@pytest.mark.skipif(not NIKE_XLSX.exists(), reason="Nike sample xlsx not present")
def test_parse_real_nike_workbook() -> None:
    snap = parse_workbook(NIKE_XLSX)
    assert snap.ticker == "NKE"
    assert snap.company_name == "NIKE Inc"
    assert snap.currency == "USD"
    assert snap.units == "millions"
    # Nike fiscal years 2004–2025 = 22 annual periods.
    assert len(snap.periods) == 22
    assert snap.periods[0] == "FY2004"
    assert snap.periods[-1] == "FY2025"
    # Revenue ground-truth tail: FY2025 ≈ $46.3B, FY2024 ≈ $51.4B.
    rev = snap.series["revenue"]
    assert rev[-1] == pytest.approx(46309, abs=100)
    assert rev[-2] == pytest.approx(51362, abs=100)
    # Margins and returns present.
    assert "ebit_margin" in snap.ratios
    assert "roe" in snap.ratios
    assert "roce_post_tax" in snap.ratios
    # DCF block extracted.
    assert "discount_rate" in snap.dcf
    assert "target_price" in snap.dcf
    # Metadata description bounded, not bleeding into DCF block.
    desc = snap.metadata.get("description", "")
    assert "athletic footwear" in desc.lower()
    assert "terminal growth" not in desc.lower()
