"""MCP-facing functions that operate on the local research vault.

These wrap the lower-level ``investor_research.vault`` modules with the
shape expected by FastMCP tool handlers: input validation, return-dict
envelopes (JSON-serializable), and a consistent ``VAULT_NOT_CONFIGURED``
error response when ``VAULT_ROOT`` is unset.

Implements seven tools (see plan, "Tool surface"):

- ``list_vault_companies``
- ``create_vault_company``
- ``import_xlsx_export``
- ``load_company_data``
- ``write_company_note_tool``
- ``write_memo_to_vault_tool``
- ``list_company_memos_tool``
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from investor_research.config import Settings, get_settings
from investor_research.tickers import validate_ticker
from investor_research.vault.database import query_metric, reindex_vault
from investor_research.vault.notes import (
    list_company_memos,
    parse_frontmatter,
    read_company_frontmatter,
    read_company_note,
    write_company_note,
    write_memo_to_vault,
)
from investor_research.vault.paths import (
    VaultNotConfiguredError,
    create_company_folder,
    get_vault_root,
    list_companies,
    vault_enabled,
)
from investor_research.vault.snapshots import (
    WATCHLIST_PEER_SET_NAME,
    import_watchlist_xlsx,
    import_xlsx,
    load_company_watchlist,
    load_parsed_json,
    load_watchlist_snapshot,
)

logger = logging.getLogger(__name__)


_VAULT_NOT_CONFIGURED = {
    "ok": False,
    "error": "VAULT_NOT_CONFIGURED",
    "message": (
        "VAULT_ROOT is not set. Configure it in .env (e.g. "
        "VAULT_ROOT=./data/Investor\\ Research\\ Vault) and run "
        "`python -m investor_research.vault init` before using vault tools."
    ),
}


def _require_vault(settings: Settings) -> dict[str, Any] | None:
    if vault_enabled(settings):
        return None
    return dict(_VAULT_NOT_CONFIGURED)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- Tool implementations -----------------------------------------------


def list_vault_companies() -> dict[str, Any]:
    """List companies present in the vault, with frontmatter metadata."""
    settings = get_settings()
    missing = _require_vault(settings)
    if missing:
        return missing
    entries = list_companies(settings)
    return {
        "ok": True,
        "as_of": _now(),
        "vault_root": str(settings.vault_root),
        "count": len(entries),
        "companies": entries,
    }


def create_vault_company(
    ticker: str,
    company_name: str | None = None,
    sector: str | None = None,
) -> dict[str, Any]:
    """Create the folder tree + stub home note for ``ticker``. Idempotent."""
    settings = get_settings()
    missing = _require_vault(settings)
    if missing:
        return missing
    ticker = validate_ticker(ticker)
    folder = create_company_folder(
        settings, ticker, company_name=company_name, sector=sector
    )
    return {
        "ok": True,
        "as_of": _now(),
        "ticker": ticker,
        "path": str(folder),
        "frontmatter": read_company_frontmatter(settings, ticker),
    }


def import_xlsx_export(
    ticker: str,
    file_path: str,
    *,
    reindex: bool = True,
) -> dict[str, Any]:
    """Copy + parse + persist an xlsx export. Hash-dedupes on re-imports."""
    settings = get_settings()
    missing = _require_vault(settings)
    if missing:
        return missing
    ticker = validate_ticker(ticker)
    try:
        result = import_xlsx(settings, ticker, file_path)
    except FileNotFoundError as exc:
        return {"ok": False, "error": "FILE_NOT_FOUND", "message": str(exc)}
    if reindex and settings.enable_sqlite:
        try:
            reindex_vault(settings)
        except Exception as exc:  # noqa: BLE001 - never block the import on index failure
            logger.warning(
                "vault.tool.reindex_failed",
                extra={"ticker": ticker, "err": str(exc)[:200]},
            )
    return {"ok": True, "as_of": _now(), **result}


def import_watchlist_xlsx_export(
    file_path: str,
    *,
    reindex: bool = True,
) -> dict[str, Any]:
    """Import a Hatcher-style multi-company watchlist scorecard xlsx.

    Fans out per-company watchlist data, writes a master index, auto-seeds
    US-suffix Bloomberg tickers, and updates the ``hatcher_coverage`` peer set.
    Unresolved (non-US) Bloomberg tickers are surfaced in ``unresolved_tickers``
    so the user can add them to ``Watchlists/ticker_aliases.yml``.
    """
    settings = get_settings()
    missing = _require_vault(settings)
    if missing:
        return missing
    try:
        result = import_watchlist_xlsx(settings, file_path)
    except FileNotFoundError as exc:
        return {"ok": False, "error": "FILE_NOT_FOUND", "message": str(exc)}
    if reindex and settings.enable_sqlite:
        try:
            reindex_vault(settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "vault.tool.reindex_failed",
                extra={"err": str(exc)[:200]},
            )
    return {"ok": True, "as_of": _now(), **result}


def load_watchlist_snapshot_tool() -> dict[str, Any]:
    """Return the most recently imported watchlist snapshot, if any."""
    settings = get_settings()
    missing = _require_vault(settings)
    if missing:
        return missing
    snapshot = load_watchlist_snapshot(settings)
    if snapshot is None:
        return {
            "ok": True,
            "as_of": _now(),
            "has_snapshot": False,
            "message": (
                "No watchlist snapshot on file yet. Run `import_watchlist_xlsx_export` "
                "against a Hatcher-style scorecard xlsx."
            ),
        }
    return {
        "ok": True,
        "as_of": _now(),
        "has_snapshot": True,
        "snapshot": snapshot.model_dump(mode="json"),
        "peer_set": WATCHLIST_PEER_SET_NAME,
    }


def load_company_watchlist_tool(ticker: str) -> dict[str, Any]:
    """Return the per-company watchlist row written by the last scorecard import."""
    settings = get_settings()
    missing = _require_vault(settings)
    if missing:
        return missing
    ticker = validate_ticker(ticker)
    payload = load_company_watchlist(settings, ticker)
    if payload is None:
        return {
            "ok": True,
            "as_of": _now(),
            "ticker": ticker,
            "has_watchlist_data": False,
            "message": (
                f"No watchlist row on file for {ticker}. Either run "
                "`import_watchlist_xlsx_export` or add a manual alias if this is "
                "a non-US ticker."
            ),
        }
    return {
        "ok": True,
        "as_of": _now(),
        "ticker": ticker,
        "has_watchlist_data": True,
        "row": payload,
    }


def load_company_data(ticker: str) -> dict[str, Any]:
    """Return parsed fundamentals + frontmatter for ``ticker``."""
    settings = get_settings()
    missing = _require_vault(settings)
    if missing:
        return missing
    ticker = validate_ticker(ticker)
    snapshot = load_parsed_json(settings, ticker)
    frontmatter = read_company_frontmatter(settings, ticker)
    note = read_company_note(settings, ticker)
    memos = list_company_memos(settings, ticker)

    if snapshot is None:
        return {
            "ok": True,
            "as_of": _now(),
            "ticker": ticker,
            "has_parsed_data": False,
            "frontmatter": frontmatter,
            "note_present": note is not None,
            "memos": memos,
            "message": (
                "No parsed fundamentals on file yet. Run `import_xlsx_export` "
                "with the path to a Bloomberg/FactSet xlsx export."
            ),
        }

    return {
        "ok": True,
        "as_of": _now(),
        "ticker": ticker,
        "has_parsed_data": True,
        "frontmatter": frontmatter,
        "note_present": note is not None,
        "memos": memos,
        "fundamentals": snapshot.model_dump(mode="json"),
        "prompt_block": snapshot.to_prompt_block(recent_years=5),
    }


def write_company_note_tool(
    ticker: str,
    content_markdown: str,
    frontmatter: dict[str, Any] | None = None,
    *,
    merge_frontmatter: bool = True,
) -> dict[str, Any]:
    """Write the body of ``{TICKER}.md``. Existing frontmatter is preserved by default."""
    settings = get_settings()
    missing = _require_vault(settings)
    if missing:
        return missing
    ticker = validate_ticker(ticker)
    create_company_folder(settings, ticker)
    path = write_company_note(
        settings,
        ticker,
        content_markdown,
        frontmatter=frontmatter,
        merge_frontmatter=merge_frontmatter,
    )
    return {
        "ok": True,
        "as_of": _now(),
        "ticker": ticker,
        "path": str(path),
        "frontmatter": read_company_frontmatter(settings, ticker),
    }


def write_memo_to_vault_tool(
    ticker: str,
    memo_markdown: str,
    memo_type: str = "investment_memo",
    *,
    title: str | None = None,
    date: str | None = None,
) -> dict[str, Any]:
    """Save a memo under ``Companies/{TICKER}/memos/`` and return its path."""
    settings = get_settings()
    missing = _require_vault(settings)
    if missing:
        return missing
    ticker = validate_ticker(ticker)
    create_company_folder(settings, ticker)
    path = write_memo_to_vault(
        settings,
        ticker,
        memo_markdown,
        memo_type=memo_type,
        title=title,
        date=date,
    )
    if settings.enable_sqlite:
        try:
            reindex_vault(settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "vault.tool.reindex_failed",
                extra={"ticker": ticker, "err": str(exc)[:200]},
            )
    return {
        "ok": True,
        "as_of": _now(),
        "ticker": ticker,
        "memo_type": memo_type,
        "path": str(path),
    }


def list_company_memos_tool(ticker: str) -> dict[str, Any]:
    """List a company's saved memos, newest first."""
    settings = get_settings()
    missing = _require_vault(settings)
    if missing:
        return missing
    ticker = validate_ticker(ticker)
    memos = list_company_memos(settings, ticker)
    return {
        "ok": True,
        "as_of": _now(),
        "ticker": ticker,
        "count": len(memos),
        "memos": memos,
    }


# ---- Cross-company vault tools -----------------------------------------


def screen_vault_companies(
    metric_name: str,
    *,
    min_value: float | None = None,
    max_value: float | None = None,
    period: str | None = None,
    tickers: list[str] | None = None,
) -> dict[str, Any]:
    """Screen indexed vault companies by a metric threshold."""
    settings = get_settings()
    missing = _require_vault(settings)
    if missing:
        return missing
    if not settings.enable_sqlite:
        return {"ok": False, "error": "SQLITE_DISABLED", "message": "ENABLE_SQLITE=false."}
    validated = [validate_ticker(t) for t in tickers] if tickers else None
    if period is None:
        # Screen on each company's latest value: pick the latest period first,
        # then apply the thresholds (filtering first would let an old period
        # that passes stand in for a current one that fails).
        rows = _latest_metric_rows(
            query_metric(settings, metric_name, tickers=validated)
        )
        rows = [
            row
            for row in rows
            if row.get("value") is not None
            and (min_value is None or row["value"] >= min_value)
            and (max_value is None or row["value"] <= max_value)
        ]
    else:
        rows = query_metric(
            settings,
            metric_name,
            tickers=validated,
            period=period,
            min_value=min_value,
            max_value=max_value,
        )
    return {
        "ok": True,
        "as_of": _now(),
        "metric_name": metric_name,
        "period": period,
        "min_value": min_value,
        "max_value": max_value,
        "count": len(rows),
        "rows": rows,
    }


def compare_vault_metrics(
    tickers: list[str],
    metric_names: list[str],
    *,
    recent_periods: int = 5,
) -> dict[str, Any]:
    """Return recent vault metric history for multiple companies."""
    settings = get_settings()
    missing = _require_vault(settings)
    if missing:
        return missing
    validated = [validate_ticker(t) for t in tickers]
    if not metric_names:
        raise ValueError("metric_names must be a non-empty list")
    if recent_periods <= 0 or recent_periods > 20:
        raise ValueError("recent_periods must be between 1 and 20")

    rows: list[dict[str, Any]] = []
    for ticker in validated:
        snapshot = load_parsed_json(settings, ticker)
        metric_blocks = {}
        if snapshot is not None:
            merged = {
                **snapshot.series,
                **snapshot.ratios,
                **snapshot.valuation_history,
                **{f"dcf.{k}": [v] for k, v in snapshot.dcf.items()},
            }
            for metric in metric_names:
                values = merged.get(metric)
                if values is None:
                    metric_blocks[metric] = None
                    continue
                if metric.startswith("dcf."):
                    metric_blocks[metric] = {"periods": ["point_in_time"], "values": values}
                else:
                    metric_blocks[metric] = {
                        "periods": snapshot.periods[-recent_periods:],
                        "values": values[-recent_periods:],
                    }
        rows.append({"ticker": ticker, "metrics": metric_blocks, "has_parsed_data": snapshot is not None})
    return {
        "ok": True,
        "as_of": _now(),
        "tickers": validated,
        "metric_names": metric_names,
        "recent_periods": recent_periods,
        "rows": rows,
    }


def vault_dashboard() -> dict[str, Any]:
    """Summarize vault freshness, memo coverage, and DCF flags."""
    settings = get_settings()
    missing = _require_vault(settings)
    if missing:
        return missing
    companies = list_companies(settings)
    rows: list[dict[str, Any]] = []
    for company in companies:
        ticker = company["ticker"]
        snapshot = load_parsed_json(settings, ticker)
        memos = list_company_memos(settings, ticker)
        latest_memo = memos[0] if memos else None
        dcf = snapshot.dcf if snapshot is not None else {}
        rows.append(
            {
                **company,
                "latest_memo": latest_memo,
                "parsed_at": snapshot.parsed_at.isoformat() if snapshot else None,
                "latest_period": snapshot.periods[-1] if snapshot and snapshot.periods else None,
                "dcf_target_price": dcf.get("target_price"),
                "dcf_intrinsic_value_per_share": dcf.get("intrinsic_value_per_share"),
                "dcf_margin_of_safety": dcf.get("margin_of_safety"),
                "needs_memo": latest_memo is None,
                "needs_parsed_data": snapshot is None,
            }
        )
    return {
        "ok": True,
        "as_of": _now(),
        "vault_root": str(settings.vault_root),
        "count": len(rows),
        "companies": rows,
    }


def compare_to_last_memo_tool(
    ticker: str,
    new_evidence_summary: str | None = None,
) -> dict[str, Any]:
    """Return latest memo context plus instructions for a thesis-change review."""
    settings = get_settings()
    missing = _require_vault(settings)
    if missing:
        return missing
    ticker = validate_ticker(ticker)
    memos = list_company_memos(settings, ticker)
    if not memos:
        return {
            "ok": True,
            "as_of": _now(),
            "ticker": ticker,
            "has_prior_memo": False,
            "message": "No prior memo found for this ticker.",
        }
    latest = memos[0]
    latest_text = Path(latest["path"]).read_text(encoding="utf-8")
    latest_meta, latest_body = parse_frontmatter(latest_text)
    previous = memos[1] if len(memos) > 1 else None
    previous_excerpt = None
    if previous is not None:
        _meta, previous_body = parse_frontmatter(Path(previous["path"]).read_text(encoding="utf-8"))
        previous_excerpt = _excerpt(previous_body, max_chars=2500)
    return {
        "ok": True,
        "as_of": _now(),
        "ticker": ticker,
        "has_prior_memo": True,
        "latest_memo": latest,
        "latest_frontmatter": latest_meta,
        "latest_memo_excerpt": _excerpt(latest_body, max_chars=5000),
        "previous_memo": previous,
        "previous_memo_excerpt": previous_excerpt,
        "new_evidence_summary": new_evidence_summary,
        "instructions": (
            "Compare the latest memo against the new evidence. Identify thesis changes, "
            "new or retired risks, valuation changes, evidence that contradicts the prior "
            "memo, and whether the verdict should change. Separate sourced facts from interpretation."
        ),
    }


# ---- Peer-set tools -----------------------------------------------------


def write_peer_set_tool(
    name: str,
    tickers: list[str],
    *,
    description: str | None = None,
) -> dict[str, Any]:
    """Save a user-curated peer set under Watchlists/peer_sets.yml."""
    settings = get_settings()
    missing = _require_vault(settings)
    if missing:
        return missing
    validated = [validate_ticker(t) for t in tickers]
    if not name.strip():
        raise ValueError("name must be non-empty")
    data = _read_peer_sets(settings, strict=True)
    data[name] = {"tickers": validated, "description": description or ""}
    _write_peer_sets(settings, data)
    return {"ok": True, "as_of": _now(), "name": name, "tickers": validated}


def list_peer_sets_tool() -> dict[str, Any]:
    settings = get_settings()
    missing = _require_vault(settings)
    if missing:
        return missing
    data = _read_peer_sets(settings)
    return {"ok": True, "as_of": _now(), "count": len(data), "peer_sets": data}


def load_peer_set_tool(name: str) -> dict[str, Any]:
    settings = get_settings()
    missing = _require_vault(settings)
    if missing:
        return missing
    data = _read_peer_sets(settings)
    peer_set = data.get(name)
    if peer_set is None:
        return {"ok": False, "error": "PEER_SET_NOT_FOUND", "name": name}
    return {"ok": True, "as_of": _now(), "name": name, **peer_set}


def load_peer_set(settings: Settings, name: str) -> list[str]:
    """Internal helper used by valuation tools."""
    peer_set = _read_peer_sets(settings).get(name)
    if not peer_set:
        return []
    return [validate_ticker(t) for t in peer_set.get("tickers", [])]


def _peer_sets_path(settings: Settings) -> Path:
    return get_vault_root(settings) / "Watchlists" / "peer_sets.yml"


def _read_peer_sets(settings: Settings, *, strict: bool = False) -> dict[str, Any]:
    """Load peer sets. With ``strict``, a corrupt file raises instead of
    reading as empty, so a following write cannot silently wipe it."""
    path = _peer_sets_path(settings)
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        if strict:
            raise ValueError(
                f"{path.name} could not be parsed ({exc}); fix or remove it before saving peer sets."
            ) from exc
        return {}
    if not isinstance(data, dict):
        if strict:
            raise ValueError(f"{path.name} must contain a mapping of peer-set names.")
        return {}
    return data


def _write_peer_sets(settings: Settings, data: dict[str, Any]) -> None:
    path = _peer_sets_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _latest_metric_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row["ticker"]
        if key not in latest or str(row.get("period") or "") > str(latest[key].get("period") or ""):
            latest[key] = row
    return sorted(latest.values(), key=lambda row: row["ticker"])


def _excerpt(text: str, *, max_chars: int) -> str:
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rsplit(" ", 1)[0].rstrip() + "..."
