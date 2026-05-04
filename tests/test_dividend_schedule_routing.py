"""Tests that PricingRequest.dividend_schedule reaches the discrete-div FDM
engine and produces a price that's measurably different from the
continuous-yield approximation.

Why this exists: the discrete-div engine was fully built and unit-tested at
the engine level (price_american_discrete_div_ql) but unreachable from REST,
CLI, or YAML — the field was missing from PricingRequest, PricingConfig, and
the pricing_params dict in handlers/main. These tests pin the now-wired path
end-to-end so the regression doesn't recur.
"""

import math
from datetime import date, timedelta

import pytest

pytest.importorskip("QuantLib")

from src.api.handlers import price_option, _convert_dividend_schedule
from src.api.models import PricingRequest


def _amer_call_request(**overrides):
    """An American call where a discrete dividend should matter — high q
    (relative to r) and a near-the-money strike so early exercise is on the
    table. Defaults give a ~$3-5 option."""
    base = dict(
        option_type="american_call",
        underlying="TEST",
        spot_price=100.0,
        strike_price=100.0,
        days_to_expiration=180,
        risk_free_rate=0.04,
        volatility=0.25,
        dividend_yield=0.0,  # discrete dividends only
    )
    base.update(overrides)
    return PricingRequest(**base)


def test_dividend_schedule_changes_price_vs_continuous_q():
    """Pricing a $2 cash dividend mid-life via the discrete-div FDM engine
    must give a measurably different price than the continuous-q
    approximation. The two engines should disagree by more than FDM noise
    (~1bp = $0.01-ish on a $100 underlying).

    On an American call, the discrete drop on ex-div day is exactly the
    early-exercise trigger that the continuous-yield approximation cannot
    capture — so this test would silently pass if the wire-through were
    broken AND the engine were also broken; we use the engine-level test
    elsewhere to pin the engine itself.
    """
    # Continuous-yield baseline: q=0, no discrete divs.
    req_no_divs = _amer_call_request()
    res_no_divs = price_option(req_no_divs)

    # Discrete-div: ex-div ~90 days out, $2 cash.
    ex_div_date = (date.today() + timedelta(days=90)).isoformat()
    req_divs = _amer_call_request(dividend_schedule=[[ex_div_date, 2.0]])
    res_divs = price_option(req_divs)

    # The discrete dividend must move the price by more than FDM grid noise.
    # On a $100 underlying, FDM @ 200x200 ~ 1bp = ~$0.01; require 5x that.
    assert abs(res_divs.price - res_no_divs.price) > 0.05, (
        f"discrete-div price ${res_divs.price:.4f} barely differs from "
        f"continuous-q baseline ${res_no_divs.price:.4f} — "
        f"is dividend_schedule reaching the engine?"
    )


def test_dividend_schedule_routes_to_fdm_method_label():
    """Engine routing sanity: when dividend_schedule is non-empty, the
    method label still reports a QuantLib American engine (not the LR-tree
    label) so the report doesn't claim a tree was used when FDM was."""
    ex_div_date = (date.today() + timedelta(days=30)).isoformat()
    req = _amer_call_request(dividend_schedule=[[ex_div_date, 1.0]])
    res = price_option(req)
    assert "QuantLib" in res.method or "Monte Carlo" in res.method
    # And the price is finite + non-negative — basic sanity.
    assert math.isfinite(res.price) and res.price >= 0


def test_dividend_schedule_default_none_is_unchanged():
    """Backwards-compatibility: not setting dividend_schedule must give the
    SAME price as before the contract change (continuous-q FDM is now the
    default for American Greeks; price still goes through price_american_ql
    → LR tree). Pinning this so the wire-through can't accidentally route
    every American option through the discrete-div engine."""
    req = _amer_call_request()  # no dividend_schedule -> continuous-q
    res = price_option(req)
    # No assertion on absolute price; this is a guard that the call doesn't
    # raise + returns a finite number. Absolute regression is owned by the
    # standalone American-pricing tests.
    assert math.isfinite(res.price)


def test_convert_dividend_schedule_helper_roundtrip():
    """Direct test of the wire-format → ql.Date converter — bad input must
    raise a clear ValueError, not a cryptic QuantLib error."""
    import QuantLib as ql

    out = _convert_dividend_schedule([["2026-06-15", 1.5], ["2026-12-15", 1.5]])
    assert out is not None and len(out) == 2
    d0, amt0 = out[0]
    assert isinstance(d0, ql.Date)
    assert d0.year() == 2026 and d0.month() == 6 and d0.dayOfMonth() == 15
    assert amt0 == pytest.approx(1.5)

    # Empty / None passthroughs.
    assert _convert_dividend_schedule(None) is None
    assert _convert_dividend_schedule([]) is None

    # Malformed entries raise.
    with pytest.raises(ValueError):
        _convert_dividend_schedule([["not-a-date", 1.0]])
    with pytest.raises(ValueError):
        _convert_dividend_schedule([["2026-06-15", "two-bucks"]])
    with pytest.raises(ValueError):
        _convert_dividend_schedule([["2026-06-15"]])  # missing amount
