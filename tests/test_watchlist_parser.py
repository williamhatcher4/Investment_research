"""Tests for the Hatcher watchlist scorecard xlsx parser."""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from investor_research.vault.watchlist_parser import parse_watchlist_workbook
from investor_research.vault.xlsx_parser import XlsxParseError, detect_format


def _build_minimal_watchlist(path: Path) -> None:
    """Build a 2-row synthetic workbook mimicking the Hatcher scorecard layout."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Jan 12 2026"

    # Row 1: section bands
    ws.cell(row=1, column=10, value="Growth")
    ws.cell(row=1, column=14, value="Capital and Intensity")
    ws.cell(row=1, column=21, value="Margins")
    ws.cell(row=1, column=25, value="Cash Generation and Use of Cash")
    ws.cell(row=1, column=38, value="Valuation")

    # Row 2: Morningstar sub-band
    ws.cell(row=2, column=66, value="Morningstar")

    # Row 4: canonical headers
    headers_row_4 = {
        1: "Count", 2: "Lead", 3: "Status", 4: "BB Ticker", 5: "Long Name",
        14: "Tangible Post Tax", 16: "Post Tax",
        18: "ROE", 19: "WC/Sales", 20: "Capex/Sales",
        21: "GM", 22: "GM", 23: "EBITA Margin", 24: "EBITA Margin",
        25: "FCF Conversion",
        27: "Div Payout",
        28: "Dividend", 29: "Buyback", 30: "Total", 31: "To M&A", 32: "From Debt",
        33: "Dividend", 34: "Buyback", 35: "Total", 36: "To M&A", 37: "From Debt",
        44: "EBITDA",
        47: "Yield", 48: "return", 49: "Return", 50: "Return",
        62: "P/E1", 63: "P/E2", 64: "P/FV",
        67: "Moat", 68: "Cap Alloc", 69: "Risk", 70: "Rating",
        71: "5 Star Price", 72: "Fair Value",
    }
    for col, val in headers_row_4.items():
        ws.cell(row=4, column=col, value=val)

    # Row 5: sub-bands
    row_5 = {
        7: "Price", 8: "IV", 9: "MoS",
        10: "5 years", 11: "10 years", 12: "5 years", 13: "10 years",
        14: "5 years", 15: "10 years", 16: "5 years", 17: "10 years",
        18: "Years", 19: "5 Years", 20: "5 Years",
        21: "5 Years", 22: "10 Years", 23: "5 Years", 24: "10 Years",
        25: "5 Years", 26: "10 years",
    }
    for col, val in row_5.items():
        ws.cell(row=5, column=col, value=val)

    # Row 6: AAPL
    ws.cell(row=6, column=1, value=1)
    ws.cell(row=6, column=4, value="aapl us equity")
    ws.cell(row=6, column=5, value="Apple Inc")
    ws.cell(row=6, column=6, value="Information Technology")
    ws.cell(row=6, column=7, value=260.25)   # price
    ws.cell(row=6, column=10, value=0.087)   # revenue_growth_5y
    ws.cell(row=6, column=14, value=30.4)    # roce_tangible_post_tax_5y
    ws.cell(row=6, column=21, value=0.445)   # gross_margin_5y
    ws.cell(row=6, column=38, value=31.6)    # pe_fy1
    ws.cell(row=6, column=44, value=-0.125)  # net_debt_to_ebitda
    ws.cell(row=6, column=67, value="Wide")  # morningstar_moat
    ws.cell(row=6, column=68, value="Exemplary")
    ws.cell(row=6, column=69, value="Medium")
    ws.cell(row=6, column=70, value=3)        # star rating
    ws.cell(row=6, column=72, value=260)      # fair value

    # Row 7: BF/B (US ticker w/ slash → should be parseable downstream)
    ws.cell(row=7, column=1, value=2)
    ws.cell(row=7, column=4, value="BF/B US Equity")
    ws.cell(row=7, column=5, value="Brown-Forman Corp")
    ws.cell(row=7, column=6, value="Consumer Staples")
    ws.cell(row=7, column=7, value="#NAME?")  # error string -> None after coerce
    ws.cell(row=7, column=10, value=0.025)
    ws.cell(row=7, column=67, value="Wide")

    # Row 8: ASML (non-US; alias must be added manually downstream)
    ws.cell(row=8, column=1, value=3)
    ws.cell(row=8, column=4, value="ASML NA Equity")
    ws.cell(row=8, column=5, value="ASML Holding NV")
    ws.cell(row=8, column=6, value="Information Technology")
    ws.cell(row=8, column=7, value=900.0)

    # Row 9: a row mimicking a broken trailing row — purely #REF! sentinels
    ws.cell(row=9, column=1, value="#REF!")
    ws.cell(row=9, column=4, value="#REF!")

    wb.save(path)


def test_detect_format_watchlist(tmp_path: Path) -> None:
    src = tmp_path / "hatcher.xlsx"
    _build_minimal_watchlist(src)
    assert detect_format(src) == "hatcher_watchlist"


def test_parse_watchlist_three_rows(tmp_path: Path) -> None:
    src = tmp_path / "hatcher.xlsx"
    _build_minimal_watchlist(src)
    snap = parse_watchlist_workbook(src)
    assert snap.sheet_name == "Jan 12 2026"
    assert snap.snapshot_date == "2026-01-12"
    assert len(snap.rows) == 3
    tickers = [r.bloomberg_ticker for r in snap.rows]
    assert tickers == ["aapl us equity", "BF/B US Equity", "ASML NA Equity"]


def test_parse_watchlist_normalized_metrics_for_aapl(tmp_path: Path) -> None:
    src = tmp_path / "hatcher.xlsx"
    _build_minimal_watchlist(src)
    snap = parse_watchlist_workbook(src)
    aapl = snap.rows[0]
    assert aapl.long_name == "Apple Inc"
    assert aapl.sector == "Information Technology"
    assert aapl.price == pytest.approx(260.25)
    assert aapl.metrics["revenue_growth_5y"] == pytest.approx(0.087)
    assert aapl.metrics["roce_tangible_post_tax_5y"] == pytest.approx(30.4)
    assert aapl.metrics["gross_margin_5y"] == pytest.approx(0.445)
    assert aapl.metrics["pe_fy1"] == pytest.approx(31.6)
    assert aapl.metrics["net_debt_to_ebitda"] == pytest.approx(-0.125)
    assert aapl.metrics["morningstar_star_rating"] == pytest.approx(3)
    assert aapl.metrics["morningstar_fair_value"] == pytest.approx(260)


def test_parse_watchlist_morningstar_strings_kept(tmp_path: Path) -> None:
    src = tmp_path / "hatcher.xlsx"
    _build_minimal_watchlist(src)
    snap = parse_watchlist_workbook(src)
    aapl = snap.rows[0]
    assert aapl.ratings["morningstar_moat"] == "Wide"
    assert aapl.ratings["morningstar_cap_allocation"] == "Exemplary"
    assert aapl.ratings["morningstar_risk"] == "Medium"


def test_parse_watchlist_error_cell_becomes_none(tmp_path: Path) -> None:
    src = tmp_path / "hatcher.xlsx"
    _build_minimal_watchlist(src)
    snap = parse_watchlist_workbook(src)
    bfb = snap.rows[1]
    assert bfb.bloomberg_ticker == "BF/B US Equity"
    assert bfb.price is None  # came from "#NAME?"


def test_parse_watchlist_terminal_ref_row_stops_scan(tmp_path: Path) -> None:
    src = tmp_path / "hatcher.xlsx"
    _build_minimal_watchlist(src)
    snap = parse_watchlist_workbook(src)
    # The #REF! row at row 9 must not appear as a parsed company.
    assert all(r.bloomberg_ticker != "#REF!" for r in snap.rows)


def test_parse_watchlist_normalized_ticker_unset_pre_alias(tmp_path: Path) -> None:
    """The parser itself does not resolve aliases — the snapshot orchestrator does."""
    src = tmp_path / "hatcher.xlsx"
    _build_minimal_watchlist(src)
    snap = parse_watchlist_workbook(src)
    assert all(r.normalized_ticker is None for r in snap.rows)


def test_parse_watchlist_raw_columns_for_unmapped(tmp_path: Path) -> None:
    """Adding a stray populated column should land in raw_columns under its column letter."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "May 21 2026"
    ws.cell(row=4, column=4, value="BB Ticker")
    ws.cell(row=4, column=5, value="Long Name")
    ws.cell(row=4, column=80, value="Custom")  # column CB
    ws.cell(row=6, column=1, value=1)
    ws.cell(row=6, column=4, value="msft us equity")
    ws.cell(row=6, column=5, value="Microsoft Corp")
    ws.cell(row=6, column=80, value=12.5)
    src = tmp_path / "synth.xlsx"
    wb.save(src)

    snap = parse_watchlist_workbook(src)
    assert len(snap.rows) == 1
    row = snap.rows[0]
    assert row.raw_columns.get("CB") == pytest.approx(12.5)


def test_parse_watchlist_missing_sheet_raises(tmp_path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "EmptySheet"
    ws.cell(row=4, column=4, value="not bb ticker")
    src = tmp_path / "bad.xlsx"
    wb.save(src)
    with pytest.raises(XlsxParseError):
        parse_watchlist_workbook(src)
