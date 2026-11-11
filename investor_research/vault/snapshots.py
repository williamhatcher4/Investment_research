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
from investor_research.models import FundamentalsSnapshot, WatchlistSnapshot
from investor_research.tickers import validate_ticker
from investor_research.vault.paths import (
    create_company_folder,
    get_company_path,
    get_latest_xlsx_export,
    get_vault_root,
)
from investor_research.vault.ticker_aliases import (
    auto_seed_aliases,
    resolve as resolve_alias,
    unresolved as unresolved_aliases,
)
from investor_research.vault.watchlist_parser import parse_watchlist_workbook
from investor_research.vault.xlsx_parser import parse_workbook, sha256_of

logger = logging.getLogger(__name__)


FINANCIALS_FILE = "financials.json"
VALUATION_FILE = "valuation.json"
METADATA_FILE = "metadata.json"
WATCHLIST_FILE = "watchlist.json"

# Layout under {VAULT_ROOT}/Watchlists/ for the multi-company watchlist scorecard.
WATCHLIST_INDEX_FILENAME = "watchlist_scorecard_latest.json"
WATCHLIST_HISTORY_DIRNAME = "history"
WATCHLIST_RAW_EXPORTS_DIRNAME = "raw_exports"
WATCHLIST_PEER_SET_NAME = "watchlist_coverage"


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


# ---- Watchlist (multi-company scorecard) snapshots ---------------------


def _watchlists_dir(settings: Settings) -> Path:
    return get_vault_root(settings) / "Watchlists"


def import_watchlist_xlsx(
    settings: Settings,
    file_path: Path | str,
    *,
    copy_to_vault: bool = True,
) -> dict[str, Any]:
    """Import a multi-company watchlist scorecard workbook end-to-end.

    Steps:
      1. SHA-256 the source and copy it under ``Watchlists/raw_exports/`` (hash-deduped).
      2. Parse the workbook to a ``WatchlistSnapshot``.
      3. Auto-seed US-suffix Bloomberg tickers in ``Watchlists/ticker_aliases.yml``
         and resolve every row's ``normalized_ticker`` via the alias map.
      4. Write the JSON snapshot to ``Watchlists/watchlist_scorecard_latest.json``
         plus a dated audit copy under ``Watchlists/history/``.
      5. For each resolved ticker, ensure the company folder exists and
         write ``Companies/{TICKER}/parsed/watchlist.json``.
      6. Update the peer set ``watchlist_coverage`` with the resolved tickers.
    """
    src = Path(file_path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"watchlist xlsx not found: {src}")
    src_hash = sha256_of(src)

    raw_exports = _watchlists_dir(settings) / WATCHLIST_RAW_EXPORTS_DIRNAME
    raw_exports.mkdir(parents=True, exist_ok=True)

    deduped = False
    if copy_to_vault:
        existing = _find_export_by_hash(raw_exports, src_hash)
        if existing is not None:
            target = existing
            deduped = True
        else:
            date_str = datetime.now(timezone.utc).date().isoformat()
            target = raw_exports / f"{date_str}_{src.stem}.xlsx"
            counter = 1
            while target.exists():
                target = raw_exports / f"{date_str}_{src.stem}_{counter}.xlsx"
                counter += 1
            shutil.copy2(src, target)
    else:
        target = src

    snapshot = parse_watchlist_workbook(target)

    # Resolve tickers via the alias YAML (auto-seeding US-suffix rows first).
    bb_tickers = [row.bloomberg_ticker for row in snapshot.rows]
    aliases, added = auto_seed_aliases(settings, bb_tickers)
    for row in snapshot.rows:
        resolved = resolve_alias(aliases, row.bloomberg_ticker)
        if resolved is None:
            row.warnings.append(
                f"no alias for Bloomberg ticker {row.bloomberg_ticker!r}; "
                "add it to Watchlists/ticker_aliases.yml"
            )
        row.normalized_ticker = resolved

    index_path, dated_path = _write_watchlist_snapshot(settings, snapshot)
    fanned_out = _fan_out_per_company(settings, snapshot)
    removed_stale = _remove_stale_watchlist_files(
        settings, {r.normalized_ticker for r in snapshot.rows if r.normalized_ticker}
    )
    peer_set_written = _update_watchlist_peer_set(settings, snapshot)

    rows_resolved = sum(1 for r in snapshot.rows if r.normalized_ticker)
    unresolved_tickers = unresolved_aliases(aliases, bb_tickers)

    summary = {
        "rows_total": len(snapshot.rows),
        "rows_resolved": rows_resolved,
        "rows_unresolved": len(unresolved_tickers),
        "metrics_per_row": len(snapshot.rows[0].metrics) if snapshot.rows else 0,
        "aliases_seeded": added,
        "snapshot_date": snapshot.snapshot_date,
        "sheet_name": snapshot.sheet_name,
    }
    return {
        "xlsx_path": str(target),
        "source_sha256": src_hash,
        "deduped": deduped,
        "index_path": str(index_path),
        "dated_path": str(dated_path),
        "fanned_out_paths": [str(p) for p in fanned_out],
        "removed_stale_paths": [str(p) for p in removed_stale],
        "peer_set": peer_set_written,
        "rows_total": len(snapshot.rows),
        "rows_resolved": rows_resolved,
        "rows_unresolved": len(unresolved_tickers),
        "unresolved_tickers": unresolved_tickers,
        "summary": summary,
    }


def load_watchlist_snapshot(settings: Settings) -> WatchlistSnapshot | None:
    """Load ``Watchlists/watchlist_scorecard_latest.json`` if it exists."""
    path = _watchlists_dir(settings) / WATCHLIST_INDEX_FILENAME
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return WatchlistSnapshot.model_validate(payload)


def load_company_watchlist(settings: Settings, ticker: str) -> dict[str, Any] | None:
    """Load ``Companies/{TICKER}/parsed/watchlist.json`` if it exists."""
    ticker = validate_ticker(ticker)
    path = _parsed_dir(settings, ticker) / WATCHLIST_FILE
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_watchlist_snapshot(
    settings: Settings, snapshot: WatchlistSnapshot
) -> tuple[Path, Path]:
    """Write the canonical + dated copies of the watchlist snapshot JSON."""
    base = _watchlists_dir(settings)
    history = base / WATCHLIST_HISTORY_DIRNAME
    base.mkdir(parents=True, exist_ok=True)
    history.mkdir(parents=True, exist_ok=True)

    index_path = base / WATCHLIST_INDEX_FILENAME
    date_token = snapshot.snapshot_date or datetime.now(timezone.utc).date().isoformat()
    dated_path = history / f"watchlist_scorecard_{date_token}.json"

    payload = snapshot.model_dump(mode="json")
    text = json.dumps(payload, indent=2, default=str)
    index_path.write_text(text, encoding="utf-8")
    dated_path.write_text(text, encoding="utf-8")
    logger.info(
        "vault.watchlist_written",
        extra={
            "index_path": str(index_path),
            "dated_path": str(dated_path),
            "rows": len(snapshot.rows),
        },
    )
    return index_path, dated_path


def _fan_out_per_company(
    settings: Settings, snapshot: WatchlistSnapshot
) -> list[Path]:
    """Write each resolved row to its company's ``parsed/watchlist.json``.

    Returns the list of files written. Unresolved rows are skipped.
    """
    written: list[Path] = []
    parsed_at_iso = snapshot.parsed_at.isoformat()
    for row in snapshot.rows:
        ticker = row.normalized_ticker
        if not ticker:
            continue
        create_company_folder(
            settings, ticker, company_name=row.long_name, sector=row.sector
        )
        parsed_dir = _parsed_dir(settings, ticker)
        parsed_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            **row.model_dump(mode="json"),
            "source_file": snapshot.source_file,
            "source_sha256": snapshot.source_sha256,
            "source_workbook_date": snapshot.snapshot_date,
            "parsed_at": parsed_at_iso,
        }
        target = parsed_dir / WATCHLIST_FILE
        target.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        written.append(target)
    return written


def _remove_stale_watchlist_files(settings: Settings, keep: set[str]) -> list[Path]:
    """Delete ``parsed/watchlist.json`` for companies no longer on the watchlist.

    Otherwise a company dropped from the workbook keeps serving its old row
    to ``build_dcf`` and ``load_company_watchlist``.
    """
    companies_dir = get_vault_root(settings) / "Companies"
    if not companies_dir.is_dir():
        return []
    removed: list[Path] = []
    for path in sorted(companies_dir.glob(f"*/parsed/{WATCHLIST_FILE}")):
        if path.parent.parent.name not in keep:
            path.unlink()
            removed.append(path)
    return removed


def _update_watchlist_peer_set(
    settings: Settings, snapshot: WatchlistSnapshot
) -> dict[str, Any] | None:
    """Write/update the ``watchlist_coverage`` peer set with resolved tickers.

    Uses the same on-disk format as ``investor_research.tools.vault_tools._write_peer_sets``
    so existing peer-set tools see this entry.
    """
    tickers = [r.normalized_ticker for r in snapshot.rows if r.normalized_ticker]
    if not tickers:
        return None

    # Local import keeps this module independent of the tool layer at module-load time.
    from investor_research.tools.vault_tools import _read_peer_sets, _write_peer_sets

    data = _read_peer_sets(settings, strict=True)
    description = (
        f"Coverage list parsed from "
        f"{Path(snapshot.source_file).name if snapshot.source_file else 'watchlist xlsx'}"
        f" ({snapshot.snapshot_date or 'undated'})"
    )
    entry = {"tickers": tickers, "description": description}
    data[WATCHLIST_PEER_SET_NAME] = entry
    _write_peer_sets(settings, data)
    return {"name": WATCHLIST_PEER_SET_NAME, **entry}
