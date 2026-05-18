"""Vault filesystem paths and folder lifecycle.

All path math goes through here. Other vault modules call ``get_vault_root()``
and friends rather than building paths themselves so the layout is
defined in one place.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from investor_research.config import Settings
from investor_research.tickers import validate_ticker

logger = logging.getLogger(__name__)


# Vault subdirectory layout (relative to VAULT_ROOT).
TOP_LEVEL_DIRS: tuple[str, ...] = (
    "Companies",
    "Sectors",
    "Watchlists",
    "Templates",
    "database",
)

# Per-company subdirectory layout (relative to Companies/{TICKER}/).
COMPANY_SUBDIRS: tuple[str, ...] = (
    "bloomberg_exports",
    "parsed",
    "memos",
    "notes",
)


class VaultNotConfiguredError(RuntimeError):
    """Raised when the vault is needed but ``VAULT_ROOT`` is unset."""


def vault_enabled(settings: Settings) -> bool:
    """True when ``VAULT_ROOT`` is configured. The directory itself may not exist yet."""
    return settings.vault_root is not None


def get_vault_root(settings: Settings) -> Path:
    """Return the configured vault root or raise ``VaultNotConfiguredError``."""
    if settings.vault_root is None:
        raise VaultNotConfiguredError(
            "VAULT_ROOT is not set. Configure it in .env or run "
            "`python -m investor_research.vault init` after setting it."
        )
    return settings.vault_root


def initialize_vault(settings: Settings) -> Path:
    """Create the top-level vault folders if missing. Idempotent.

    Returns the absolute vault root path.
    """
    root = get_vault_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    for sub in TOP_LEVEL_DIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    logger.info("vault.initialized", extra={"root": str(root)})
    return root


def get_company_path(settings: Settings, ticker: str) -> Path:
    """Return ``{VAULT_ROOT}/Companies/{TICKER}/`` for a validated ticker."""
    ticker = validate_ticker(ticker)
    return get_vault_root(settings) / "Companies" / ticker


def create_company_folder(
    settings: Settings,
    ticker: str,
    company_name: str | None = None,
    sector: str | None = None,
) -> Path:
    """Create the per-company folder tree and stub home note. Idempotent.

    Writes a stub ``{TICKER}.md`` with YAML frontmatter only when the note
    does not already exist — never clobbers user-written prose.
    """
    initialize_vault(settings)
    ticker = validate_ticker(ticker)
    company_dir = get_company_path(settings, ticker)
    company_dir.mkdir(parents=True, exist_ok=True)
    for sub in COMPANY_SUBDIRS:
        (company_dir / sub).mkdir(parents=True, exist_ok=True)

    note_path = company_dir / f"{ticker}.md"
    if not note_path.exists():
        # Local import to avoid a paths<->notes module cycle.
        from investor_research.vault.notes import write_company_note

        frontmatter: dict[str, Any] = {
            "ticker": ticker,
            "company_name": company_name or "",
            "sector": sector or "",
            "status": "watching",
            "last_updated": datetime.now(timezone.utc).date().isoformat(),
        }
        title = company_name or ticker
        body = f"# {title} ({ticker})\n\n_Notes go here._\n"
        write_company_note(settings, ticker, body, frontmatter=frontmatter)

    return company_dir


def get_latest_xlsx_export(settings: Settings, ticker: str) -> Path | None:
    """Return the most recently modified xlsx file under ``bloomberg_exports/``.

    Returns ``None`` when no exports have been imported yet.
    """
    ticker = validate_ticker(ticker)
    exports_dir = get_company_path(settings, ticker) / "bloomberg_exports"
    if not exports_dir.is_dir():
        return None
    xlsx_files = [p for p in exports_dir.glob("*.xlsx") if p.is_file()]
    if not xlsx_files:
        return None
    xlsx_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return xlsx_files[0]


def list_companies(settings: Settings) -> list[dict[str, Any]]:
    """Enumerate ticker folders under ``Companies/`` with frontmatter metadata.

    Result is sorted by ticker. Each entry: ``{ticker, company_name, sector,
    status, last_updated, has_parsed, has_exports}``. Missing frontmatter
    fields appear as ``None``.
    """
    if not vault_enabled(settings):
        return []
    root = get_vault_root(settings)
    companies_dir = root / "Companies"
    if not companies_dir.is_dir():
        return []

    from investor_research.vault.notes import read_company_frontmatter

    out: list[dict[str, Any]] = []
    for entry in sorted(companies_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        ticker = entry.name
        try:
            ticker = validate_ticker(ticker)
        except Exception:
            continue  # skip non-ticker folders
        meta = read_company_frontmatter(settings, ticker) or {}
        parsed_dir = entry / "parsed"
        exports_dir = entry / "bloomberg_exports"
        out.append(
            {
                "ticker": ticker,
                "company_name": meta.get("company_name") or None,
                "sector": meta.get("sector") or None,
                "status": meta.get("status") or None,
                "last_updated": meta.get("last_updated") or None,
                "has_parsed": parsed_dir.is_dir()
                and any(parsed_dir.glob("*.json")),
                "has_exports": exports_dir.is_dir()
                and any(exports_dir.glob("*.xlsx")),
            }
        )
    return out
