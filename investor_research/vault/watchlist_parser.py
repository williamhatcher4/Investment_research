"""Parse Hatcher-style multi-company watchlist scorecard workbooks.

The supported layout (``Hatcher_HL_Workbook_BB_V3``) puts all companies on
a single date-named sheet. The header occupies rows 1-5 with section
bands across the top and sub-labels (``5 years`` / ``10 years``) on row 5.
Company rows begin at row 6, one per company, with the Bloomberg ticker
in column D.

The parser is lenient: error cells (``#REF!`` / ``#NAME?`` / ``#VALUE!``)
become ``None``; unknown columns flow through to ``raw_columns``; the
ticker alias resolution is delegated to the caller.

Output: a populated ``WatchlistSnapshot`` instance — see
``investor_research.models.WatchlistSnapshot``.
"""

from __future__ import annotations

import logging
import re
import warnings as _warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from investor_research.models import WatchlistRow, WatchlistSnapshot
from investor_research.vault._xlsx_util import coerce_number
from investor_research.vault.xlsx_parser import XlsxParseError, sha256_of

logger = logging.getLogger(__name__)


# Column index (1-based) -> normalized numeric metric key.
# Verified against Hatcher_HL_Workbook_BB_V3_Jan_12_2026_Clean_V16.xlsx (Jan 2026).
_NUMERIC_METRIC_COLUMNS: dict[int, str] = {
    10: "revenue_growth_5y",         # J
    11: "revenue_growth_10y",        # K
    12: "fcf_per_share_growth_5y",   # L
    13: "fcf_per_share_growth_10y",  # M
    14: "roce_tangible_post_tax_5y",  # N
    15: "roce_tangible_post_tax_10y",  # O
    16: "roce_post_tax_5y",          # P
    17: "roce_post_tax_10y",         # Q
    18: "roe_5y",                    # R
    19: "wc_to_sales_5y",            # S
    20: "capex_to_sales_5y",         # T
    21: "gross_margin_5y",           # U
    22: "gross_margin_10y",          # V
    23: "ebita_margin_5y",           # W
    24: "ebita_margin_10y",          # X
    25: "fcf_conversion_5y",         # Y
    26: "fcf_conversion_10y",        # Z
    27: "dividend_payout_5y",        # AA
    28: "dividend_return_5y",        # AB
    29: "buyback_return_5y",         # AC
    30: "total_return_5y",           # AD
    31: "cash_to_ma_5y",             # AE
    32: "cash_from_debt_5y",         # AF
    33: "dividend_return_10y",       # AG
    34: "buyback_return_10y",        # AH
    35: "total_return_10y",          # AI
    36: "cash_to_ma_10y",            # AJ
    37: "cash_from_debt_10y",        # AK
    38: "pe_fy1",                    # AL
    39: "pe_fy2",                    # AM
    40: "ev_ebit_fy1",               # AN
    41: "ev_ebit_fy2",               # AO
    42: "p_fcf_fy1",                 # AP
    43: "p_fcf_fy2",                 # AQ
    44: "net_debt_to_ebitda",        # AR
    47: "dividend_yield",            # AU
    48: "return_1y",                 # AV
    49: "return_ytd",                # AW
    50: "return_5y",                 # AX
    51: "eps_fy1",                   # AY
    52: "eps_fy2",                   # AZ
    53: "fcf_per_share_fy1",         # BA
    54: "fcf_per_share_fy2",         # BB
    62: "pe_current_fy1",            # BJ
    63: "pe_current_fy2",            # BK
    64: "price_to_fv",               # BL
    70: "morningstar_star_rating",   # BR (numeric: 1-5)
    71: "morningstar_5star_price",   # BS
    72: "morningstar_fair_value",    # BT
}

# String-valued Morningstar columns (kept as raw strings, not coerced).
_STRING_RATING_COLUMNS: dict[int, str] = {
    67: "morningstar_moat",              # BO
    68: "morningstar_cap_allocation",    # BP
    69: "morningstar_risk",              # BQ
}

# Top-level row fields (extracted directly onto the WatchlistRow).
_TICKER_COL = 4   # D
_NAME_COL = 5     # E
_SECTOR_COL = 6   # F
_PRICE_COL = 7    # G
_IV_COL = 8       # H
_MOS_COL = 9      # I

# Sentinel values for "this row is empty / broken".
_ROW_STOP_TOKENS = {"#REF!", "#N/A"}


_DATE_IN_SHEETNAME_RE = re.compile(
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+(\d{1,2})[,\s]+(\d{4})",
    re.IGNORECASE,
)
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_watchlist_workbook(file_path: Path | str) -> WatchlistSnapshot:
    """Parse a Hatcher-style watchlist scorecard xlsx into a snapshot.

    Reads computed values (``data_only=True``) so error cells come through
    as the literal strings ``"#NAME?"`` / ``"#REF!"`` etc., which the
    coercer maps to ``None``.
    """
    path = Path(file_path).resolve()
    if not path.is_file():
        raise XlsxParseError(f"workbook not found: {path}")

    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore", UserWarning)
        wb = openpyxl.load_workbook(path, data_only=True, read_only=False)

    sheet = _select_watchlist_sheet(wb)
    parse_warnings: list[str] = []

    _validate_header(sheet, parse_warnings)

    rows: list[WatchlistRow] = []
    for row_idx in range(6, sheet.max_row + 1):
        bb_value = sheet.cell(row=row_idx, column=_TICKER_COL).value
        if not _is_real_ticker(bb_value):
            # Empty cells / formula error sentinels / non-strings — stop scanning.
            if _looks_like_terminal_row(sheet, row_idx):
                break
            continue
        row = _parse_company_row(sheet, row_idx)
        rows.append(row)

    snapshot = WatchlistSnapshot(
        source_file=str(path),
        source_sha256=sha256_of(path),
        sheet_name=sheet.title,
        snapshot_date=_parse_sheet_date(sheet.title),
        parsed_at=datetime.now(timezone.utc),
        rows=rows,
        warnings=parse_warnings,
    )
    logger.info(
        "vault.watchlist_parsed",
        extra={"sheet": sheet.title, "rows": len(rows), "warnings": len(parse_warnings)},
    )
    return snapshot


# ---- internals ----------------------------------------------------------


def _select_watchlist_sheet(wb: openpyxl.Workbook) -> Worksheet:
    """Pick the first visible sheet whose row 4 has the watchlist header."""
    for name in wb.sheetnames:
        ws = wb[name]
        if getattr(ws, "sheet_state", "visible") != "visible":
            continue
        d4 = ws.cell(row=4, column=_TICKER_COL).value
        e4 = ws.cell(row=4, column=_NAME_COL).value
        if (
            isinstance(d4, str)
            and d4.strip().lower() == "bb ticker"
            and isinstance(e4, str)
            and e4.strip().lower() == "long name"
        ):
            return ws
    raise XlsxParseError(
        "no watchlist sheet found (expected 'BB Ticker' in D4 and 'Long Name' in E4)"
    )


def _validate_header(sheet: Worksheet, warnings_out: list[str]) -> None:
    """Sanity-check key headers; emit warnings on drift so column indices stay honest."""
    expected = [
        (4, 14, ("Tangible Post Tax",)),
        (4, 16, ("Post Tax",)),
        (4, 38, ("P/E1",)),   # AL4
        (4, 70, ("Rating",)),  # BR4 — Morningstar star rating
        (4, 72, ("Fair Value",)),  # BT4
        (5, 7, ("Price",)),    # G5
        (5, 8, ("IV",)),       # H5
        (5, 9, ("MoS",)),      # I5
    ]
    for row, col, candidates in expected:
        value = sheet.cell(row=row, column=col).value
        if not isinstance(value, str) or value.strip() not in candidates:
            warnings_out.append(
                f"unexpected header at {get_column_letter(col)}{row}: "
                f"got {value!r}, expected one of {candidates}"
            )


def _is_real_ticker(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if not stripped or stripped.upper() in _ROW_STOP_TOKENS:
        return False
    return True


def _looks_like_terminal_row(sheet: Worksheet, row_idx: int) -> bool:
    """Stop scanning once we see #REF!-filled rows AND we're past the data block.

    The workbook has spare numbered rows (101-120) with empty strings between
    data rows and the broken #REF! tail. Keep scanning unless we hit row 200+
    or the ticker cell is #REF! AND col A is also #REF!.
    """
    if row_idx > 200:
        return True
    a_val = sheet.cell(row=row_idx, column=1).value
    d_val = sheet.cell(row=row_idx, column=_TICKER_COL).value
    return isinstance(a_val, str) and isinstance(d_val, str) and (
        a_val.strip().upper() in _ROW_STOP_TOKENS
        or d_val.strip().upper() in _ROW_STOP_TOKENS
    )


def _parse_company_row(sheet: Worksheet, row_idx: int) -> WatchlistRow:
    bb_ticker = str(sheet.cell(row=row_idx, column=_TICKER_COL).value).strip()
    long_name = _string_or_none(sheet.cell(row=row_idx, column=_NAME_COL).value)
    sector = _string_or_none(sheet.cell(row=row_idx, column=_SECTOR_COL).value)

    metrics: dict[str, float | None] = {}
    for col, key in _NUMERIC_METRIC_COLUMNS.items():
        metrics[key] = coerce_number(sheet.cell(row=row_idx, column=col).value)

    ratings: dict[str, str | None] = {}
    for col, key in _STRING_RATING_COLUMNS.items():
        ratings[key] = _string_or_none(sheet.cell(row=row_idx, column=col).value)

    raw_columns = _collect_raw_columns(sheet, row_idx)

    return WatchlistRow(
        bloomberg_ticker=bb_ticker,
        normalized_ticker=None,
        long_name=long_name,
        sector=sector,
        price=coerce_number(sheet.cell(row=row_idx, column=_PRICE_COL).value),
        intrinsic_value=coerce_number(sheet.cell(row=row_idx, column=_IV_COL).value),
        margin_of_safety=coerce_number(sheet.cell(row=row_idx, column=_MOS_COL).value),
        metrics=metrics,
        ratings=ratings,
        raw_columns=raw_columns,
    )


def _collect_raw_columns(sheet: Worksheet, row_idx: int) -> dict[str, Any]:
    """Dump every populated column for this row that wasn't claimed by a normalized field.

    Keyed by column letter (``"AS"``, ``"BD"``, ...). Coerces numbers when
    the value looks numeric; otherwise stores the string (with error tokens
    replaced by ``None``).
    """
    claimed = {_TICKER_COL, _NAME_COL, _SECTOR_COL, _PRICE_COL, _IV_COL, _MOS_COL}
    claimed.update(_NUMERIC_METRIC_COLUMNS.keys())
    claimed.update(_STRING_RATING_COLUMNS.keys())

    out: dict[str, Any] = {}
    for col in range(1, sheet.max_column + 1):
        if col in claimed:
            continue
        value = sheet.cell(row=row_idx, column=col).value
        if value is None or value == "":
            continue
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                out[get_column_letter(col)] = None
                continue
            # Try to coerce; fall back to string.
            number = coerce_number(stripped)
            out[get_column_letter(col)] = number if number is not None else stripped
        elif isinstance(value, bool):
            continue
        elif isinstance(value, (int, float)):
            number = coerce_number(value)
            if number is not None:
                out[get_column_letter(col)] = number
        else:
            out[get_column_letter(col)] = str(value)
    return out


def _string_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or stripped.startswith("#"):
        return None
    return stripped


def _parse_sheet_date(sheet_name: str) -> str | None:
    """Extract ``YYYY-MM-DD`` from a sheet name like ``"Jan 12 2026"``."""
    match = _DATE_IN_SHEETNAME_RE.search(sheet_name)
    if not match:
        return None
    month = _MONTHS.get(match.group(1)[:3].lower())
    if not month:
        return None
    try:
        day = int(match.group(2))
        year = int(match.group(3))
        return f"{year:04d}-{month:02d}-{day:02d}"
    except (TypeError, ValueError):
        return None
