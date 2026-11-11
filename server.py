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
    research_follow_up as _research_follow_up,
)
from investor_research.tools.valuation import (
    batch_dcf_screen as _batch_dcf_screen,
    build_dcf as _build_dcf,
    compare_peers as _compare_peers,
    data_cross_check as _data_cross_check,
    dcf_scenario_analysis as _dcf_scenario_analysis,
    valuation_snapshot as _valuation_snapshot,
)
from investor_research.tools.vault_tools import (
    compare_to_last_memo_tool as _compare_to_last_memo_tool,
    compare_vault_metrics as _compare_vault_metrics,
    create_vault_company as _create_vault_company,
    import_watchlist_xlsx_export as _import_watchlist_xlsx_export,
    import_xlsx_export as _import_xlsx_export,
    list_peer_sets_tool as _list_peer_sets_tool,
    list_company_memos_tool as _list_company_memos_tool,
    list_vault_companies as _list_vault_companies,
    load_company_data as _load_company_data,
    load_company_watchlist_tool as _load_company_watchlist_tool,
    load_peer_set_tool as _load_peer_set_tool,
    load_watchlist_snapshot_tool as _load_watchlist_snapshot_tool,
    screen_vault_companies as _screen_vault_companies,
    vault_dashboard as _vault_dashboard,
    write_peer_set_tool as _write_peer_set_tool,
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
mcp = FastMCP(
    "investor-research",
    instructions=(
        "Use these tools for both initial and follow-up equity research questions. "
        "When a follow-up asks for more detail, updated facts, or a challenge to a "
        "prior answer and no narrower tool fits, call research_follow_up so the "
        "answer is grounded in fresh MCP evidence rather than prior chat context alone."
    ),
)


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
def research_follow_up(
    ticker: str,
    question: str,
    recency_days: int | None = None,
) -> dict:
    """Fresh evidence for a follow-up investment research question.

    Use this for conversational follow-up questions when the user asks for
    more detail, an update, a challenge to a prior answer, or a topic not
    covered by a more specific tool. It always performs a new MCP research
    lookup instead of relying only on previous chat context.
    """
    return _research_follow_up(ticker, question, recency_days=recency_days)


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
def valuation_snapshot(ticker: str, peer_set: str | None = None) -> dict:
    """Current valuation multiples plus peer averages.

    Returns the subject ticker's P/E, EV/EBITDA, FCF yield, margins, and
    debt profile (from FMP), peer-by-peer rows for up to 5 FMP peers, and
    peer-average versions of each metric for quick relative-value context.
    """
    return _valuation_snapshot(ticker, peer_set=peer_set)


@mcp.tool()
def dcf_scenario_analysis(
    ticker: str,
    bear_adjustment: float = -0.20,
    bull_adjustment: float = 0.20,
) -> dict:
    """Bear/base/bull DCF sensitivity using parsed vault DCF outputs."""
    return _dcf_scenario_analysis(
        ticker, bear_adjustment=bear_adjustment, bull_adjustment=bull_adjustment
    )


@mcp.tool()
def data_cross_check(ticker: str) -> dict:
    """Compare key vault fundamentals against FMP as a sanity check."""
    return _data_cross_check(ticker)


@mcp.tool()
def build_dcf(
    ticker: str,
    base_fcf_per_share: float | None = None,
    growth_rate_yr1_5: float | None = None,
    growth_rate_yr6_10: float | None = None,
    discount_rate: float = 0.09,
    terminal_growth_rate: float = 0.03,
) -> dict:
    """Build a 10-year per-share DCF model and return intrinsic value.

    Automatically sources base FCF per share from (in priority order):
    vault financials series, FMP cash flow statement, FMP FCF yield × price.
    Sources growth rates from watchlist historical FCF/share growth rates or
    vault DCF projections. Override any input explicitly to customize.

    Returns year-by-year projections, terminal value, intrinsic value per share,
    and upside vs. current price.
    """
    return _build_dcf(
        ticker,
        base_fcf_per_share=base_fcf_per_share,
        growth_rate_yr1_5=growth_rate_yr1_5,
        growth_rate_yr6_10=growth_rate_yr6_10,
        discount_rate=discount_rate,
        terminal_growth_rate=terminal_growth_rate,
    )


@mcp.tool()
def batch_dcf_screen(
    top_n: int = 10,
    discount_rate: float = 0.09,
    terminal_growth_rate: float = 0.03,
    tickers: list[str] | None = None,
) -> dict:
    """Run a DCF across all watchlist companies and rank by upside to intrinsic value.

    Uses the watchlist snapshot as the base rate for growth inputs
    (historical FCF per share growth 5y/10y). Sources current FCF per share
    from vault data or FMP. Returns the top ``top_n`` companies ranked by
    upside (intrinsic value / current price - 1), descending.

    This directly answers: which companies on the watchlist are most
    undervalued on a DCF basis?
    """
    return _batch_dcf_screen(
        top_n=top_n,
        discount_rate=discount_rate,
        terminal_growth_rate=terminal_growth_rate,
        tickers=tickers,
    )


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


@mcp.tool()
def screen_vault_companies(
    metric_name: str,
    min_value: float | None = None,
    max_value: float | None = None,
    period: str | None = None,
    tickers: list[str] | None = None,
) -> dict:
    """Screen vault companies by indexed metric thresholds."""
    return _screen_vault_companies(
        metric_name,
        min_value=min_value,
        max_value=max_value,
        period=period,
        tickers=tickers,
    )


@mcp.tool()
def compare_vault_metrics(
    tickers: list[str],
    metric_names: list[str],
    recent_periods: int = 5,
) -> dict:
    """Compare recent parsed vault metric history across tickers."""
    return _compare_vault_metrics(
        tickers, metric_names, recent_periods=recent_periods
    )


@mcp.tool()
def vault_dashboard() -> dict:
    """Vault freshness and coverage dashboard."""
    return _vault_dashboard()


@mcp.tool()
def compare_to_last_memo(
    ticker: str,
    new_evidence_summary: str | None = None,
) -> dict:
    """Load the latest memo and instructions for a thesis-change review."""
    return _compare_to_last_memo_tool(
        ticker, new_evidence_summary=new_evidence_summary
    )


@mcp.tool()
def write_peer_set(
    name: str,
    tickers: list[str],
    description: str | None = None,
) -> dict:
    """Save a user-curated peer set under the vault."""
    return _write_peer_set_tool(name, tickers, description=description)


@mcp.tool()
def list_peer_sets() -> dict:
    """List user-curated peer sets in the vault."""
    return _list_peer_sets_tool()


@mcp.tool()
def load_peer_set(name: str) -> dict:
    """Load one user-curated peer set by name."""
    return _load_peer_set_tool(name)


@mcp.tool()
def import_watchlist_xlsx_export(file_path: str) -> dict:
    """Import a multi-company watchlist scorecard xlsx.

    Parses the workbook, fans out per-company watchlist data under
    Companies/{TICKER}/parsed/watchlist.json, writes a master index under
    Watchlists/, auto-seeds US-suffix Bloomberg tickers in
    Watchlists/ticker_aliases.yml, and updates the 'watchlist_coverage'
    peer set. Returns the list of Bloomberg tickers (non-US) that still
    need manual alias entries.
    """
    return _import_watchlist_xlsx_export(file_path)


@mcp.tool()
def load_watchlist_snapshot() -> dict:
    """Return the most recently imported watchlist snapshot."""
    return _load_watchlist_snapshot_tool()


@mcp.tool()
def load_company_watchlist(ticker: str) -> dict:
    """Return one company's watchlist row from the most recent scorecard import."""
    return _load_company_watchlist_tool(ticker)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
