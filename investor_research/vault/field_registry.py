"""Field registry — maps Bloomberg/FactSet template labels & codes to normalized keys.

The xlsx parser walks rows of the workbook's ``Main`` sheet and looks up
each row's label (column C) and field code (column D) in this registry.
Matched rows have their annual time-series values extracted into the
normalized JSON output.

Extending the registry: append a new ``FieldSpec`` to ``FIELDS``. Labels
are matched case-insensitively after collapsing whitespace; codes are
matched exact (uppercase). The parser does not require both a label and
a code match — either is sufficient.

All annual financial values are stored as-is from the workbook (typically
in millions of the reporting currency; the workbook's ``Scale`` cell is
captured in ``parsed/metadata.json`` under ``units``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal


Category = Literal[
    "financials",      # IS + CF series ($ amounts, share counts)
    "balance_sheet",   # BS line items ($ amounts)
    "ratios",          # margins, returns, growth rates (ratios)
    "valuation",       # price/multiple history series
    "dcf",             # DCF inputs/outputs (scalars, not series)
    "metadata",        # company description, exchange, etc.
]


@dataclass(frozen=True)
class FieldSpec:
    normalized_key: str
    labels: tuple[str, ...]
    codes: tuple[str, ...] = field(default_factory=tuple)
    category: Category = "financials"
    unit: str = "currency"


_WHITESPACE_RE = re.compile(r"\s+")


def _norm_label(text: str) -> str:
    """Lowercase + collapse whitespace + strip — for tolerant label matching."""
    return _WHITESPACE_RE.sub(" ", text).strip().lower()


# Income statement + cash flow series (annual).
INCOME_AND_CASH_FLOW: tuple[FieldSpec, ...] = (
    FieldSpec("revenue", ("Revenue",), ("SALES_REV_TURN",), "financials", "currency"),
    FieldSpec("gross_profit", ("Gross income", "Gross profit"), ("GROSS_PROFIT",), "financials"),
    FieldSpec("rd_expense", ("R&D expenses",), ("IS_OPEX_R_AND_D_GAAP",), "financials"),
    FieldSpec("sga_expense", ("SG&A expenses",), ("IS_SG_AND_A_EXPENSE",), "financials"),
    FieldSpec("ebitda", ("EBITDA",), (), "financials"),
    FieldSpec("depreciation", ("Depreciation",), ("IS_DEPR_EXP",), "financials"),
    FieldSpec("ebita", ("EBITA",), ("EBITA",), "financials"),
    FieldSpec("goodwill_amort", ("Goodwill amort/impairment",), ("AMORT_OF_GOODWILL",), "financials"),
    FieldSpec("ebit", ("EBIT",), ("EBIT",), "financials"),
    FieldSpec("interest_expense", ("Interest expense",), ("IS_INT_EXPENSE",), "financials"),
    FieldSpec("interest_income", ("Interest income",), ("IS_INT_INC",), "financials"),
    FieldSpec("pretax_income", ("Pre-tax income",), ("IS_PRETAX_INCOME",), "financials"),
    FieldSpec("tax_expense", ("Tax expense",), ("IS_INC_TAX_EXP",), "financials"),
    FieldSpec("net_income", ("Net income",), ("NET_INCOME",), "financials"),
    FieldSpec("shares_outstanding", ("Shares Out", "Shares outstanding"), ("BS_SH_OUT",), "financials", "shares"),
    FieldSpec("eps", ("EPS",), (), "financials", "per_share"),
    FieldSpec("dividends_per_share", ("Dividends",), ("IS_TOT_CASH_COM_DVD",), "financials", "per_share"),
    FieldSpec("payout_ratio", ("Payout ratio",), ("DVD_PAYOUT_RATIO",), "ratios", "ratio"),
    FieldSpec("d_and_a", ("D & A", "D&A"), ("CF_DEPR_AMORT",), "financials"),
    FieldSpec("capex", ("Capex", "CapEx"), ("CAPITAL_EXPEND",), "financials"),
    FieldSpec("change_in_wc", ("Decrease (increase) in WC",), ("CHNG_WORK_CAP",), "financials"),
    FieldSpec("free_cash_flow", ("Free cash flow",), (), "financials"),
    FieldSpec("operating_cash_flow", ("Operating cash flow (after tax and WC)",), ("CF_CASH_FROM_OPER",), "financials"),
    FieldSpec("investing_cash_flow", ("Investing cash flow",), ("CF_CASH_FROM_INV_ACT",), "financials"),
    FieldSpec("acquisitions", ("Net Cash Acquisitions",), ("CF_NT_CSH_RCVD_PD_FOR_ACQUIS_DIV",), "financials"),
    FieldSpec("dividends_cash", ("Dividends",), ("CF_DVD_PAID",), "financials"),
    FieldSpec("buybacks", ("Net share issue (buybacks)",), ("PROC_FR_REPURCH_EQTY_DETAILED",), "financials"),
)


# Margins + returns + growth (annual ratios).
MARGINS_AND_RETURNS: tuple[FieldSpec, ...] = (
    FieldSpec("gross_margin", ("Gross margin",), (), "ratios", "ratio"),
    FieldSpec("rd_pct_sales", ("R&D % of sales",), (), "ratios", "ratio"),
    FieldSpec("sga_pct_sales", ("SG&A % of sales",), (), "ratios", "ratio"),
    FieldSpec("ebitda_margin", ("EBITDA margin",), (), "ratios", "ratio"),
    FieldSpec("ebita_margin", ("EBITA margin",), (), "ratios", "ratio"),
    FieldSpec("ebit_margin", ("EBIT margin",), (), "ratios", "ratio"),
    FieldSpec("net_margin", ("Net Margin", "Net margin"), (), "ratios", "ratio"),
    FieldSpec("fcf_margin_pre_wc", ("FCFM before WC",), (), "ratios", "ratio"),
    FieldSpec("fcf_margin_post_wc", ("FCFM after WC",), (), "ratios", "ratio"),
    FieldSpec("debt_to_cap", ("Debt to cap",), (), "ratios", "ratio"),
    FieldSpec("interest_coverage", ("Interest coverage",), (), "ratios", "ratio"),
    FieldSpec("asset_turns", ("Asset turns",), (), "ratios", "ratio"),
    FieldSpec("roe", ("ROE",), (), "ratios", "ratio"),
    FieldSpec("roe_tangible", ("ROE (tangible)",), (), "ratios", "ratio"),
    FieldSpec("roce_post_tax", ("ROCE (post-tax)",), (), "ratios", "ratio"),
    FieldSpec("roce_tangible_post_tax", ("ROCE (tangible, post-tax)",), (), "ratios", "ratio"),
    FieldSpec("rogi_post_tax", ("ROGI (post-tax)",), (), "ratios", "ratio"),
)


# Balance sheet line items (annual).
BALANCE_SHEET: tuple[FieldSpec, ...] = (
    FieldSpec("cash", ("Cash & Equivalents",), ("C_AND_CE_AND_STI_DETAILED",), "balance_sheet"),
    FieldSpec("current_assets", ("Current Assets",), ("BS_CUR_ASSET_REPORT",), "balance_sheet"),
    FieldSpec("gross_ppe", ("Gross PP&E",), ("BS_GROSS_FIX_ASSET",), "balance_sheet"),
    FieldSpec("net_ppe", ("Net PP&E",), ("BS_NET_FIX_ASSET",), "balance_sheet"),
    FieldSpec("intangibles", ("Intangible Assets",), ("BS_DISCLOSED_INTANGIBLES",), "balance_sheet"),
    FieldSpec("total_assets", ("Total Assets",), ("BS_TOT_ASSET",), "balance_sheet"),
    FieldSpec("total_tangible_assets", ("Total Tangible Assets",), ("TANGIBLE_ASSETS",), "balance_sheet"),
    FieldSpec("short_term_debt", ("Short Term Debt",), ("BS_ST_BORROW",), "balance_sheet"),
    FieldSpec("current_liabilities", ("Current Liabilities",), ("BS_CUR_LIAB",), "balance_sheet"),
    FieldSpec("total_debt", ("Total Debt",), ("SHORT_AND_LONG_TERM_DEBT",), "balance_sheet"),
    FieldSpec("net_debt", ("Net debt (cash)",), (), "balance_sheet"),
    FieldSpec("net_working_capital", ("Net Working Cap",), (), "balance_sheet"),
    FieldSpec("equity", ("Equity",), ("TOTAL_EQUITY",), "balance_sheet"),
)


# Valuation history series (annual high/low).
VALUATION_HISTORY: tuple[FieldSpec, ...] = (
    FieldSpec("share_price_high", ("Share price - HIGH",), ("MAX(PX_LAST())",), "valuation"),
    FieldSpec("share_price_low", ("Share price - LOW",), ("MIN(PX_LAST())",), "valuation"),
    FieldSpec("mkt_cap_high", ("Mkt cap - HIGH",), (), "valuation"),
    FieldSpec("mkt_cap_low", ("MKT cap - LOW", "Mkt cap - LOW"), (), "valuation"),
    FieldSpec("pb_high", ("P/B - HIGH",), (), "valuation", "ratio"),
    FieldSpec("pb_low", ("P/B - LOW",), (), "valuation", "ratio"),
    FieldSpec("pb_tangible_high", ("P/B tangible - HIGH",), (), "valuation", "ratio"),
    FieldSpec("pb_tangible_low", ("P/B tangible - LOW",), (), "valuation", "ratio"),
)


# Per-share valuation history.
PER_SHARE: tuple[FieldSpec, ...] = (
    FieldSpec("intrinsic_per_share_history", ("INTRINSIC PER SHARE",), (), "valuation", "per_share"),
    FieldSpec("book_per_share_history", ("BOOK PER SHARE",), (), "valuation", "per_share"),
    FieldSpec("tangible_book_per_share_history", ("TANGIBLE BOOK PER SHARE",), (), "valuation", "per_share"),
)


SERIES_FIELDS: tuple[FieldSpec, ...] = (
    INCOME_AND_CASH_FLOW
    + MARGINS_AND_RETURNS
    + BALANCE_SHEET
    + VALUATION_HISTORY
    + PER_SHARE
)


# Lookup helpers used by the parser.

def label_lookup() -> dict[str, FieldSpec]:
    """Normalized-label → FieldSpec. Later entries do not override earlier."""
    out: dict[str, FieldSpec] = {}
    for spec in SERIES_FIELDS:
        for label in spec.labels:
            key = _norm_label(label)
            out.setdefault(key, spec)
    return out


def code_lookup() -> dict[str, FieldSpec]:
    """Uppercase field-code → FieldSpec."""
    out: dict[str, FieldSpec] = {}
    for spec in SERIES_FIELDS:
        for code in spec.codes:
            out.setdefault(code.upper(), spec)
    return out


# Metadata labels found in the business-description block (rows ~170-180).
METADATA_LABELS: dict[str, str] = {
    "phone:": "phone",
    "exchange:": "exchange",
    "industry:": "industry",
    "sector:": "sector",
    "description:": "description",
}


# DCF scalars found in the right-hand VALUATION block (col J area).
DCF_SCALAR_LABELS: dict[str, str] = {
    "sustainable growth rate": "sustainable_growth_rate",
    "discount rate": "discount_rate",
    "terminal growth rate": "terminal_growth_rate",
    "intrinsic value per share": "intrinsic_value_per_share",
    "current share price": "current_share_price",
    "margin of safety": "margin_of_safety",
    "target price": "target_price",
    "intrinsic value growth - last 10 years": "intrinsic_value_growth_10yr",
    "intrinsic value growth - last 5 years": "intrinsic_value_growth_5yr",
    "fcfps growth - last 10 years": "fcfps_growth_10yr",
    "fcfps growth - last 5 years": "fcfps_growth_5yr",
    "consensus eps growth": "consensus_eps_growth",
    "consensus ebitda growth": "consensus_ebitda_growth",
    "fcf growth projection (10 yrs)": "fcf_growth_projection_10yr",
}
