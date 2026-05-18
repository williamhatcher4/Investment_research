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
from typing import Any

from investor_research.config import Settings, get_settings
from investor_research.tickers import validate_ticker
from investor_research.vault.database import reindex_vault
from investor_research.vault.notes import (
    list_company_memos,
    read_company_frontmatter,
    read_company_note,
    write_company_note,
    write_memo_to_vault,
)
from investor_research.vault.paths import (
    VaultNotConfiguredError,
    create_company_folder,
    list_companies,
    vault_enabled,
)
from investor_research.vault.snapshots import import_xlsx, load_parsed_json

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
