"""Parse Bloomberg/FactSet-style company-history xlsx workbooks.

The supported template (``CompanyHistoryBBV2``) lays out:

- A header block (rows 1-22) with the entered ticker, scale, currency.
- A period row (``FISCAL_YEAR_PERIOD`` in col D) where columns from E onward
  carry per-year period labels (e.g. ``"2025 Y"``).
- A series block (rows ~26+) where col C holds a human label, col D holds a
  Bloomberg/FactSet field code, and the year columns hold the annual values.
- A right-hand "VALUATION" block (around col J onwards) carrying DCF
  scalars (intrinsic value, target price, MOS, discount rate, etc.).
- A business-description block (rows ~169-180) carrying phone, exchange,
  industry, sector, description.

The parser is **lenient**. Unknown labels are ignored. Missing fields
produce ``None`` rather than raising. Sheet anomalies are recorded in the
``warnings`` list on the returned ``FundamentalsSnapshot``.

Output: a populated ``FundamentalsSnapshot`` instance — see
``investor_research.models.FundamentalsSnapshot``.
"""

from __future__ import annotations

import hashlib
import logging
import re
import warnings as _warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.worksheet.worksheet import Worksheet

from investor_research.models import FundamentalsSnapshot
from investor_research.tickers import validate_ticker
from investor_research.vault._xlsx_util import coerce_number as _coerce_number
from investor_research.vault.field_registry import (
    DCF_SCALAR_LABELS,
    METADATA_LABELS,
    SERIES_FIELDS,
    FieldSpec,
    _norm_label,
    code_lookup,
    label_lookup,
)

logger = logging.getLogger(__name__)


# Rows 1-30 contain the metadata + period header block. Anything past row
# 30 we treat as series content.
_HEADER_SCAN_ROWS = 30

# The "Main" sheet is the canonical data source for this template.
_PRIMARY_SHEET_CANDIDATES = ("Main", "main", "MAIN")


class XlsxParseError(RuntimeError):
    """Raised when a workbook is structurally invalid (e.g. no Main sheet)."""


def sha256_of(path: Path) -> str:
    """Return the lowercase hex SHA-256 of a file's bytes."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_format(file_path: Path | str) -> str:
    """Inspect a workbook and return its parser format identifier.

    Returns one of:
      - ``"bbv2_history"``     — per-company time series (CompanyHistoryBBV2)
      - ``"watchlist_scorecard"`` — multi-company watchlist scorecard

    Raises ``XlsxParseError`` when the workbook matches neither shape.
    """
    path = Path(file_path).resolve()
    if not path.is_file():
        raise XlsxParseError(f"workbook not found: {path}")

    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore", UserWarning)
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)

    try:
        # BBV2 signal: "Main" sheet with FISCAL_YEAR_PERIOD in col D rows 1-30.
        for name in _PRIMARY_SHEET_CANDIDATES:
            if name in wb.sheetnames:
                ws = wb[name]
                for row_idx in range(1, _HEADER_SCAN_ROWS + 1):
                    cell = ws.cell(row=row_idx, column=4).value
                    if isinstance(cell, str) and cell.strip() == "FISCAL_YEAR_PERIOD":
                        return "bbv2_history"

        # Watchlist scorecard signal: any visible sheet with "BB Ticker" in D4 + "Long Name" in E4.
        for name in wb.sheetnames:
            ws = wb[name]
            if getattr(ws, "sheet_state", "visible") != "visible":
                continue
            d4 = ws.cell(row=4, column=4).value
            e4 = ws.cell(row=4, column=5).value
            if (
                isinstance(d4, str)
                and d4.strip().lower() == "bb ticker"
                and isinstance(e4, str)
                and e4.strip().lower() == "long name"
            ):
                return "watchlist_scorecard"
    finally:
        wb.close()

    raise XlsxParseError(
        f"unrecognized workbook format: {path.name}. "
        "Supported: CompanyHistoryBBV2 (per-company history) and "
        "multi-company watchlist scorecard."
    )


def parse_workbook(file_path: Path | str) -> FundamentalsSnapshot:
    """Parse a Bloomberg/FactSet company-history xlsx into a snapshot.

    Reads computed values (``data_only=True``) rather than formulas so the
    output contains the resolved numbers the user sees in Excel.
    """
    path = Path(file_path).resolve()
    if not path.is_file():
        raise XlsxParseError(f"workbook not found: {path}")

    with _warnings.catch_warnings():
        # openpyxl logs a UserWarning on some Bloomberg-exported headers; harmless.
        _warnings.simplefilter("ignore", UserWarning)
        wb = openpyxl.load_workbook(path, data_only=True, read_only=False)

    sheet = _select_primary_sheet(wb)
    parse_warnings: list[str] = []

    header = _extract_header(sheet, parse_warnings)
    period_row, period_labels, year_columns = _find_period_row(sheet, parse_warnings)

    series: dict[str, list[float | None]] = {}
    ratios: dict[str, list[float | None]] = {}
    valuation_history: dict[str, list[float | None]] = {}
    seen_keys: set[str] = set()

    if period_row is not None and year_columns:
        labels_by_norm = label_lookup()
        codes_by_upper = code_lookup()
        for row_idx in range(period_row + 1, sheet.max_row + 1):
            label_cell = sheet.cell(row=row_idx, column=3).value
            code_cell = sheet.cell(row=row_idx, column=4).value
            spec = _match_spec(label_cell, code_cell, labels_by_norm, codes_by_upper)
            if spec is None or spec.normalized_key in seen_keys:
                continue
            values = [
                _coerce_number(sheet.cell(row=row_idx, column=col).value)
                for col in year_columns
            ]
            if all(v is None for v in values):
                continue
            seen_keys.add(spec.normalized_key)
            _bucket_for_category(spec.category, series, ratios, valuation_history)[
                spec.normalized_key
            ] = values

    metadata = _extract_metadata_block(sheet, parse_warnings)
    dcf = _extract_dcf_scalars(sheet, parse_warnings)

    ticker_raw = header.get("ticker") or header.get("entered_ticker") or ""
    try:
        ticker = validate_ticker(ticker_raw)
    except Exception:
        ticker = (ticker_raw or path.stem).upper()
        parse_warnings.append(
            f"could not validate ticker from workbook header (got {ticker_raw!r}); using {ticker!r}"
        )

    company_name = header.get("company_name") or metadata.get("company_name")
    units = header.get("units")

    snapshot = FundamentalsSnapshot(
        ticker=ticker,
        company_name=company_name,
        currency=header.get("currency"),
        units=units,
        fiscal_year_end=header.get("fiscal_year_end"),
        periods=period_labels,
        series=series,
        ratios=ratios,
        valuation_history=valuation_history,
        dcf=dcf,
        metadata=metadata,
        source_file=str(path),
        source_sha256=sha256_of(path),
        parsed_at=datetime.now(timezone.utc),
        warnings=parse_warnings,
    )
    logger.info(
        "vault.xlsx_parsed",
        extra={
            "ticker": ticker,
            "periods": len(period_labels),
            "series_keys": len(series),
            "ratio_keys": len(ratios),
            "valuation_keys": len(valuation_history),
            "warnings": len(parse_warnings),
        },
    )
    return snapshot


# ---- internals ----------------------------------------------------------


def _select_primary_sheet(wb: openpyxl.Workbook) -> Worksheet:
    for name in _PRIMARY_SHEET_CANDIDATES:
        if name in wb.sheetnames:
            return wb[name]
    # Fall back to the first visible sheet that isn't a FactSet cache.
    for name in wb.sheetnames:
        if name.startswith("__"):
            continue
        return wb[name]
    raise XlsxParseError("workbook has no readable sheets")


def _extract_header(sheet: Worksheet, warnings_out: list[str]) -> dict[str, Any]:
    """Pull ticker / company / currency / scale from the rows 1-10 block.

    The template uses fixed positions (col B values keyed by col A labels)
    but we read by label-match for robustness against minor row shifts.
    """
    header: dict[str, Any] = {}
    label_to_col_b: dict[str, Any] = {}
    for row_idx in range(1, _HEADER_SCAN_ROWS + 1):
        label = sheet.cell(row=row_idx, column=1).value
        value = sheet.cell(row=row_idx, column=2).value
        if isinstance(label, str) and label.strip():
            label_to_col_b[_norm_label(label)] = value

    entered_ticker = label_to_col_b.get("enter ticker")
    cleaned = label_to_col_b.get("cleaned ticker")
    fundamental = label_to_col_b.get("fundamental ticker")

    # Prefer a Bloomberg-style ticker like "NKE Equity" and strip the suffix.
    candidate = cleaned or fundamental or entered_ticker
    if isinstance(candidate, str):
        candidate = candidate.strip().split()[0] if candidate.strip() else ""
        if candidate:
            header["ticker"] = candidate

    if isinstance(entered_ticker, str):
        header["entered_ticker"] = entered_ticker.strip()

    # Company name typically lives at C3 — labeled to the right of the BBG ticker.
    company_cell = sheet.cell(row=3, column=3).value
    if isinstance(company_cell, str) and company_cell.strip():
        header["company_name"] = company_cell.strip()

    currency = label_to_col_b.get("local currency") or label_to_col_b.get("formula currency")
    if isinstance(currency, str) and currency.strip():
        header["currency"] = currency.strip()

    scale = label_to_col_b.get("scale")
    if isinstance(scale, (int, float)) and scale:
        if scale >= 1_000_000:
            header["units"] = "millions"
        elif scale >= 1_000:
            header["units"] = "thousands"
        else:
            header["units"] = "ones"
        header["scale_factor"] = float(scale)

    if not header.get("ticker"):
        warnings_out.append("could not locate ticker in header block (rows 1-10)")
    return header


def _find_period_row(
    sheet: Worksheet, warnings_out: list[str]
) -> tuple[int | None, list[str], list[int]]:
    """Locate the FISCAL_YEAR_PERIOD row and extract the per-column period labels.

    Returns ``(row_index, period_labels, column_indices)``. When the row
    can't be found, returns ``(None, [], [])`` and appends a warning.
    """
    period_row: int | None = None
    for row_idx in range(1, _HEADER_SCAN_ROWS + 1):
        code_cell = sheet.cell(row=row_idx, column=4).value
        if isinstance(code_cell, str) and code_cell.strip() == "FISCAL_YEAR_PERIOD":
            period_row = row_idx
            break
    if period_row is None:
        warnings_out.append("FISCAL_YEAR_PERIOD row not found in rows 1-30")
        return None, [], []

    period_labels: list[str] = []
    year_columns: list[int] = []
    for col in range(5, sheet.max_column + 1):
        value = sheet.cell(row=period_row, column=col).value
        if value is None:
            continue
        label = str(value).strip()
        if not label:
            continue
        normalized = _normalize_period_label(label)
        if normalized is None:
            # Aggregate columns ("-20 to -15", "20 years", etc.) sit to the right
            # of the per-year cells. They are not periods; skip them.
            continue
        period_labels.append(normalized)
        year_columns.append(col)
    if not period_labels:
        warnings_out.append("FISCAL_YEAR_PERIOD row had no period labels")
    return period_row, period_labels, year_columns


_YEAR_ONLY_RE = re.compile(r"^(\d{4})\s*Y?$", re.IGNORECASE)
_YEAR_QUARTER_RE = re.compile(r"^(\d{4})\s*Q([1-4])$", re.IGNORECASE)


def _normalize_period_label(label: str) -> str | None:
    """Turn a workbook period label into ``FYYYYY`` / ``YYYY_Q#`` or ``None`` to skip."""
    stripped = label.strip()
    match = _YEAR_ONLY_RE.match(stripped)
    if match:
        return f"FY{match.group(1)}"
    match = _YEAR_QUARTER_RE.match(stripped)
    if match:
        return f"FY{match.group(1)}_Q{match.group(2)}"
    return None


def _match_spec(
    label_value: Any,
    code_value: Any,
    labels_by_norm: dict[str, FieldSpec],
    codes_by_upper: dict[str, FieldSpec],
) -> FieldSpec | None:
    if isinstance(code_value, str) and code_value.strip():
        spec = codes_by_upper.get(code_value.strip().upper())
        if spec is not None:
            return spec
    if isinstance(label_value, str) and label_value.strip():
        return labels_by_norm.get(_norm_label(label_value))
    return None


def _bucket_for_category(
    category: str,
    series: dict[str, list[float | None]],
    ratios: dict[str, list[float | None]],
    valuation_history: dict[str, list[float | None]],
) -> dict[str, list[float | None]]:
    if category == "ratios":
        return ratios
    if category == "valuation":
        return valuation_history
    return series


def _extract_metadata_block(sheet: Worksheet, warnings_out: list[str]) -> dict[str, Any]:
    """Walk the business-description block looking for labeled metadata.

    Labels live in column C (rows ~169-180 in the canonical template) and
    values live ~3 columns to the right. We're tolerant about exact
    positions and search across rows 150-220.
    """
    out: dict[str, Any] = {}
    metadata_labels_norm = {label: key for label, key in METADATA_LABELS.items()}
    description_chunks: list[str] = []
    description_active = False
    # Cap the description-continuation scan so it can't bleed into unrelated
    # downstream blocks (DCF, "Discounted Cash Flow", etc.).
    description_remaining_rows = 0
    _MAX_DESCRIPTION_CONTINUATION_ROWS = 8

    for row_idx in range(150, min(sheet.max_row + 1, 230)):
        label_cell = sheet.cell(row=row_idx, column=3).value
        if isinstance(label_cell, str) and label_cell.strip():
            norm = _norm_label(label_cell)
            mapped = metadata_labels_norm.get(norm)
            if mapped:
                value = _first_nonempty(sheet, row_idx, start_col=4, end_col=10)
                if value is not None:
                    if mapped == "description":
                        description_chunks.append(str(value).strip())
                        description_active = True
                        description_remaining_rows = _MAX_DESCRIPTION_CONTINUATION_ROWS
                    else:
                        out[mapped] = value
                        description_active = False
                continue
            # Any other non-empty label row terminates the description block.
            description_active = False
            description_remaining_rows = 0
            continue
        if description_active and description_remaining_rows > 0:
            # On continuation rows the company description text wraps in the
            # same columns as the original "Description:" value (F/G area).
            # Right-hand columns (J+) carry DCF labels — must not be picked up.
            value = _first_nonempty(sheet, row_idx, start_col=4, end_col=7)
            if value is not None:
                description_chunks.append(str(value).strip())
                description_remaining_rows -= 1
            else:
                description_active = False

    if description_chunks:
        out["description"] = " ".join(description_chunks).strip()
    return out


def _extract_dcf_scalars(sheet: Worksheet, warnings_out: list[str]) -> dict[str, float | None]:
    """Pull DCF inputs/outputs from the right-hand VALUATION block.

    Labels are matched case-insensitively in column J (and column C for the
    DCF rows 194+). Values are taken from the first numeric cell to the right.
    """
    out: dict[str, float | None] = {}
    for row_idx in range(150, min(sheet.max_row + 1, 220)):
        for label_col in (3, 10):  # col C (DCF block) and col J (right-hand valuation)
            label_value = sheet.cell(row=row_idx, column=label_col).value
            if not isinstance(label_value, str) or not label_value.strip():
                continue
            norm = _norm_label(label_value)
            mapped = DCF_SCALAR_LABELS.get(norm)
            if not mapped or mapped in out:
                continue
            value = _first_number(sheet, row_idx, start_col=label_col + 1, end_col=label_col + 6)
            if value is not None:
                out[mapped] = value
    return out


def _first_nonempty(
    sheet: Worksheet, row: int, *, start_col: int, end_col: int
) -> Any:
    for col in range(start_col, end_col + 1):
        v = sheet.cell(row=row, column=col).value
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return v
    return None


def _first_number(
    sheet: Worksheet, row: int, *, start_col: int, end_col: int
) -> float | None:
    for col in range(start_col, end_col + 1):
        coerced = _coerce_number(sheet.cell(row=row, column=col).value)
        if coerced is not None:
            return coerced
    return None


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import json
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "CompanyHistoryBBV2 - Nike.xlsx"
    snap = parse_workbook(target)
    print(
        json.dumps(
            {
                "ticker": snap.ticker,
                "company_name": snap.company_name,
                "currency": snap.currency,
                "units": snap.units,
                "periods_n": len(snap.periods),
                "first_period": snap.periods[0] if snap.periods else None,
                "last_period": snap.periods[-1] if snap.periods else None,
                "series_keys": list(snap.series.keys()),
                "ratio_keys": list(snap.ratios.keys()),
                "valuation_keys": list(snap.valuation_history.keys()),
                "dcf": snap.dcf,
                "metadata": snap.metadata,
                "warnings": snap.warnings,
                "revenue_last5": snap.series.get("revenue", [])[-5:],
                "ebit_margin_last5": snap.ratios.get("ebit_margin", [])[-5:],
            },
            indent=2,
            default=str,
        )
    )
