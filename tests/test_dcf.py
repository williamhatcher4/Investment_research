"""Unit tests for the per-share DCF math and its input guards."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from investor_research.clients.fmp import FmpSnapshot
from investor_research.tools import valuation as v


def _hand_dcf(fcf, g1, g2, r, tg):
    pv = 0.0
    for year in range(1, 11):
        fcf *= 1 + (g1 if year <= 5 else g2)
        pv += fcf / (1 + r) ** year
    return pv + fcf * (1 + tg) / (r - tg) / (1 + r) ** 10


def test_compute_dcf_matches_hand_calculation():
    out = v._compute_dcf_per_share(2.0, 0.10, 0.07, 0.09, 0.03)
    assert out["intrinsic_value_per_share"] == pytest.approx(_hand_dcf(2.0, 0.10, 0.07, 0.09, 0.03), abs=1e-4)
    assert len(out["year_projections"]) == 10
    assert out["year_projections"][0]["fcf_per_share"] == pytest.approx(2.2)


def test_zero_growth_equals_perpetuity_cross_check():
    # With no growth and no terminal growth, value = FCF / r (a perpetuity).
    out = v._compute_dcf_per_share(5.0, 0.0, 0.0, 0.10, 0.0)
    assert out["intrinsic_value_per_share"] == pytest.approx(50.0, abs=1e-3)


@pytest.mark.parametrize(
    ("fcf", "g1", "g2", "code"),
    [
        (-1.5, 0.12, 0.08, "NON_POSITIVE_BASE_FCF"),
        (0.0, 0.12, 0.08, "NON_POSITIVE_BASE_FCF"),
        (2.0, 12.0, 0.08, "GROWTH_OUT_OF_RANGE"),
        (2.0, 0.12, -0.9, "GROWTH_OUT_OF_RANGE"),
    ],
)
def test_dcf_input_guards(fcf, g1, g2, code):
    assert v._dcf_input_error(fcf, g1, g2)[0] == code


def test_dcf_input_guard_accepts_sane_inputs():
    assert v._dcf_input_error(2.0, 0.12, 0.084) is None


def test_growth_rates_from_workbook_percent_are_converted():
    watchlist = {"metrics": {"fcf_per_share_growth_5y": 12.5, "fcf_per_share_growth_10y": 9.0}}
    g1, g2, source = v._resolve_growth_rates(None, None, watchlist, None)
    assert (g1, g2) == pytest.approx((0.125, 0.09))
    assert "converted from percent" in source


def test_growth_rates_already_decimal_are_untouched():
    watchlist = {"metrics": {"fcf_per_share_growth_5y": 0.125}}
    g1, g2, source = v._resolve_growth_rates(None, None, watchlist, None)
    assert g1 == pytest.approx(0.125)
    assert g2 == pytest.approx(0.125 * 0.7)
    assert source == "watchlist_fcf_per_share_growth"


def test_implied_growth_round_trips():
    price = v._compute_dcf_per_share(3.0, 0.08, 0.056, 0.09, 0.03)["intrinsic_value_per_share"]
    assert v._implied_growth_rate(3.0, price, 0.09, 0.03) == pytest.approx(0.08, abs=1e-4)


def test_implied_growth_none_when_unreachable():
    assert v._implied_growth_rate(1.0, 10_000.0, 0.09, 0.03) is None
    assert v._implied_growth_rate(-1.0, 50.0, 0.09, 0.03) is None


def _fmp_with_quote(price: float) -> MagicMock:
    fmp = MagicMock()
    fmp.fetch_snapshot.return_value = FmpSnapshot(ticker="ACME", quote={"price": price})
    return fmp


def test_build_dcf_rejects_negative_fcf(monkeypatch, settings_tavily):
    monkeypatch.setattr(v, "get_settings", lambda: settings_tavily)
    monkeypatch.setattr(v, "FmpClient", lambda s: _fmp_with_quote(50.0))
    out = v.build_dcf("ACME", base_fcf_per_share=-2.0, growth_rate_yr1_5=0.1)
    assert out["ok"] is False
    assert out["error"] == "NON_POSITIVE_BASE_FCF"


def test_build_dcf_rejects_bad_rates_before_fetching(monkeypatch, settings_tavily):
    fmp = MagicMock()
    monkeypatch.setattr(v, "FmpClient", lambda s: fmp)
    out = v.build_dcf("ACME", base_fcf_per_share=2.0, growth_rate_yr1_5=0.1,
                      discount_rate=0.03, terminal_growth_rate=0.03)
    assert out["error"] == "INVALID_RATES"
    fmp.fetch_snapshot.assert_not_called()


def test_build_dcf_happy_path_includes_reverse_dcf(monkeypatch, settings_tavily):
    monkeypatch.setattr(v, "get_settings", lambda: settings_tavily)
    monkeypatch.setattr(v, "FmpClient", lambda s: _fmp_with_quote(40.0))
    out = v.build_dcf("ACME", base_fcf_per_share=2.0, growth_rate_yr1_5=0.10)
    assert out["ok"] is True
    assert out["intrinsic_value_per_share"] == pytest.approx(_hand_dcf(2.0, 0.10, 0.07, 0.09, 0.03), abs=1e-3)
    assert out["upside_to_current"] == pytest.approx(out["intrinsic_value_per_share"] / 40.0 - 1)
    g = out["reverse_dcf"]["implied_growth_rate_yr1_5"]
    assert v._compute_dcf_per_share(2.0, g, g * 0.7, 0.09, 0.03)["intrinsic_value_per_share"] == pytest.approx(40.0, rel=1e-3)


def test_batch_dcf_screen_rejects_bad_rates_up_front():
    out = v.batch_dcf_screen(discount_rate=0.02, terminal_growth_rate=0.03)
    assert out == {
        "ok": False,
        "error": "INVALID_RATES",
        "message": "discount_rate must be strictly greater than terminal_growth_rate.",
    }
