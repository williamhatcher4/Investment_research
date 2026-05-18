"""FastMCP entry point for the ``investor-research`` MCP server.

Run directly (``python server.py``) or via Claude Desktop's MCP config.
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from investor_research.config import get_settings
from investor_research.logging_setup import configure_logging
from investor_research.tools.filings import (
    extract_10k_risks as _extract_10k_risks,
    get_sec_filings as _get_sec_filings,
)
from investor_research.tools.memo import build_investment_memo as _build_investment_memo
from investor_research.tools.research import (
    get_competitors as _get_competitors,
    get_earnings_summary as _get_earnings_summary,
    get_recent_news as _get_recent_news,
    get_risks as _get_risks,
    research_company as _research_company,
)
from investor_research.tools.valuation import (
    compare_peers as _compare_peers,
    valuation_snapshot as _valuation_snapshot,
)
from investor_research.tools.vault_tools import (
    create_vault_company as _create_vault_company,
    import_xlsx_export as _import_xlsx_export,
    list_company_memos_tool as _list_company_memos_tool,
    list_vault_companies as _list_vault_companies,
    load_company_data as _load_company_data,
    write_company_note_tool as _write_company_note_tool,
    write_memo_to_vault_tool as _write_memo_to_vault_tool,
)


def _bootstrap() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, secrets=settings.secrets)
    logging.getLogger(__name__).info(
        "server.bootstrap",
        extra={
            "provider": settings.research_provider,
            "fmp_enabled": settings.fmp_api_key is not None,
        },
    )


_bootstrap()
mcp = FastMCP("investor-research")


@mcp.tool()
def research_company(ticker: str) -> dict:
    """Broad research profile for a public company.

    Returns business model, revenue segments, latest earnings summary,
    growth drivers, margin profile, balance sheet concerns, valuation
    context, key risks, competitors, and recent material news.
    """
    return _research_company(ticker)


@mcp.tool()
def get_recent_news(ticker: str, days: int = 30) -> dict:
    """Material company-specific events over the trailing window.

    Filters out routine analyst chatter and rumors without primary sourcing.
    """
    return _get_recent_news(ticker, days=days)


@mcp.tool()
def get_earnings_summary(ticker: str) -> dict:
    """Summary of the most recently reported quarter.

    Includes revenue, EPS, margins, guidance, management commentary,
    analyst concerns, and market reaction.
    """
    return _get_earnings_summary(ticker)


@mcp.tool()
def get_competitors(ticker: str) -> dict:
    """Primary public competitors with business model / growth / valuation / risk comparison."""
    return _get_competitors(ticker)


@mcp.tool()
def get_risks(ticker: str) -> dict:
    """Material risks across operational, balance sheet, regulatory, macro, valuation, industry."""
    return _get_risks(ticker)


@mcp.tool()
def build_investment_memo(ticker: str) -> dict:
    """Compose a full 11-section investment memo from the upstream research tools."""
    return _build_investment_memo(ticker)


# ---- Phase 2 tools ----------------------------------------------------


@mcp.tool()
def get_sec_filings(
    ticker: str,
    forms: list[str] | None = None,
    limit_per_form: int = 4,
) -> dict:
    """Recent SEC filings (10-K, 10-Q, 8-K, proxy) pulled directly from EDGAR.

    Returns filing dates, accession numbers, and direct URLs to the primary
    filing documents — the highest-quality source for company disclosures.
    """
    return _get_sec_filings(ticker, forms=forms, limit_per_form=limit_per_form)


@mcp.tool()
def extract_10k_risks(ticker: str) -> dict:
    """Surface the most recent 10-K's Item 1A Risk Factors.

    Returns the primary 10-K URL from EDGAR plus search evidence
    restricted/biased toward SEC sources so Claude can summarize the actual
    risk-factor disclosures.
    """
    return _extract_10k_risks(ticker)


@mcp.tool()
def compare_peers(tickers: list[str]) -> dict:
    """Side-by-side comparison of multiple public companies.

    For each ticker: revenue, margins, valuation multiples, debt profile
    (from FMP) plus a comparative qualitative search across the group.
    """
    return _compare_peers(tickers)


@mcp.tool()
def valuation_snapshot(ticker: str) -> dict:
    """Current valuation multiples plus peer averages.

    Returns the subject ticker's P/E, EV/EBITDA, FCF yield, margins, and
    debt profile (from FMP), peer-by-peer rows for up to 5 FMP peers, and
    peer-average versions of each metric for quick relative-value context.
    """
    return _valuation_snapshot(ticker)


# ---- Vault tools ------------------------------------------------------


@mcp.tool()
def list_vault_companies() -> dict:
    """List companies present in the local research vault.

    Returns one row per ticker folder under VAULT_ROOT/Companies/ with
    frontmatter metadata (sector, status, last_updated) and flags for
    whether parsed data and raw xlsx exports are present.
    """
    return _list_vault_companies()


@mcp.tool()
def create_vault_company(
    ticker: str,
    company_name: str | None = None,
    sector: str | None = None,
) -> dict:
    """Create the per-company folder tree (memos/, notes/, parsed/, bloomberg_exports/)
    and a stub home note. Idempotent."""
    return _create_vault_company(ticker, company_name=company_name, sector=sector)


@mcp.tool()
def import_xlsx_export(ticker: str, file_path: str) -> dict:
    """Copy a Bloomberg/FactSet xlsx export into the vault, parse it, and
    write the structured JSON files (financials.json, valuation.json,
    metadata.json) plus update the SQLite index.

    Hash-dedupes: re-importing the same file is a no-op for the raw copy.
    """
    return _import_xlsx_export(ticker, file_path)


@mcp.tool()
def load_company_data(ticker: str) -> dict:
    """Return the parsed fundamentals snapshot + vault note metadata + memo list
    for a company. Use this before composing memos so the model can anchor
    on the user's own structured data instead of re-deriving from the web."""
    return _load_company_data(ticker)


@mcp.tool()
def write_company_note(
    ticker: str,
    content_markdown: str,
) -> dict:
    """Write/overwrite the body of {TICKER}.md in the vault. Existing
    YAML frontmatter is preserved automatically; only the body is replaced."""
    return _write_company_note_tool(ticker, content_markdown)


@mcp.tool()
def write_memo_to_vault(
    ticker: str,
    memo_markdown: str,
    memo_type: str = "investment_memo",
    title: str | None = None,
) -> dict:
    """Save a generated memo to Companies/{TICKER}/memos/ as a dated markdown file
    with YAML frontmatter. memo_type controls the file slug
    (e.g., 'investment_memo', 'earnings_update', 'thesis_update')."""
    return _write_memo_to_vault_tool(
        ticker, memo_markdown, memo_type=memo_type, title=title
    )


@mcp.tool()
def list_company_memos(ticker: str) -> dict:
    """List saved memos for a company, newest first. Each row includes the
    date, memo_type, title, and absolute file path."""
    return _list_company_memos_tool(ticker)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
