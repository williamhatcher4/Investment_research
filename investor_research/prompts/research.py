"""Search-query templates per tool.

In Tavily / raw-evidence mode these are passed directly as Tavily queries.
In Perplexity mode they're the user message; the system prompt in
``prompts/system.py`` adds source-quality rules.
"""

from __future__ import annotations

RESEARCH_COMPANY_QUERY = (
    "{ticker} stock — business model, revenue segments, latest quarterly "
    "earnings, growth drivers, margin profile, balance sheet, valuation "
    "metrics, key risks, primary competitors, and recent material news. "
    "Prefer SEC filings (10-K, 10-Q, 8-K), the company's investor relations "
    "site, and earnings transcripts over blogs."
)

RECENT_NEWS_QUERY = (
    "{ticker} stock material company-specific news in the last {days} days: "
    "earnings reactions, product launches, regulatory actions, management "
    "changes, litigation, M&A. Exclude routine analyst upgrades/downgrades."
)

EARNINGS_SUMMARY_QUERY = (
    "{ticker} most recently reported quarterly earnings: revenue vs "
    "consensus, EPS vs consensus, gross/operating/net margins, forward "
    "guidance, key management commentary from the call, analyst questions, "
    "and same-day market reaction. Cite the earnings release, 10-Q, or "
    "transcript."
)

COMPETITORS_QUERY = (
    "Primary publicly traded competitors of {ticker}: who are they, how "
    "do their business models overlap, how do their growth rates, margins, "
    "and valuation multiples compare, and what risks differ. {peers_hint}"
)

RISKS_QUERY = (
    "Material investment risks facing {ticker} over the next 24 months "
    "across these categories: operational, balance sheet, regulatory, "
    "macro, valuation, and industry. Cite the latest 10-K risk factors, "
    "recent 8-Ks, and credible press coverage."
)

EXTRACT_10K_RISKS_QUERY = (
    "{ticker} 10-K Item 1A Risk Factors — summarize the most material "
    "risk factors disclosed in the most recent annual report, including "
    "management's stated concerns and any hidden operational weaknesses "
    "implied between the lines."
)

COMPARE_PEERS_QUERY = (
    "Compare {tickers_csv} across revenue growth, gross/operating margins, "
    "valuation multiples (P/E, EV/EBITDA, FCF yield), capital allocation "
    "(buybacks, dividends, M&A), and competitive moat. Use the latest "
    "10-K/10-Q disclosures and analyst coverage."
)

VALUATION_PEERS_QUERY = (
    "How does {ticker}'s current valuation compare to its peers on P/E, "
    "EV/EBITDA, and FCF yield? Is the multiple justified by growth, "
    "margins, and balance sheet relative to peers?"
)

FOLLOW_UP_QUERY = (
    "Follow-up investment research question for {ticker}: {question}\n\n"
    "Answer by gathering fresh evidence. Prefer primary sources such as SEC "
    "filings, company investor relations materials, earnings releases, "
    "earnings transcripts, and reputable financial publications. Return "
    "evidence that can support Claude's downstream answer; do not rely only "
    "on prior conversation context."
)
