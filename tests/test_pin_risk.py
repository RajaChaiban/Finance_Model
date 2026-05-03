"""Tests for pin-risk handling in barrier Greeks.

When S sits within the engine's accuracy floor of B, both up- and
down-bumps in the central-difference Greek formula straddle the barrier,
making the resulting "Greeks" pure numerical noise (frequently sign-
flipped). The contract: NaN out delta/gamma/vega/theta/rho and surface
``pin_risk: True`` on the response. The price itself is still computed
because the engine handles S-on-B correctly for the price.
"""

import math

import pytest

pytest.importorskip("QuantLib")

from src.engines.quantlib_engine import greeks_knockout_ql
from src.api.handlers import price_option
from src.api.models import PricingRequest


def test_greeks_knockout_at_pin_returns_nan_with_flag():
    """S = B exactly → Greeks NaN, pin_risk=True. Price must still be
    finite because the AnalyticBarrierEngine handles S-on-B (KO knocks
    immediately, KI activates immediately)."""
    g = greeks_knockout_ql(
        S=100.0, K=100.0, B=100.0, r=0.05, sigma=0.25, T=0.5, q=0.02,
        option_type="call", barrier_kind="out",
    )
    assert g["pin_risk"] is True
    for greek in ("delta", "gamma", "vega", "theta", "rho"):
        assert math.isnan(g[greek]), f"{greek} should be NaN at pin, got {g[greek]}"
    # Price still numeric.
    assert math.isfinite(g["price"])


def test_greeks_knockout_off_pin_returns_finite_with_flag_false():
    """One bump-distance away from the barrier → finite Greeks,
    pin_risk=False. h_engine_floor on a $100 stock is max(S*0.001, 1e-3)
    = $0.1, so S = B + $5 is comfortably outside."""
    g = greeks_knockout_ql(
        S=105.0, K=100.0, B=100.0, r=0.05, sigma=0.25, T=0.5, q=0.02,
        option_type="call", barrier_kind="out",
    )
    assert g["pin_risk"] is False
    for greek in ("delta", "gamma", "vega", "theta", "rho"):
        assert math.isfinite(g[greek]), f"{greek} should be finite off pin"


def test_pin_risk_lifted_to_pricing_result():
    """Round-trip via the REST handler: a request with S=B sets
    PricingResult.pin_risk=True and the result.greeks dict has NaN values
    where the bump-reprice would have straddled the barrier."""
    req = PricingRequest(
        option_type="knockout_call",
        underlying="TEST",
        spot_price=100.0,
        strike_price=100.0,
        days_to_expiration=180,
        risk_free_rate=0.04,
        volatility=0.25,
        dividend_yield=0.02,
        barrier_level=100.0,        # S == B
        barrier_type="down_and_out",
    )
    res = price_option(req)
    assert res.pin_risk is True
    assert math.isnan(res.greeks["delta"])
    # And pin_risk must NOT leak into the greeks dict on the response —
    # the dict should only carry numeric Greeks.
    assert "pin_risk" not in res.greeks


def test_pin_risk_false_for_non_pinned_request():
    """Off-pin request: pin_risk False on the response; greeks dict has
    finite values."""
    req = PricingRequest(
        option_type="knockout_call",
        underlying="TEST",
        spot_price=110.0,
        strike_price=100.0,
        days_to_expiration=180,
        risk_free_rate=0.04,
        volatility=0.25,
        dividend_yield=0.02,
        barrier_level=90.0,
        barrier_type="down_and_out",
    )
    res = price_option(req)
    assert res.pin_risk is False
    assert math.isfinite(res.greeks["delta"])


def test_pin_risk_false_for_vanilla_options():
    """Vanilla (non-barrier) options never trip pin_risk, even though
    the field exists on every PricingResult."""
    req = PricingRequest(
        option_type="european_call",
        underlying="TEST",
        spot_price=100.0,
        strike_price=100.0,
        days_to_expiration=180,
        risk_free_rate=0.04,
        volatility=0.25,
        dividend_yield=0.02,
    )
    res = price_option(req)
    assert res.pin_risk is False
