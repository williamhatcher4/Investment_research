"""SQLite index over the vault's parsed JSON.

The SQLite layer is NOT a source of truth. It is a derived projection
over ``Companies/{TICKER}/parsed/*.json`` rebuilt by ``reindex_vault()``
(invoked via ``python -m investor_research.vault reindex``).

Use cases for the index:

- Cross-company screens (e.g. "show ROIC > 15% over 10yr").
- Peer comparisons that aggregate metrics across the vault.
- Watchlist + memo metadata lookups without scanning the filesystem.

If ``settings.enable_sqlite`` is ``False``, every public function in this
module either no-ops (writes) or returns empty results (reads). The vault
remains fully functional without it.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from investor_research.config import Settings
from investor_research.tickers import validate_ticker
from investor_research.vault.notes import (
    list_company_memos,
    read_company_frontmatter,
)
from investor_research.vault.paths import (
    VaultNotConfiguredError,
    get_company_path,
    get_vault_root,
    list_companies,
    vault_enabled,
)
from investor_research.vault.snapshots import (
    FINANCIALS_FILE,
    METADATA_FILE,
    VALUATION_FILE,
)

logger = logging.getLogger(__name__)


DB_RELATIVE_PATH = Path("database") / "investor_research.sqlite"


SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
  ticker          TEXT PRIMARY KEY,
  company_name    TEXT,
  sector          TEXT,
  industry        TEXT,
  exchange        TEXT,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_files (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker          TEXT NOT NULL REFERENCES companies(ticker) ON DELETE CASCADE,
  file_path       TEXT NOT NULL,
  file_type       TEXT NOT NULL,
  sha256          TEXT NOT NULL UNIQUE,
  imported_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metrics (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker          TEXT NOT NULL REFERENCES companies(ticker) ON DELETE CASCADE,
  metric_name     TEXT NOT NULL,
  period          TEXT NOT NULL,
  period_type     TEXT NOT NULL,
  value           REAL,
  unit            TEXT,
  source_file_id  INTEGER REFERENCES source_files(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_metrics_lookup ON metrics(ticker, metric_name, period);

CREATE TABLE IF NOT EXISTS memos (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker          TEXT NOT NULL REFERENCES companies(ticker) ON DELETE CASCADE,
  memo_type       TEXT NOT NULL,
  title           TEXT,
  file_path       TEXT NOT NULL UNIQUE,
  created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker          TEXT NOT NULL REFERENCES companies(ticker) ON DELETE CASCADE,
  title           TEXT,
  file_path       TEXT NOT NULL UNIQUE,
  created_at      TEXT NOT NULL
);
"""


def db_path(settings: Settings) -> Path:
    """Absolute path to the SQLite file under the configured vault root."""
    return get_vault_root(settings) / DB_RELATIVE_PATH


def connect(settings: Settings) -> sqlite3.Connection:
    """Open (and lazily create) the SQLite index. Foreign keys ON.

    Raises ``VaultNotConfiguredError`` when ``VAULT_ROOT`` is unset, or
    ``RuntimeError`` when SQLite is disabled.
    """
    if not vault_enabled(settings):
        raise VaultNotConfiguredError("VAULT_ROOT is not set; cannot open SQLite index")
    if not settings.enable_sqlite:
        raise RuntimeError(
            "SQLite index is disabled (ENABLE_SQLITE=false). "
            "Enable it in .env to use cross-company queries."
        )
    path = db_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def reindex_vault(settings: Settings) -> dict[str, int]:
    """Rebuild the SQLite index from the parsed JSON + filesystem scan.

    Truncates and re-populates ``companies``, ``source_files``, ``metrics``,
    ``memos``, ``notes``. Returns a counts dict.
    """
    if not settings.enable_sqlite:
        logger.info("vault.reindex.skipped_disabled")
        return {"companies": 0, "metrics": 0, "source_files": 0, "memos": 0, "notes": 0}

    conn = connect(settings)
    try:
        with conn:
            for table in ("metrics", "source_files", "memos", "notes", "companies"):
                conn.execute(f"DELETE FROM {table}")
            counts = _populate(conn, settings)
        logger.info("vault.reindex.complete", extra=counts)
        return counts
    finally:
        conn.close()


# ---- Read-side helpers --------------------------------------------------


def query_metric(
    settings: Settings,
    metric_name: str,
    *,
    tickers: list[str] | None = None,
    period: str | None = None,
    min_value: float | None = None,
    max_value: float | None = None,
) -> list[dict[str, Any]]:
    """Filtered metric query across companies.

    Returns rows ``{ticker, metric_name, period, period_type, value, unit}``.
    Empty list when SQLite is disabled or no rows match.
    """
    if not settings.enable_sqlite or not vault_enabled(settings):
        return []
    if not db_path(settings).exists():
        return []
    conn = connect(settings)
    try:
        clauses = ["metric_name = ?"]
        params: list[Any] = [metric_name]
        if tickers:
            placeholders = ",".join("?" for _ in tickers)
            clauses.append(f"ticker IN ({placeholders})")
            params.extend(validate_ticker(t) for t in tickers)
        if period is not None:
            clauses.append("period = ?")
            params.append(period)
        if min_value is not None:
            clauses.append("value >= ?")
            params.append(min_value)
        if max_value is not None:
            clauses.append("value <= ?")
            params.append(max_value)
        sql = (
            "SELECT ticker, metric_name, period, period_type, value, unit "
            "FROM metrics WHERE " + " AND ".join(clauses) + " ORDER BY ticker, period"
        )
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def list_indexed_companies(settings: Settings) -> list[dict[str, Any]]:
    """Return the rows in the ``companies`` table (empty if no index yet)."""
    if not settings.enable_sqlite or not vault_enabled(settings):
        return []
    if not db_path(settings).exists():
        return []
    conn = connect(settings)
    try:
        rows = conn.execute(
            "SELECT ticker, company_name, sector, industry, exchange, updated_at "
            "FROM companies ORDER BY ticker"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# ---- internals ----------------------------------------------------------


def _populate(conn: sqlite3.Connection, settings: Settings) -> dict[str, int]:
    counts = {"companies": 0, "metrics": 0, "source_files": 0, "memos": 0, "notes": 0}
    now = datetime.now(timezone.utc).isoformat()

    for entry in list_companies(settings):
        ticker = entry["ticker"]
        frontmatter = read_company_frontmatter(settings, ticker)
        meta = _read_metadata_json(settings, ticker)
        company_name = (
            entry.get("company_name")
            or frontmatter.get("company_name")
            or meta.get("company_name")
        )
        sector = frontmatter.get("sector") or meta.get("sector")
        industry = frontmatter.get("industry") or meta.get("industry")
        exchange = frontmatter.get("exchange") or meta.get("exchange")

        conn.execute(
            "INSERT OR REPLACE INTO companies "
            "(ticker, company_name, sector, industry, exchange, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ticker, company_name, sector, industry, exchange, now, now),
        )
        counts["companies"] += 1

        source_file_id = _insert_source_file(conn, settings, ticker, meta, now)
        counts["source_files"] += 1 if source_file_id is not None else 0

        counts["metrics"] += _insert_metrics(conn, settings, ticker, source_file_id)
        counts["memos"] += _insert_memos(conn, settings, ticker)
        counts["notes"] += _insert_notes(conn, settings, ticker)

    return counts


def _read_financials_json(settings: Settings, ticker: str) -> dict[str, Any]:
    return _read_parsed_json(settings, ticker, FINANCIALS_FILE)


def _read_valuation_json(settings: Settings, ticker: str) -> dict[str, Any]:
    return _read_parsed_json(settings, ticker, VALUATION_FILE)


def _read_metadata_json(settings: Settings, ticker: str) -> dict[str, Any]:
    return _read_parsed_json(settings, ticker, METADATA_FILE)


def _read_parsed_json(settings: Settings, ticker: str, name: str) -> dict[str, Any]:
    path = get_company_path(settings, ticker) / "parsed" / name
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "vault.parsed_json.read_failed",
            extra={"ticker": ticker, "file": name, "err": str(exc)[:200]},
        )
        return {}


def _insert_source_file(
    conn: sqlite3.Connection,
    settings: Settings,
    ticker: str,
    meta: dict[str, Any],
    now: str,
) -> int | None:
    sha = meta.get("source_sha256")
    source_path = meta.get("source_file")
    if not sha or not source_path:
        return None
    imported_at = meta.get("parsed_at") or now
    try:
        cursor = conn.execute(
            "INSERT INTO source_files (ticker, file_path, file_type, sha256, imported_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (ticker, source_path, "xlsx_bloomberg_factset", sha, imported_at),
        )
        return int(cursor.lastrowid) if cursor.lastrowid is not None else None
    except sqlite3.IntegrityError:
        # Same hash already present (e.g. shared across tickers, unlikely).
        row = conn.execute(
            "SELECT id FROM source_files WHERE sha256 = ?", (sha,)
        ).fetchone()
        return int(row["id"]) if row else None


def _insert_metrics(
    conn: sqlite3.Connection,
    settings: Settings,
    ticker: str,
    source_file_id: int | None,
) -> int:
    financials = _read_financials_json(settings, ticker)
    valuation = _read_valuation_json(settings, ticker)
    if not financials and not valuation:
        return 0

    periods = financials.get("periods") or valuation.get("periods") or []
    rows: list[tuple[Any, ...]] = []

    for metric_name, values in (financials.get("series") or {}).items():
        rows.extend(
            _series_rows(ticker, metric_name, "currency", periods, values, source_file_id)
        )
    for metric_name, values in (financials.get("ratios") or {}).items():
        rows.extend(
            _series_rows(ticker, metric_name, "ratio", periods, values, source_file_id)
        )
    for metric_name, values in (valuation.get("history") or {}).items():
        rows.extend(
            _series_rows(ticker, metric_name, "ratio", periods, values, source_file_id)
        )
    for metric_name, value in (valuation.get("dcf") or {}).items():
        if value is None:
            continue
        rows.append(
            (
                ticker,
                f"dcf.{metric_name}",
                "point_in_time",
                "point_in_time",
                float(value),
                "scalar",
                source_file_id,
            )
        )

    if not rows:
        return 0
    conn.executemany(
        "INSERT INTO metrics "
        "(ticker, metric_name, period, period_type, value, unit, source_file_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def _series_rows(
    ticker: str,
    metric_name: str,
    unit: str,
    periods: list[str],
    values: list[float | None],
    source_file_id: int | None,
) -> list[tuple[Any, ...]]:
    out: list[tuple[Any, ...]] = []
    for period, value in zip(periods, values):
        if value is None:
            continue
        out.append(
            (
                ticker,
                metric_name,
                period,
                "annual",
                float(value),
                unit,
                source_file_id,
            )
        )
    return out


def _insert_memos(
    conn: sqlite3.Connection, settings: Settings, ticker: str
) -> int:
    rows = list_company_memos(settings, ticker)
    if not rows:
        return 0
    conn.executemany(
        "INSERT OR REPLACE INTO memos (ticker, memo_type, title, file_path, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (
                ticker,
                row.get("memo_type") or "memo",
                row.get("title"),
                row["path"],
                row.get("date") or datetime.now(timezone.utc).isoformat(),
            )
            for row in rows
        ],
    )
    return len(rows)


def _insert_notes(
    conn: sqlite3.Connection, settings: Settings, ticker: str
) -> int:
    notes_dir = get_company_path(settings, ticker) / "notes"
    if not notes_dir.is_dir():
        return 0
    rows = []
    for path in notes_dir.glob("*.md"):
        created_at = datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc
        ).isoformat()
        rows.append((ticker, path.stem, str(path), created_at))
    if not rows:
        return 0
    conn.executemany(
        "INSERT OR REPLACE INTO notes (ticker, title, file_path, created_at) "
        "VALUES (?, ?, ?, ?)",
        rows,
    )
    return len(rows)
