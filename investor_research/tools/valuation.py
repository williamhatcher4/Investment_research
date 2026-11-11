"""Phase 2 valuation tools: compare_peers and valuation_snapshot."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from statistics import mean
from typing import Any

from investor_research.clients.fmp import FmpClient, FmpSnapshot
from investor_research.clients.research_provider import make_research_provider
from investor_research.config import Settings, get_settings
from investor_research.prompts.research import COMPARE_PEERS_QUERY, VALUATION_PEERS_QUERY
from investor_research.source_quality import build_evidence_claims, enrich_sources
from investor_research.tools.research import _validate_ticker

logger = logging.getLogger(__name__)


# ---- compare_peers -----------------------------------------------------


def compare_peers(tickers: list[str]) -> dict[str, Any]:
    """Side-by-side comparison across revenue growth, margins, valuation,
    capital allocation, and competitive moat.
    """
    validated = _validate_ticker_list(tickers)
    settings = get_settings()

    fmp_by_ticker = _parallel_fmp_snapshots(
        settings,
        validated,
        include_balance=False,
        include_peers=False,
    )

    provider = make_research_provider(settings)
    query = COMPARE_PEERS_QUERY.format(tickers_csv=", ".join(validated))
    evidence = provider.search(query, max_results=10)

    rows = [_valuation_row(t, fmp_by_ticker[t]) for t in validated]

    return {
        "tickers": validated,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "provider": settings.research_provider,
        "structured_rows": rows,
        "query": evidence.query,
        "answer": evidence.answer,
        "results": [r.model_dump(mode="json") for r in evidence.results],
        "sources": [s.model_dump(mode="json") for s in enrich_sources(evidence.sources)],
        "evidence_claims": build_evidence_claims(evidence.results, evidence.sources),
    }


# ---- valuation_snapshot ------------------------------------------------


def valuation_snapshot(ticker: str, peer_set: str | None = None) -> dict[str, Any]:
    """Return current valuation multiples for ``ticker`` plus peer averages."""
    ticker = _validate_ticker(ticker)
    settings = get_settings()
    fmp = FmpClient(settings)
    snapshot = fmp.fetch_snapshot(ticker)

    peer_tickers = _peer_tickers(settings, snapshot, peer_set)[:5]
    peer_snapshots: dict[str, FmpSnapshot] = {}
    if peer_tickers:
        peer_snapshots = _parallel_fmp_snapshots(
            settings,
            peer_tickers,
            include_balance=False,
            include_peers=False,
        )

    own_row = _valuation_row(ticker, snapshot)
    peer_rows = [_valuation_row(t, peer_snapshots[t]) for t in peer_tickers]
    peer_avg = _average_row(peer_rows)

    provider = make_research_provider(settings)
    query = VALUATION_PEERS_QUERY.format(ticker=ticker)
    evidence = provider.search(query, max_results=8)

    return {
        "ticker": ticker,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "provider": settings.research_provider,
        "subject": own_row,
        "peers": peer_rows,
        "peer_averages": peer_avg,
        "peer_set": peer_set,
        "query": evidence.query,
        "answer": evidence.answer,
        "sources": [s.model_dump(mode="json") for s in enrich_sources(evidence.sources)],
        "results": [r.model_dump(mode="json") for r in evidence.results],
        "evidence_claims": build_evidence_claims(evidence.results, evidence.sources),
        "notes": _snapshot_notes(snapshot, peer_tickers),
    }


def dcf_scenario_analysis(
    ticker: str,
    *,
    bear_adjustment: float = -0.20,
    bull_adjustment: float = 0.20,
) -> dict[str, Any]:
    """Return a simple DCF sensitivity table from the vault's DCF outputs.

    The imported workbook remains the source of truth. This tool creates
    deterministic bear/base/bull scenarios around the parsed target price and
    intrinsic value so Claude can discuss valuation risk without inventing
    spreadsheet math.
    """
    ticker = _validate_ticker(ticker)
    settings = get_settings()
    snapshot = _load_vault_snapshot(settings, ticker)
    if snapshot is None or not snapshot.dcf:
        return {
            "ticker": ticker,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "error": "NO_VAULT_DCF",
            "message": "No parsed vault DCF available for this ticker.",
        }
    dcf = snapshot.dcf
    target = _first_num(dcf.get("target_price"), dcf.get("intrinsic_value_per_share"))
    intrinsic = _first_num(dcf.get("intrinsic_value_per_share"), target)
    current = _num(dcf.get("current_share_price"))
    scenarios = []
    for name, adjustment in (("bear", bear_adjustment), ("base", 0.0), ("bull", bull_adjustment)):
        scenario_target = target * (1 + adjustment) if target is not None else None
        scenario_intrinsic = intrinsic * (1 + adjustment) if intrinsic is not None else None
        scenarios.append(
            {
                "scenario": name,
                "target_price": scenario_target,
                "intrinsic_value_per_share": scenario_intrinsic,
                "upside_to_current": _ratio_delta(scenario_target, current),
                "margin_of_safety": _ratio_delta(scenario_intrinsic, current),
            }
        )
    return {
        "ticker": ticker,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "ok": True,
        "source": "vault",
        "parsed_at": snapshot.parsed_at.isoformat(),
        "base_dcf": dcf,
        "scenario_method": (
            f"Flat {bear_adjustment:+.0%} / {bull_adjustment:+.0%} adjustment to the "
            "workbook's target price and intrinsic value (not a re-run of the model)."
        ),
        "scenarios": scenarios,
        "reverse_dcf": _reverse_dcf_from_vault(settings, ticker, snapshot, current),
    }


def build_dcf(
    ticker: str,
    *,
    base_fcf_per_share: float | None = None,
    growth_rate_yr1_5: float | None = None,
    growth_rate_yr6_10: float | None = None,
    discount_rate: float = 0.09,
    terminal_growth_rate: float = 0.03,
) -> dict[str, Any]:
    """Build a 10-year per-share DCF for ``ticker`` and return intrinsic value.

    Data sourcing priority:
    - base_fcf_per_share: vault FCF series → FMP cash flow statement →
      FMP FCF yield × price → parameter override
    - growth rates: parameter override → watchlist historical FCF/share growth →
      vault DCF consensus growth → no default (error if unavailable)
    - discount_rate, terminal_growth_rate: parameter (defaults 9%, 3%)

    Returns a full projection table plus intrinsic value per share, upside to
    current price, and a note on each data source used.
    """
    ticker = _validate_ticker(ticker)
    if discount_rate <= terminal_growth_rate:
        return {
            "ticker": ticker,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "error": "INVALID_RATES",
            "message": "discount_rate must be strictly greater than terminal_growth_rate.",
        }
    settings = get_settings()
    notes: list[str] = []

    fmp = FmpClient(settings)
    snapshot = fmp.fetch_snapshot(
        ticker,
        include_profile=False,
        include_income=False,
        include_balance=False,
        include_peers=False,
        include_cashflow=True,
    )

    vault_snapshot = _load_vault_snapshot(settings, ticker)
    watchlist = _load_company_watchlist_data(settings, ticker)

    # ---- base FCF per share ------------------------------------------------
    fcf_ps, fcf_source = _resolve_base_fcf_ps(
        base_fcf_per_share, snapshot, vault_snapshot, watchlist
    )
    if fcf_ps is None:
        return {
            "ticker": ticker,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "error": "NO_BASE_FCF",
            "message": (
                "Could not determine base FCF per share. Provide base_fcf_per_share "
                "explicitly, or ensure FMP_API_KEY is set / vault data is imported."
            ),
        }
    notes.append(f"Base FCF per share sourced from: {fcf_source}")

    # ---- growth rates -------------------------------------------------------
    g1, g2, growth_source = _resolve_growth_rates(
        growth_rate_yr1_5, growth_rate_yr6_10, watchlist, vault_snapshot
    )
    if g1 is None:
        return {
            "ticker": ticker,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "error": "NO_GROWTH_RATE",
            "message": (
                "Could not determine FCF growth rates. Provide growth_rate_yr1_5 "
                "explicitly, or ensure watchlist data has been imported via "
                "import_watchlist_xlsx_export."
            ),
        }
    notes.append(f"Growth rates sourced from: {growth_source}")

    # ---- sanity checks on model inputs ------------------------------------------
    input_error = _dcf_input_error(fcf_ps, g1, g2)
    if input_error is not None:
        code, message = input_error
        return {
            "ticker": ticker,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "error": code,
            "message": message,
            "base_fcf_per_share": fcf_ps,
            "base_fcf_source": fcf_source,
            "growth_rate_yr1_5": g1,
            "growth_rate_yr6_10": g2,
            "growth_rate_source": growth_source,
        }

    # ---- current price & shares --------------------------------------------
    current_price = _num((snapshot.quote or {}).get("price"))
    if current_price is None and watchlist:
        current_price = _num(watchlist.get("price"))
        if current_price is not None:
            notes.append("Current price from watchlist snapshot (may be stale).")

    # ---- DCF calculation ---------------------------------------------------
    dcf = _compute_dcf_per_share(fcf_ps, g1, g2, discount_rate, terminal_growth_rate)
    intrinsic = dcf["intrinsic_value_per_share"]
    upside = _ratio_delta(intrinsic, current_price)
    mos = upside  # same formula: intrinsic/price - 1
    implied_g1 = (
        _implied_growth_rate(fcf_ps, current_price, discount_rate, terminal_growth_rate)
        if current_price is not None
        else None
    )

    return {
        "ticker": ticker,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "ok": True,
        "base_fcf_per_share": round(fcf_ps, 4),
        "base_fcf_source": fcf_source,
        "current_price": current_price,
        "inputs": {
            "growth_rate_yr1_5": g1,
            "growth_rate_yr6_10": g2,
            "growth_rate_source": growth_source,
            "discount_rate": discount_rate,
            "terminal_growth_rate": terminal_growth_rate,
        },
        **dcf,
        "intrinsic_value_per_share": intrinsic,
        "upside_to_current": upside,
        "margin_of_safety": mos,
        "reverse_dcf": {
            "implied_growth_rate_yr1_5": implied_g1,
            "method": "Solves for the years 1-5 growth rate (years 6-10 at 70% of it) "
            "that makes this DCF equal the current price.",
        },
        "notes": notes,
    }


def batch_dcf_screen(
    *,
    top_n: int = 10,
    discount_rate: float = 0.09,
    terminal_growth_rate: float = 0.03,
    tickers: list[str] | None = None,
) -> dict[str, Any]:
    """Run a DCF across all watchlist companies and rank by upside.

    For each company:
    - Base FCF per share: vault → FMP cash flow statement → FMP FCF yield × price
    - Growth rates: watchlist historical FCF/share growth rates
    - Discount rate and terminal growth rate: shared inputs (defaults 9%, 3%)

    Returns the top ``top_n`` companies sorted by upside (intrinsic / price - 1),
    with full inputs and data-source notes per row.
    """
    if discount_rate <= terminal_growth_rate:
        return {
            "ok": False,
            "error": "INVALID_RATES",
            "message": "discount_rate must be strictly greater than terminal_growth_rate.",
        }
    settings = get_settings()
    if settings.vault_root is None:
        return {
            "ok": False,
            "error": "VAULT_NOT_CONFIGURED",
            "message": "VAULT_ROOT must be set to load watchlist data.",
        }

    from investor_research.vault.snapshots import load_watchlist_snapshot as _load_ws

    ws = _load_ws(settings)
    if ws is None:
        return {
            "ok": False,
            "error": "NO_WATCHLIST",
            "message": (
                "No watchlist snapshot found. Run import_watchlist_xlsx_export first."
            ),
        }

    rows = [r for r in ws.rows if r.normalized_ticker]
    if tickers:
        upper = {t.upper() for t in tickers}
        rows = [r for r in rows if (r.normalized_ticker or "").upper() in upper]

    if not rows:
        return {
            "ok": False,
            "error": "NO_ROWS",
            "message": "No resolved watchlist rows found for the requested tickers.",
        }

    # Parallel FMP fetch: quote + key_metrics + cash_flow for all tickers
    all_tickers = [r.normalized_ticker for r in rows]  # already non-None
    fmp_snapshots = _parallel_fmp_snapshots(
        settings,
        all_tickers,
        include_profile=False,
        include_income=False,
        include_balance=False,
        include_peers=False,
        include_cashflow=True,
    )

    results: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for row in rows:
        t = row.normalized_ticker
        fmp_snap = fmp_snapshots.get(t, FmpSnapshot(ticker=t))
        watchlist_dict = row.model_dump(mode="json")
        vault_snap = _load_vault_snapshot(settings, t)

        fcf_ps, fcf_source = _resolve_base_fcf_ps(None, fmp_snap, vault_snap, watchlist_dict)
        if fcf_ps is None:
            failed.append({"ticker": t, "reason": "NO_BASE_FCF"})
            continue

        g1, g2, growth_source = _resolve_growth_rates(None, None, watchlist_dict, vault_snap)
        if g1 is None:
            failed.append({"ticker": t, "reason": "NO_GROWTH_RATE"})
            continue

        input_error = _dcf_input_error(fcf_ps, g1, g2)
        if input_error is not None:
            failed.append({"ticker": t, "reason": input_error[0], "message": input_error[1]})
            continue

        dcf = _compute_dcf_per_share(fcf_ps, g1, g2, discount_rate, terminal_growth_rate)
        intrinsic = dcf["intrinsic_value_per_share"]

        current_price = _first_num((fmp_snap.quote or {}).get("price"), row.price)
        upside = _ratio_delta(intrinsic, current_price)

        results.append({
            "ticker": t,
            "company_name": row.long_name,
            "sector": row.sector,
            "current_price": current_price,
            "intrinsic_value_per_share": intrinsic,
            "upside_pct": round(upside * 100, 1) if upside is not None else None,
            "margin_of_safety": upside,
            "base_fcf_per_share": round(fcf_ps, 4),
            "base_fcf_source": fcf_source,
            "growth_rate_yr1_5": g1,
            "growth_rate_yr6_10": g2,
            "growth_rate_source": growth_source,
        })

    results.sort(
        key=lambda r: r["upside_pct"] if r["upside_pct"] is not None else -999,
        reverse=True,
    )

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "ok": True,
        "snapshot_date": ws.snapshot_date,
        "discount_rate": discount_rate,
        "terminal_growth_rate": terminal_growth_rate,
        "rows_total": len(rows),
        "rows_with_dcf": len(results),
        "rows_failed": len(failed),
        "top_n": top_n,
        "results": results[:top_n],
        "failed": failed,
        "notes": [
            "Intrinsic value is a per-share DCF using historical FCF growth as a base rate.",
            "Growth rates are NOT forward forecasts — use research_follow_up per company to refine.",
            f"Discount rate: {discount_rate:.1%} | Terminal growth: {terminal_growth_rate:.1%}",
        ],
    }


def data_cross_check(ticker: str) -> dict[str, Any]:
    """Compare key vault fundamentals against FMP as a sanity check."""
    ticker = _validate_ticker(ticker)
    settings = get_settings()
    vault_snapshot = _load_vault_snapshot(settings, ticker)
    fmp_snapshot = FmpClient(settings).fetch_snapshot(
        ticker,
        include_profile=False,
        include_metrics=False,
        include_peers=False,
    )
    rows: list[dict[str, Any]] = []

    if vault_snapshot is not None:
        latest_idx = len(vault_snapshot.periods) - 1
        scale = _vault_scale(vault_snapshot.units)
        fmp_income = (fmp_snapshot.income_statement or [{}])[0] if fmp_snapshot.income_statement else {}
        fmp_balance = (fmp_snapshot.balance_sheet or [{}])[0] if fmp_snapshot.balance_sheet else {}
        rows.extend(
            [
                _cross_check_row("revenue", _series_value(vault_snapshot.series, "revenue", latest_idx, scale), _safe_get(fmp_income, "revenue")),
                _cross_check_row("net_income", _series_value(vault_snapshot.series, "net_income", latest_idx, scale), _safe_get(fmp_income, "netIncome")),
                _cross_check_row("total_debt", _series_value(vault_snapshot.series, "total_debt", latest_idx, scale), _safe_get(fmp_balance, "totalDebt")),
                _cross_check_row("shares_outstanding", _series_value(vault_snapshot.series, "shares_outstanding", latest_idx, scale), _safe_get(fmp_snapshot.quote or {}, "sharesOutstanding")),
            ]
        )
    return {
        "ticker": ticker,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "vault_available": vault_snapshot is not None,
        "fmp_available": fmp_snapshot.has_data(),
        "rows": [row for row in rows if row["vault_value"] is not None or row["fmp_value"] is not None],
        "notes": [
            "Differences can reflect fiscal-period mismatch, units, restatements, or provider normalization."
        ],
    }


# ---- helpers -----------------------------------------------------------


def _load_company_watchlist_data(settings: Settings, ticker: str) -> dict[str, Any] | None:
    if settings.vault_root is None:
        return None
    try:
        from investor_research.vault.snapshots import load_company_watchlist

        return load_company_watchlist(settings, ticker)
    except Exception:  # noqa: BLE001
        return None


def _resolve_base_fcf_ps(
    override: float | None,
    fmp_snap: FmpSnapshot,
    vault_snap: Any,
    watchlist: dict[str, Any] | None,
) -> tuple[float | None, str]:
    """Return (fcf_per_share, source_label) from the highest-quality source."""
    if override is not None:
        return override, "provided"

    # 1. Vault fundamentals series (most recent period)
    if vault_snap is not None and vault_snap.series:
        fcf_series = vault_snap.series.get("free_cash_flow") or []
        shares_series = vault_snap.series.get("shares_outstanding") or []
        scale = _vault_scale(vault_snap.units)
        # Walk backwards to find the most recent non-None pair
        for i in range(len(fcf_series) - 1, -1, -1):
            fcf_raw = fcf_series[i]
            if fcf_raw is None:
                continue
            shares_raw = shares_series[i] if i < len(shares_series) else None
            if isinstance(shares_raw, (int, float)) and shares_raw > 0:
                return (fcf_raw * scale) / (shares_raw * scale), "vault_financials"

    # 2. FMP cash flow statement
    if fmp_snap.cash_flow_statement:
        cf = fmp_snap.cash_flow_statement[0]
        fcf = _num(cf.get("freeCashFlow"))
        if fcf is not None:
            shares = _num((fmp_snap.quote or {}).get("sharesOutstanding"))
            if shares and shares > 0:
                return fcf / shares, "fmp_cash_flow_statement"

    # 3. FMP FCF yield × price  (FCF yield = FCF/market_cap = FCF_ps/price)
    metrics = fmp_snap.key_metrics_ttm or {}
    fcf_yield = _num(metrics.get("freeCashFlowYieldTTM"))
    price = _num((fmp_snap.quote or {}).get("price"))
    if fcf_yield is not None and price and price > 0:
        return fcf_yield * price, "fmp_fcf_yield_x_price"

    # 4. Watchlist FY1/FY2 FCF per share (consensus forward estimate from workbook)
    if watchlist:
        wl_metrics = watchlist.get("metrics") or {}
        fy1 = _num(wl_metrics.get("fcf_per_share_fy1"))
        if fy1 is not None and fy1 > 0:
            return fy1, "watchlist_fcf_per_share_fy1"
        fy2 = _num(wl_metrics.get("fcf_per_share_fy2"))
        if fy2 is not None and fy2 > 0:
            return fy2, "watchlist_fcf_per_share_fy2"

    return None, "unavailable"


def _resolve_growth_rates(
    override_g1: float | None,
    override_g2: float | None,
    watchlist: dict[str, Any] | None,
    vault_snap: Any,
) -> tuple[float | None, float | None, str]:
    """Return (growth_yr1_5, growth_yr6_10, source_label).

    Overrides are used as given (decimals, e.g. 0.12 for 12%). Rates read from
    workbooks are converted from percent to decimal when they look like
    percentages (|value| > 1), and the source label says so.
    """
    if override_g1 is not None:
        g2 = override_g2 if override_g2 is not None else override_g1 * _LATE_GROWTH_FADE
        return override_g1, g2, "provided"

    candidates: list[tuple[str, Any, Any]] = []
    if watchlist:
        metrics = watchlist.get("metrics") or {}
        candidates.append((
            "watchlist_fcf_per_share_growth",
            metrics.get("fcf_per_share_growth_5y"),
            metrics.get("fcf_per_share_growth_10y"),
        ))
        candidates.append(("watchlist_revenue_growth_proxy", metrics.get("revenue_growth_5y"), None))
    if vault_snap is not None and vault_snap.dcf:
        candidates.append((
            "vault_dcf_growth_projection",
            _first_num(
                vault_snap.dcf.get("fcf_growth_projection_10yr"),
                vault_snap.dcf.get("fcfps_growth_5yr"),
            ),
            None,
        ))

    for label, raw_g1, raw_g2 in candidates:
        g1, converted = _as_decimal_rate(_num(raw_g1))
        if g1 is None:
            continue
        g2, converted_g2 = _as_decimal_rate(_num(raw_g2))
        if g2 is None:
            g2 = g1 * _LATE_GROWTH_FADE
        if converted or converted_g2:
            label += " (converted from percent)"
        return g1, g2, label

    return None, None, "unavailable"


def _compute_dcf_per_share(
    base_fcf_ps: float,
    growth_yr1_5: float,
    growth_yr6_10: float,
    discount_rate: float,
    terminal_growth: float,
) -> dict[str, Any]:
    """Core 10-year per-share DCF calculation (two-stage growth + Gordon terminal)."""
    projections: list[dict[str, Any]] = []
    fcf = base_fcf_ps
    pv_fcf_sum = 0.0
    for yr in range(1, 11):
        g = growth_yr1_5 if yr <= 5 else growth_yr6_10
        fcf = fcf * (1 + g)
        pv = fcf / (1 + discount_rate) ** yr
        pv_fcf_sum += pv
        projections.append({
            "year": yr,
            "fcf_per_share": round(fcf, 4),
            "pv_fcf": round(pv, 4),
        })

    terminal_value_ps = fcf * (1 + terminal_growth) / (discount_rate - terminal_growth)
    pv_terminal_ps = terminal_value_ps / (1 + discount_rate) ** 10
    intrinsic = pv_fcf_sum + pv_terminal_ps

    return {
        "year_projections": projections,
        "pv_fcf_sum": round(pv_fcf_sum, 4),
        "terminal_value_per_share": round(terminal_value_ps, 4),
        "pv_terminal_value_per_share": round(pv_terminal_ps, 4),
        "intrinsic_value_per_share": round(intrinsic, 4),
    }


# Years 6-10 growth defaults to this fraction of years 1-5 growth.
_LATE_GROWTH_FADE = 0.7
# Plausible bounds for an annual FCF growth assumption (as decimals).
_MIN_GROWTH, _MAX_GROWTH = -0.5, 0.5


def _as_decimal_rate(value: float | None) -> tuple[float | None, bool]:
    """Return (rate_as_decimal, was_converted). Treats |value| > 1 as a percent."""
    if value is None:
        return None, False
    if abs(value) > 1:
        return value / 100.0, True
    return value, False


def _dcf_input_error(
    base_fcf_ps: float, growth_yr1_5: float, growth_yr6_10: float
) -> tuple[str, str] | None:
    """Return (error_code, message) when inputs would make the DCF meaningless."""
    if base_fcf_ps <= 0:
        return (
            "NON_POSITIVE_BASE_FCF",
            f"Base FCF per share is {base_fcf_ps:.4g}; a growth DCF on zero or "
            "negative free cash flow is not meaningful.",
        )
    for label, g in (("growth_rate_yr1_5", growth_yr1_5), ("growth_rate_yr6_10", growth_yr6_10)):
        if not _MIN_GROWTH <= g <= _MAX_GROWTH:
            return (
                "GROWTH_OUT_OF_RANGE",
                f"{label}={g:.4g} is outside the plausible range "
                f"[{_MIN_GROWTH:.0%}, {_MAX_GROWTH:.0%}]. Pass rates as decimals (0.12 = 12%).",
            )
    return None


def _implied_growth_rate(
    base_fcf_ps: float,
    price: float,
    discount_rate: float,
    terminal_growth: float,
    *,
    fade: float = _LATE_GROWTH_FADE,
) -> float | None:
    """Reverse DCF: the years 1-5 growth rate at which intrinsic value == price.

    Years 6-10 grow at ``fade`` x that rate. Solved by bisection, since
    intrinsic value rises monotonically with growth. Returns ``None`` when
    the price falls outside what growth in [-50%, +50%] can explain.
    """
    if base_fcf_ps <= 0 or price is None or price <= 0 or discount_rate <= terminal_growth:
        return None

    def value_at(g: float) -> float:
        return _compute_dcf_per_share(
            base_fcf_ps, g, g * fade, discount_rate, terminal_growth
        )["intrinsic_value_per_share"]

    lo, hi = _MIN_GROWTH, _MAX_GROWTH
    if not value_at(lo) <= price <= value_at(hi):
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if value_at(mid) < price:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 4)


def _reverse_dcf_from_vault(
    settings: Settings, ticker: str, snapshot: Any, current: float | None
) -> dict[str, Any]:
    """Implied growth at the current price, using the vault's FCF and DCF rates."""
    watchlist = _load_company_watchlist_data(settings, ticker)
    fcf_ps, fcf_source = _resolve_base_fcf_ps(None, FmpSnapshot(ticker=ticker), snapshot, watchlist)
    discount = _first_num(snapshot.dcf.get("discount_rate"), 0.09)
    terminal = _first_num(snapshot.dcf.get("terminal_growth_rate"), 0.03)
    discount, _ = _as_decimal_rate(discount)
    terminal, _ = _as_decimal_rate(terminal)
    implied = (
        _implied_growth_rate(fcf_ps, current, discount, terminal)
        if fcf_ps is not None and current is not None
        else None
    )
    if implied is None:
        return {
            "available": False,
            "message": "Need positive base FCF per share, a current price, and a price "
            "reachable with growth between -50% and +50%.",
        }
    return {
        "available": True,
        "implied_growth_rate_yr1_5": implied,
        "base_fcf_per_share": round(fcf_ps, 4),
        "base_fcf_source": fcf_source,
        "discount_rate": discount,
        "terminal_growth_rate": terminal,
        "method": "Solves for the years 1-5 growth rate (years 6-10 at 70% of it) "
        "that makes the DCF equal the current price.",
    }


def _validate_ticker_list(tickers: list[str]) -> list[str]:
    if not isinstance(tickers, list) or not tickers:
        raise ValueError("tickers must be a non-empty list of symbols")
    if len(tickers) > 8:
        raise ValueError("at most 8 tickers per compare_peers call")
    return [_validate_ticker(t) for t in tickers]


def _parallel_fmp_snapshots(
    settings: Settings,
    tickers: list[str],
    **fetch_kwargs: Any,
) -> dict[str, FmpSnapshot]:
    fmp = FmpClient(settings)
    out: dict[str, FmpSnapshot] = {}
    with ThreadPoolExecutor(max_workers=min(len(tickers), 5)) as pool:
        futures = {pool.submit(fmp.fetch_snapshot, t, **fetch_kwargs): t for t in tickers}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                out[ticker] = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.warning("fmp.snapshot_failed", extra={"ticker": ticker, "err": str(exc)[:200]})
                out[ticker] = FmpSnapshot(ticker=ticker)
    return out


def _peer_tickers(settings: Settings, snapshot: FmpSnapshot, peer_set: str | None) -> list[str]:
    if peer_set and settings.vault_root is not None:
        try:
            from investor_research.tools.vault_tools import load_peer_set

            peers = load_peer_set(settings, peer_set)
            if peers:
                return peers
        except Exception:  # noqa: BLE001 - fall back to FMP peers
            pass
    return snapshot.peers or []


def _valuation_row(ticker: str, snapshot: FmpSnapshot) -> dict[str, Any]:
    quote = snapshot.quote or {}
    metrics = snapshot.key_metrics_ttm or {}
    income = (snapshot.income_statement or [{}])[0] if snapshot.income_statement else {}
    revenue = _safe_get(income, "revenue")
    gross_profit = _safe_get(income, "grossProfit")
    operating_income = _safe_get(income, "operatingIncome")
    net_income = _safe_get(income, "netIncome")

    return {
        "ticker": ticker,
        "name": _safe_get(snapshot.profile or {}, "companyName"),
        "price": _safe_get(quote, "price"),
        "market_cap": _safe_get(quote, "marketCap"),
        "pe": _safe_get(quote, "pe"),
        "ev_to_ebitda": _safe_get(metrics, "enterpriseValueOverEBITDATTM"),
        "fcf_yield": _safe_get(metrics, "freeCashFlowYieldTTM"),
        "net_debt_to_ebitda": _safe_get(metrics, "netDebtToEBITDATTM"),
        "debt_to_equity": _safe_get(metrics, "debtToEquityTTM"),
        "gross_margin": _ratio(gross_profit, revenue),
        "operating_margin": _ratio(operating_income, revenue),
        "net_margin": _ratio(net_income, revenue),
        "revenue_ttm": revenue,
        "has_fmp_data": snapshot.has_data(),
    }


def _average_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    numeric_fields = (
        "pe",
        "ev_to_ebitda",
        "fcf_yield",
        "net_debt_to_ebitda",
        "debt_to_equity",
        "gross_margin",
        "operating_margin",
        "net_margin",
    )
    averages: dict[str, Any] = {}
    for field_name in numeric_fields:
        values = [r[field_name] for r in rows if isinstance(r.get(field_name), (int, float))]
        averages[field_name] = mean(values) if values else None
    averages["n_peers"] = len(rows)
    return averages


def _safe_get(d: dict[str, Any], key: str) -> Any:
    value = d.get(key)
    if isinstance(value, (int, float)) or isinstance(value, str):
        return value
    return None


def _ratio(numerator: Any, denominator: Any) -> float | None:
    if not isinstance(numerator, (int, float)) or not isinstance(denominator, (int, float)):
        return None
    if denominator == 0:
        return None
    return numerator / denominator


def _snapshot_notes(snapshot: FmpSnapshot, peer_tickers: list[str]) -> list[str]:
    notes: list[str] = []
    if not snapshot.has_data():
        notes.append("FMP data unavailable for subject ticker.")
    if not peer_tickers:
        notes.append("No FMP peer list available — peer averages omitted.")
    return notes


def _load_vault_snapshot(settings: Settings, ticker: str):
    if settings.vault_root is None:
        return None
    try:
        from investor_research.vault.snapshots import load_parsed_json

        return load_parsed_json(settings, ticker)
    except Exception:  # noqa: BLE001
        return None


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, (int, float)) else None


def _first_num(*values: Any) -> float | None:
    """First argument that is a real number (0 counts; None/strings do not)."""
    for value in values:
        number = _num(value)
        if number is not None:
            return number
    return None


def _ratio_delta(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator - 1


def _vault_scale(units: str | None) -> float:
    if units == "millions":
        return 1_000_000.0
    if units == "thousands":
        return 1_000.0
    return 1.0


def _series_value(
    series: dict[str, list[float | None]],
    key: str,
    idx: int,
    scale: float,
) -> float | None:
    values = series.get(key) or []
    if idx < 0 or idx >= len(values):
        return None
    value = values[idx]
    return value * scale if isinstance(value, (int, float)) else None


def _cross_check_row(metric: str, vault_value: Any, fmp_value: Any) -> dict[str, Any]:
    diff_pct = None
    if isinstance(vault_value, (int, float)) and isinstance(fmp_value, (int, float)) and fmp_value:
        diff_pct = (vault_value - fmp_value) / abs(fmp_value)
    return {
        "metric": metric,
        "vault_value": vault_value,
        "fmp_value": fmp_value,
        "difference_pct": diff_pct,
        "flag": abs(diff_pct) > 0.05 if diff_pct is not None else False,
    }
