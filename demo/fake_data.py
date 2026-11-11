"""Fictional data for the offline demo.

Every company, ticker, number and URL in this file is made up. The URLs
point at example.com so nothing here can be mistaken for a real source.
"""

from __future__ import annotations

from typing import Any

SUBJECT = "NWRX"

COMPANIES: dict[str, dict[str, Any]] = {
    "NWRX": {
        "name": "Northwind Robotics Inc.",
        "price": 84.20, "shares": 190.0, "revenue": 4150.0, "gross": 0.47,
        "op": 0.184, "net": 0.139, "ev_ebitda": 17.9, "fcf_yield": 0.041,
        "nd_ebitda": 0.6, "de": 0.38,
    },
    "CBLT": {
        "name": "Cobalt Automation Corp.",
        "price": 61.75, "shares": 260.0, "revenue": 5320.0, "gross": 0.41,
        "op": 0.152, "net": 0.108, "ev_ebitda": 15.2, "fcf_yield": 0.048,
        "nd_ebitda": 1.1, "de": 0.62,
    },
    "HLXA": {
        "name": "Helix Actuators Ltd.",
        "price": 132.40, "shares": 93.0, "revenue": 2240.0, "gross": 0.52,
        "op": 0.211, "net": 0.162, "ev_ebitda": 21.4, "fcf_yield": 0.033,
        "nd_ebitda": 0.2, "de": 0.21,
    },
    "VRDN": {
        "name": "Verdant Motion Systems",
        "price": 38.10, "shares": 278.0, "revenue": 6870.0, "gross": 0.36,
        "op": 0.118, "net": 0.081, "ev_ebitda": 12.6, "fcf_yield": 0.057,
        "nd_ebitda": 1.8, "de": 0.91,
    },
}

PEERS = {"NWRX": ["CBLT", "HLXA", "VRDN"]}


# ---- FMP-shaped responses ------------------------------------------------


def fmp_response(path: str, params: dict[str, Any] | None) -> Any:
    """Return what FMP's REST API would return for ``path``."""
    params = params or {}
    ticker = (params.get("symbol") or path.rstrip("/").split("/")[-1]).upper()
    c = COMPANIES.get(ticker)
    if c is None:
        return []
    if path.startswith("/stock_peers"):
        return [{"symbol": ticker, "peersList": PEERS.get(ticker, [])}]
    if path.startswith("/profile/"):
        return [{
            "symbol": ticker, "companyName": c["name"], "sector": "Industrials",
            "industry": "Industrial Automation & Robotics", "exchangeShortName": "NASDAQ",
            "description": f"{c['name']} is a fictional company used for demos.",
        }]
    if path.startswith("/quote/"):
        mcap = c["price"] * c["shares"] * 1e6
        eps = c["revenue"] * c["net"] / c["shares"]
        return [{"symbol": ticker, "price": c["price"], "marketCap": mcap,
                 "eps": round(eps, 2), "pe": round(c["price"] / eps, 1)}]
    if path.startswith("/income-statement/"):
        rows = []
        for i, year in enumerate((2025, 2024, 2023, 2022)):
            rev = c["revenue"] / (1.14 ** i) * 1e6
            rows.append({
                "date": f"{year}-12-31", "period": "FY", "revenue": round(rev),
                "grossProfit": round(rev * c["gross"]),
                "operatingIncome": round(rev * c["op"]),
                "netIncome": round(rev * c["net"]),
                "eps": round(rev * c["net"] / (c["shares"] * 1e6), 2),
            })
        return rows
    if path.startswith("/balance-sheet-statement/"):
        return [{"date": "2025-12-31", "cashAndCashEquivalents": 1.21e9,
                 "totalDebt": 1.68e9, "totalEquity": 4.42e9}]
    if path.startswith("/key-metrics-ttm/"):
        return [{"enterpriseValueOverEBITDATTM": c["ev_ebitda"],
                 "freeCashFlowYieldTTM": c["fcf_yield"],
                 "netDebtToEBITDATTM": c["nd_ebitda"],
                 "debtToEquityTTM": c["de"], "roeTTM": 0.17}]
    return []


# ---- SEC EDGAR-shaped responses -------------------------------------------

_CIK = 9990001


def edgar_response(url: str) -> Any:
    if url.endswith("company_tickers.json"):
        return {"0": {"cik_str": _CIK, "ticker": SUBJECT, "title": COMPANIES[SUBJECT]["name"]}}
    return {
        "name": COMPANIES[SUBJECT]["name"].upper(),
        "filings": {"recent": {
            "form": ["8-K", "10-Q", "10-Q", "DEF 14A", "10-K", "8-K", "10-Q"],
            "accessionNumber": [f"0009990001-26-0000{i:02d}" for i in range(7, 0, -1)],
            "filingDate": ["2026-10-29", "2026-10-29", "2026-07-30", "2026-04-17",
                           "2026-02-24", "2026-02-11", "2025-10-30"],
            "reportDate": ["2026-10-29", "2026-09-30", "2026-06-30", "",
                           "2025-12-31", "2026-02-11", "2025-09-30"],
            "primaryDocument": ["nwrx-8k.htm", "nwrx-10q.htm", "nwrx-10q.htm",
                                "nwrx-def14a.htm", "nwrx-10k.htm", "nwrx-8k.htm",
                                "nwrx-10q.htm"],
        }},
    }


# ---- Tavily-shaped search responses ---------------------------------------

_SNIPPETS = [
    ("Northwind Robotics Q3 2026 results", "news.example.com",
     "Revenue rose 15% YoY to $1.12B; operating margin expanded 120bp to 19.1% "
     "on mix shift toward software-attached fleet contracts."),
    ("Northwind 10-K — Item 1A Risk Factors", "filings.example.com",
     "Key risks include customer concentration (top 5 customers = 31% of revenue), "
     "dependence on a single servo-motor supplier, and export controls."),
    ("Industrial robotics demand outlook 2027", "research.example.com",
     "Warehouse automation orders expected to grow high-single digits; "
     "Cobalt Automation and Helix Actuators cited as primary competitors."),
    ("Northwind raises FY2026 guidance", "wire.example.com",
     "Management raised FY2026 revenue guidance to $4.55–4.65B and announced a "
     "$500M buyback authorization."),
]


def tavily_response(query: str, **_: Any) -> dict[str, Any]:
    return {
        "query": query,
        "answer": (
            "Northwind Robotics (fictional) is a mid-cap industrial automation "
            "company growing ~14% a year with expanding margins; main debates are "
            "customer concentration and valuation versus peers."
        ),
        "results": [
            {"title": t, "url": f"https://{host}/nwrx/{i}", "content": body,
             "score": round(0.94 - i * 0.07, 2), "published_date": "2026-10-30"}
            for i, (t, host, body) in enumerate(_SNIPPETS)
        ],
        "response_time": 0.01,
    }


class FakeTavilySDK:
    def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        return tavily_response(query, **kwargs)


# ---- Vault fundamentals (stand-in for a parsed Bloomberg export) ----------


def vault_snapshot_kwargs() -> dict[str, Any]:
    periods = [f"FY{y}" for y in range(2016, 2026)]
    revenue, r = [], 1235.0
    for _ in periods:
        revenue.append(round(r, 1))
        r *= 1.144
    ebit_margin = [0.112, 0.118, 0.121, 0.109, 0.131, 0.142, 0.151, 0.163, 0.176, 0.184]
    ebit = [round(a * b, 1) for a, b in zip(revenue, ebit_margin)]
    net = [round(e * 0.76, 1) for e in ebit]
    fcf = [round(n * 0.92, 1) for n in net]
    return {
        "ticker": SUBJECT,
        "company_name": COMPANIES[SUBJECT]["name"],
        "currency": "USD",
        "units": "millions",
        "fiscal_year_end": "December",
        "periods": periods,
        "series": {"revenue": revenue, "ebit": ebit, "net_income": net,
                   "free_cash_flow": fcf},
        "ratios": {"ebit_margin": ebit_margin,
                   "net_margin": [round(n / rv, 3) for n, rv in zip(net, revenue)]},
        "dcf": {"intrinsic_value_per_share": 97.60, "target_price": 102.00,
                "current_share_price": 84.20, "margin_of_safety": 0.137,
                "discount_rate": 0.09, "terminal_growth_rate": 0.03},
        "metadata": {"sector": "Industrials",
                     "industry": "Industrial Automation & Robotics",
                     "exchange": "NASDAQ"},
        "source_file": "demo/fictional_export.xlsx",
    }
