"""Read/write parsed JSON snapshots in ``Companies/{TICKER}/parsed/``.

The parsed JSON is split across three files for human readability and
focused diffing:

- ``financials.json`` — series + ratios + balance sheet (the 20yr history).
- ``valuation.json``  — DCF scalars + valuation history (high/low ranges).
- ``metadata.json``   — company description, sector, source-file pointer.

A ``FundamentalsSnapshot`` round-trips through these three files.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from investor_research.config import Settings
from investor_research.models import FundamentalsSnapshot
from investor_research.tickers import validate_ticker
from investor_research.vault.paths import (
    create_company_folder,
    get_company_path,
    get_latest_xlsx_export,
)
from investor_research.vault.xlsx_parser import parse_workbook, sha256_of

logger = logging.getLogger(__name__)


FINANCIALS_FILE = "financials.json"
VALUATION_FILE = "valuation.json"
METADATA_FILE = "metadata.json"


def _parsed_dir(settings: Settings, ticker: str) -> Path:
    return get_company_path(settings, ticker) / "parsed"


def _exports_dir(settings: Settings, ticker: str) -> Path:
    return get_company_path(settings, ticker) / "bloomberg_exports"


def write_parsed_json(settings: Settings, snapshot: FundamentalsSnapshot) -> dict[str, Path]:
    """Persist the three parsed JSON files for ``snapshot.ticker``.

    Returns ``{"financials": path, "valuation": path, "metadata": path}``.
    """
    ticker = validate_ticker(snapshot.ticker)
    parsed_dir = _parsed_dir(settings, ticker)
    parsed_dir.mkdir(parents=True, exist_ok=True)

    parsed_at = snapshot.parsed_at.isoformat()

    financials_doc: dict[str, Any] = {
        "ticker": ticker,
        "currency": snapshot.currency,
        "units": snapshot.units,
        "fiscal_year_end": snapshot.fiscal_year_end,
        "periods": snapshot.periods,
        "series": snapshot.series,
        "ratios": snapshot.ratios,
        "source_file": snapshot.source_file,
        "source_sha256": snapshot.source_sha256,
        "parsed_at": parsed_at,
    }
    valuation_doc: dict[str, Any] = {
        "ticker": ticker,
        "periods": snapshot.periods,
        "history": snapshot.valuation_history,
        "dcf": snapshot.dcf,
        "source_file": snapshot.source_file,
        "source_sha256": snapshot.source_sha256,
        "parsed_at": parsed_at,
    }
    metadata_doc: dict[str, Any] = {
        "ticker": ticker,
        "company_name": snapshot.company_name,
        **snapshot.metadata,
        "source_file": snapshot.source_file,
        "source_sha256": snapshot.source_sha256,
        "parsed_at": parsed_at,
        "warnings": snapshot.warnings,
    }

    paths = {
        "financials": parsed_dir / FINANCIALS_FILE,
        "valuation": parsed_dir / VALUATION_FILE,
        "metadata": parsed_dir / METADATA_FILE,
    }
    for key, path in paths.items():
        doc = {
            "financials": financials_doc,
            "valuation": valuation_doc,
            "metadata": metadata_doc,
        }[key]
        path.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    logger.info(
        "vault.parsed_written",
        extra={"ticker": ticker, "dir": str(parsed_dir)},
    )
    return paths


def load_parsed_json(settings: Settings, ticker: str) -> FundamentalsSnapshot | None:
    """Reconstitute a ``FundamentalsSnapshot`` from the per-ticker JSON files.

    Returns ``None`` when the company has no ``parsed/financials.json`` yet.
    """
    ticker = validate_ticker(ticker)
    parsed_dir = _parsed_dir(settings, ticker)
    fin_path = parsed_dir / FINANCIALS_FILE
    if not fin_path.is_file():
        return None
    financials = json.loads(fin_path.read_text(encoding="utf-8"))

    val_path = parsed_dir / VALUATION_FILE
    valuation = (
        json.loads(val_path.read_text(encoding="utf-8")) if val_path.is_file() else {}
    )
    meta_path = parsed_dir / METADATA_FILE
    metadata = (
        json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    )

    metadata_payload = {
        k: v
        for k, v in metadata.items()
        if k
        not in {
            "ticker",
            "company_name",
            "source_file",
            "source_sha256",
            "parsed_at",
            "warnings",
        }
    }

    parsed_at_raw = financials.get("parsed_at")
    try:
        parsed_at = (
            datetime.fromisoformat(parsed_at_raw)
            if isinstance(parsed_at_raw, str)
            else datetime.now(timezone.utc)
        )
    except ValueError:
        parsed_at = datetime.now(timezone.utc)

    return FundamentalsSnapshot(
        ticker=ticker,
        company_name=metadata.get("company_name"),
        currency=financials.get("currency"),
        units=financials.get("units"),
        fiscal_year_end=financials.get("fiscal_year_end"),
        periods=list(financials.get("periods") or []),
        series=dict(financials.get("series") or {}),
        ratios=dict(financials.get("ratios") or {}),
        valuation_history=dict(valuation.get("history") or {}),
        dcf=dict(valuation.get("dcf") or {}),
        metadata=metadata_payload,
        source_file=financials.get("source_file"),
        source_sha256=financials.get("source_sha256"),
        parsed_at=parsed_at,
        warnings=list(metadata.get("warnings") or []),
    )


def import_xlsx(
    settings: Settings,
    ticker: str,
    file_path: Path | str,
    *,
    copy_to_vault: bool = True,
) -> dict[str, Any]:
    """Import an xlsx export end-to-end: copy → hash dedupe → parse → write JSON.

    Steps:
      1. Ensure the company folder exists (idempotent).
      2. SHA-256 the source file.
      3. If ``copy_to_vault=True``, copy into ``bloomberg_exports/`` under a
         dated filename. When the same hash is already present, skip the copy
         and return the existing path.
      4. Parse the (vault-side) xlsx, write the three parsed JSON files.

    Returns a result dict with ``{ticker, xlsx_path, source_sha256,
    deduped, parsed_paths, summary}``.
    """
    ticker = validate_ticker(ticker)
    create_company_folder(settings, ticker)

    src = Path(file_path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"xlsx source not found: {src}")
    src_hash = sha256_of(src)

    exports = _exports_dir(settings, ticker)
    exports.mkdir(parents=True, exist_ok=True)

    deduped = False
    target: Path
    if copy_to_vault:
        existing = _find_export_by_hash(exports, src_hash)
        if existing is not None:
            target = existing
            deduped = True
        else:
            date_str = datetime.now(timezone.utc).date().isoformat()
            base = src.stem
            target = exports / f"{date_str}_{base}.xlsx"
            counter = 1
            while target.exists():
                target = exports / f"{date_str}_{base}_{counter}.xlsx"
                counter += 1
            shutil.copy2(src, target)
    else:
        target = src

    snapshot = parse_workbook(target)
    if snapshot.ticker != ticker:
        snapshot = snapshot.model_copy(update={"ticker": ticker})
    parsed_paths = write_parsed_json(settings, snapshot)

    summary = {
        "periods": len(snapshot.periods),
        "first_period": snapshot.periods[0] if snapshot.periods else None,
        "last_period": snapshot.periods[-1] if snapshot.periods else None,
        "series_keys": list(snapshot.series.keys()),
        "ratio_keys": list(snapshot.ratios.keys()),
        "valuation_keys": list(snapshot.valuation_history.keys()),
        "dcf_keys": list(snapshot.dcf.keys()),
        "warnings": snapshot.warnings,
    }
    return {
        "ticker": ticker,
        "xlsx_path": str(target),
        "source_sha256": src_hash,
        "deduped": deduped,
        "parsed_paths": {k: str(v) for k, v in parsed_paths.items()},
        "summary": summary,
    }


def refresh_from_latest_xlsx(settings: Settings, ticker: str) -> dict[str, Any]:
    """Re-parse the most recently modified xlsx in the vault for ``ticker``.

    Use this after manually dropping a new export into ``bloomberg_exports/``
    without going through ``import_xlsx``.
    """
    ticker = validate_ticker(ticker)
    latest = get_latest_xlsx_export(settings, ticker)
    if latest is None:
        raise FileNotFoundError(
            f"no xlsx exports found for {ticker} under "
            f"{_exports_dir(settings, ticker)}"
        )
    return import_xlsx(settings, ticker, latest, copy_to_vault=False)


def _find_export_by_hash(exports_dir: Path, sha256: str) -> Path | None:
    for path in exports_dir.glob("*.xlsx"):
        if sha256_of(path) == sha256:
            return path
    return None
