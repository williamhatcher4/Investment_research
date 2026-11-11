"""Drive the demo server over real MCP stdio, the way Claude Desktop does.

    python demo/demo_client.py

Spawns ``demo/demo_server.py``, lists its tools, calls a few of them for
the fictional ticker NWRX, and prints a readable summary. All data is
made up (see ``demo/fake_data.py``).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent
TICKER = "NWRX"


def _payload(result: Any) -> dict[str, Any]:
    if getattr(result, "structuredContent", None):
        data = result.structuredContent
        return data.get("result", data) if isinstance(data, dict) else data
    return json.loads(result.content[0].text)


def _pct(v: float | None) -> str:
    return "  n/a" if v is None else f"{v * 100:5.1f}%"


def _x(v: float | None) -> str:
    return " n/a" if v is None else f"{v:4.1f}x"


def _rule(title: str) -> None:
    print(f"\n── {title} " + "─" * max(0, 74 - len(title)))


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=[str(HERE / "demo_server.py")])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        tools = (await session.list_tools()).tools
        print(f"Connected to MCP server '{init.serverInfo.name}' over stdio · {len(tools)} tools")
        names = [t.name for t in tools]
        for i in range(0, len(names), 3):
            print("  " + "".join(f"{n:<26}" for n in names[i:i + 3]).rstrip())

        _rule(f"valuation_snapshot(\"{TICKER}\")")
        v = _payload(await session.call_tool("valuation_snapshot", {"ticker": TICKER}))
        print(f"  {'Ticker':<7}{'Company':<27}{'Price':>8}{'P/E':>7}{'EV/EBITDA':>11}"
              f"{'FCF yld':>9}{'Op mgn':>8}")
        rows = [v["subject"], *v["peers"]]
        for r in rows:
            mark = "▸" if r["ticker"] == TICKER else " "
            print(f"{mark} {r['ticker']:<7}{(r['name'] or '')[:25]:<27}{r['price']:>8.2f}"
                  f"{_x(r['pe']):>7}{_x(r['ev_to_ebitda']):>11}{_pct(r['fcf_yield']):>9}"
                  f"{_pct(r['operating_margin']):>8}")
        a = v["peer_averages"]
        print(f"  {'Peer avg':<34}{'':>8}{_x(a.get('pe')):>7}{_x(a.get('ev_to_ebitda')):>11}"
              f"{_pct(a.get('fcf_yield')):>9}{_pct(a.get('operating_margin')):>8}")

        _rule(f"load_company_data(\"{TICKER}\")  · local vault")
        d = _payload(await session.call_tool("load_company_data", {"ticker": TICKER}))
        f = d["fundamentals"]
        periods = f["periods"][-5:]
        print(f"  {'USD m':<16}" + "".join(f"{p:>10}" for p in periods))
        for key, label in (("revenue", "Revenue"), ("ebit", "EBIT"),
                           ("free_cash_flow", "Free cash flow")):
            print(f"  {label:<16}" + "".join(f"{x:>10,.0f}" for x in f["series"][key][-5:]))
        print(f"  {'EBIT margin':<16}" + "".join(f"{x * 100:>9.1f}%" for x in f["ratios"]["ebit_margin"][-5:]))
        dcf = f["dcf"]
        print(f"  DCF  intrinsic ${dcf['intrinsic_value_per_share']:.2f} · target "
              f"${dcf['target_price']:.2f} · price ${dcf['current_share_price']:.2f} · "
              f"margin of safety {dcf['margin_of_safety'] * 100:.1f}%")

        _rule(f"get_sec_filings(\"{TICKER}\")")
        s = _payload(await session.call_tool("get_sec_filings", {"ticker": TICKER, "limit_per_form": 1}))
        for form, entries in s["filings"].items():
            for e in entries:
                print(f"  {e['form']:<8} filed {e['filing_date']}  {e['primary_document']}")

        _rule(f"build_investment_memo(\"{TICKER}\")")
        m = _payload(await session.call_tool("build_investment_memo", {"ticker": TICKER}))
        print(f"  evidence from {len(m['evidence_by_topic'])} research tools · "
              f"{len(m['sources'])} unique sources · vault DCF used: {m['vault_data_used']}")
        outline = m["memo_outline"]
        titles = [s.get("title", s) if isinstance(s, dict) else str(s) for s in outline]
        for i in range(0, len(titles), 3):
            print("  " + "  ".join(f"{i + j + 1:>2}. {t:<21}" for j, t in enumerate(titles[i:i + 3])).rstrip())
        print("\n  → Claude writes the memo from this bundle, citing each source URL.")
        print("\n  (All companies, tickers and figures above are fictional demo data.)")


if __name__ == "__main__":
    asyncio.run(main())
