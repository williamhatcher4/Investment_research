"""Pydantic models for tool outputs.

In Tavily / raw-evidence mode (the current default), each tool returns a
``ResearchEvidence`` bundle: search results, an optional synthesized answer,
and a deduplicated source list. Claude Desktop downstream does the
fact/interpretation structuring.

The structured-output models below (``Section``, ``SourcedFact``,
``ResearchCompanyPayload`` etc.) are kept for the Perplexity provider's
optional schema-enforced mode.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------- Primitives ---------------------------------------------------


class Source(BaseModel):
    """A single citation."""

    model_config = ConfigDict(extra="ignore")

    url: str
    title: str | None = None
    publisher: str | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResearchResult(BaseModel):
    """A single search result (one row from Tavily / Perplexity)."""

    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    url: str
    content: str = ""
    score: float | None = None
    published_date: str | None = None


class ResearchEvidence(BaseModel):
    """The bundle a provider returns for one research query."""

    model_config = ConfigDict(extra="ignore")

    provider: str
    query: str
    answer: str | None = None
    results: list[ResearchResult] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    raw_meta: dict[str, Any] = Field(default_factory=dict)


class SourcedFact(BaseModel):
    """A claim with one or more source-list indices backing it."""

    model_config = ConfigDict(extra="ignore")

    claim: str = Field(description="A specific factual statement.")
    source_indices: list[int] = Field(
        default_factory=list,
        description="Indices into the enclosing result's `sources` list.",
    )

    @field_validator("source_indices")
    @classmethod
    def _non_negative(cls, v: list[int]) -> list[int]:
        if any(i < 0 for i in v):
            raise ValueError("source_indices must be non-negative")
        return v


class Section(BaseModel):
    """A labeled bundle of facts plus optional interpretation."""

    model_config = ConfigDict(extra="ignore")

    facts: list[SourcedFact] = Field(default_factory=list)
    interpretation: str | None = None


# ---------- Per-tool payloads (what Perplexity is asked to fill) ----------


class ResearchCompanyPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    business_model: Section
    revenue_segments: Section
    latest_earnings_summary: Section
    major_growth_drivers: Section
    margin_profile: Section
    balance_sheet_concerns: Section
    valuation_context: Section
    key_risks: Section
    competitors: Section
    recent_material_news: Section


class NewsEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    headline: str
    event_date: str | None = Field(
        default=None,
        description="ISO date if available, else relative description.",
    )
    category: str = Field(
        description=(
            "One of: product_launch, regulatory, management_change, "
            "litigation, acquisition, earnings, other_material."
        )
    )
    summary: str
    source_indices: list[int] = Field(default_factory=list)


class RecentNewsPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    days_window: int
    events: list[NewsEvent]
    interpretation: str | None = None


class EarningsSummaryPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    period: str = Field(description="e.g. 'Q3 2025' or 'FY2024'.")
    revenue: Section
    eps: Section
    margins: Section
    guidance: Section
    management_commentary: Section
    analyst_concerns: Section
    market_reaction: Section


class CompetitorEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ticker: str
    name: str | None = None
    business_model_comparison: str
    growth_comparison: str
    valuation_comparison: str
    risk_comparison: str
    source_indices: list[int] = Field(default_factory=list)


class CompetitorsPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    competitors: list[CompetitorEntry]
    interpretation: str | None = None


class RisksPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    operational: Section
    balance_sheet: Section
    regulatory: Section
    macro: Section
    valuation: Section
    industry: Section


# ---------- Tool result envelopes ----------------------------------------


class ToolResult(BaseModel):
    """Common envelope returned by every tool."""

    model_config = ConfigDict(extra="ignore")

    ticker: str
    as_of: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sources: list[Source] = Field(default_factory=list)
    payload: dict[str, Any]
    notes: list[str] = Field(default_factory=list)


# ---------- Memo --------------------------------------------------------


class MemoSection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    body: str
    facts: list[SourcedFact] = Field(default_factory=list)
    interpretation: str | None = None


class InvestmentMemoPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    executive_summary: MemoSection
    business_overview: MemoSection
    financial_profile: MemoSection
    recent_earnings: MemoSection
    competitive_position: MemoSection
    bull_case: MemoSection
    bear_case: MemoSection
    key_risks: MemoSection
    valuation_discussion: MemoSection
    open_questions: MemoSection
    watchlist_verdict: MemoSection


SECTION_ORDER: tuple[str, ...] = (
    "executive_summary",
    "business_overview",
    "financial_profile",
    "recent_earnings",
    "competitive_position",
    "bull_case",
    "bear_case",
    "key_risks",
    "valuation_discussion",
    "open_questions",
    "watchlist_verdict",
)


SECTION_TITLES: dict[str, str] = {
    "executive_summary": "Executive Summary",
    "business_overview": "Business Overview",
    "financial_profile": "Financial Profile",
    "recent_earnings": "Recent Earnings",
    "competitive_position": "Competitive Position",
    "bull_case": "Bull Case",
    "bear_case": "Bear Case",
    "key_risks": "Key Risks",
    "valuation_discussion": "Valuation Discussion",
    "open_questions": "Open Questions",
    "watchlist_verdict": "Watchlist Verdict",
}


# ---------- Vault: parsed xlsx fundamentals -----------------------------


class FundamentalsSnapshot(BaseModel):
    """Structured fundamentals parsed from a Bloomberg/FactSet xlsx export.

    The shape mirrors the on-disk JSON layout (``parsed/financials.json`` +
    ``parsed/valuation.json`` + ``parsed/metadata.json``), unified into a
    single model for in-process consumption.
    """

    model_config = ConfigDict(extra="ignore")

    ticker: str
    company_name: str | None = None
    currency: str | None = None
    units: str | None = None
    fiscal_year_end: str | None = None
    periods: list[str] = Field(default_factory=list)
    series: dict[str, list[float | None]] = Field(default_factory=dict)
    ratios: dict[str, list[float | None]] = Field(default_factory=dict)
    valuation_history: dict[str, list[float | None]] = Field(default_factory=dict)
    dcf: dict[str, float | None] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_file: str | None = None
    source_sha256: str | None = None
    parsed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    warnings: list[str] = Field(default_factory=list)

    def has_data(self) -> bool:
        return bool(self.series or self.ratios or self.valuation_history or self.dcf)

    def to_prompt_block(self, *, recent_years: int = 5) -> str:
        """Render a compact, deterministic summary for LLM prompts.

        Includes: company metadata, the last ``recent_years`` of the
        highest-value series (revenue, EBIT margin, FCF, ROIC, net debt),
        and DCF scalars (target price, intrinsic value, MOS).
        """
        if not self.has_data():
            return "(no parsed fundamentals available)"
        lines: list[str] = []
        meta_bits: list[str] = [f"ticker={self.ticker}"]
        if self.company_name:
            meta_bits.append(f"name={self.company_name}")
        if self.metadata.get("sector"):
            meta_bits.append(f"sector={self.metadata['sector']}")
        if self.metadata.get("industry"):
            meta_bits.append(f"industry={self.metadata['industry']}")
        if self.metadata.get("exchange"):
            meta_bits.append(f"exchange={self.metadata['exchange']}")
        if self.currency:
            meta_bits.append(f"currency={self.currency}")
        if self.units:
            meta_bits.append(f"units={self.units}")
        lines.append("Profile: " + " | ".join(meta_bits))

        if self.metadata.get("description"):
            desc = str(self.metadata["description"])[:600]
            lines.append(f"Description: {desc}")

        if self.periods:
            tail_n = min(recent_years, len(self.periods))
            tail_periods = self.periods[-tail_n:]
            lines.append(f"Recent periods: {', '.join(tail_periods)}")

            def tail(key: str, source: dict[str, list[float | None]]) -> str | None:
                if key not in source:
                    return None
                values = source[key][-tail_n:]
                formatted = [
                    "n/a" if v is None else f"{v:,.1f}" if abs(v) >= 1 else f"{v:.3f}"
                    for v in values
                ]
                return f"{key}: {', '.join(formatted)}"

            highlight_series = (
                "revenue",
                "ebit",
                "net_income",
                "free_cash_flow",
                "total_debt",
                "shares_outstanding",
            )
            highlight_ratios = (
                "gross_margin",
                "ebit_margin",
                "net_margin",
                "roe",
                "roce_post_tax",
            )
            for key in highlight_series:
                line = tail(key, self.series)
                if line:
                    lines.append(line)
            for key in highlight_ratios:
                line = tail(key, self.ratios)
                if line:
                    lines.append(line)

        if self.dcf:
            dcf_bits: list[str] = []
            for key in (
                "intrinsic_value_per_share",
                "target_price",
                "current_share_price",
                "margin_of_safety",
                "discount_rate",
                "terminal_growth_rate",
            ):
                value = self.dcf.get(key)
                if value is not None:
                    dcf_bits.append(f"{key}={value:.3f}")
            if dcf_bits:
                lines.append("DCF: " + " | ".join(dcf_bits))

        if self.warnings:
            lines.append("Warnings: " + " | ".join(self.warnings[:5]))

        return "\n".join(lines)

    def to_source(self) -> Source | None:
        if not self.source_file:
            return None
        return Source(
            url=f"file://{self.source_file}",
            title=f"User vault — parsed xlsx for {self.ticker}",
            publisher="Local vault",
        )


class VaultCompany(BaseModel):
    """Frontmatter + index entry for a company in the vault."""

    model_config = ConfigDict(extra="ignore")

    ticker: str
    company_name: str | None = None
    sector: str | None = None
    industry: str | None = None
    exchange: str | None = None
    status: str | None = None
    last_updated: str | None = None
    has_parsed: bool = False
    has_exports: bool = False
