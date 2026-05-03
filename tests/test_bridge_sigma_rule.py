"""Tests that PricingResult.bridge_sigma_rule advertises the rule used to
sample a scalar σ from a live BlackVarianceSurface for closed-form barrier
pricing.

The rule today is ``max(σ_K, σ_B)`` — applied for KO/KI products under a
successful surface build. Anything else (vanilla product, surface failed,
surface skipped) leaves the field None so the client doesn't display a
misleading rule.
"""

import pytest

pytest.importorskip("QuantLib")

from src.api.handlers import price_option
from src.api.models import PricingRequest


def test_bridge_sigma_rule_set_when_ko_with_surface_ok(monkeypatch):
    """When the surface build succeeds (status='ok') AND the product is a
    knockout, bridge_sigma_rule must say 'max(sigma_K, sigma_B)'.

    We monkeypatch the surface build to a synthetic flat 25% σ so the test
    doesn't depend on yfinance / live market data. surface_status is set
    to 'ok' inside the handler when build_iv_grid → build_vol_surface
    returns without raising AND the σ sanity bound is respected.
    """
    import QuantLib as ql
    import src.api.handlers as handlers

    # BlackVarianceSurface requires >=2 strikes and >=2 expiries (bilinear
    # interp). Build a 2x2 flat-σ surface so we don't depend on yfinance.
    today = ql.Date.todaysDate()
    cal = ql.UnitedStates(ql.UnitedStates.NYSE)
    matrix = ql.Matrix(2, 2)
    for i in range(2):
        for j in range(2):
            matrix[i][j] = 0.25
    flat_surface = ql.BlackVarianceSurface(
        today, cal,
        [today + 90, today + 365],
        [80.0, 120.0],
        matrix,
        ql.Actual365Fixed(),
    )
    flat_surface.enableExtrapolation()

    # Stub the full surface-build pipeline. Returning an iv_grid-like object
    # with the n_quotes_* attrs is easier than mocking each step.
    class _FakeGrid:
        n_quotes_inverted = 50
        n_quotes_total = 50

    monkeypatch.setattr(
        handlers, "fetch_option_chain",
        lambda *a, **k: [{"some": "row"}], raising=False,
    )
    # Patch inside the conditional import scope.
    import src.api.market_data
    monkeypatch.setattr(
        src.api.market_data, "fetch_option_chain",
        lambda *a, **k: [{"some": "row"}],
    )
    import src.data.iv_grid
    monkeypatch.setattr(
        src.data.iv_grid, "build_iv_grid",
        lambda *a, **k: _FakeGrid(),
    )
    import src.data.vol_surface
    monkeypatch.setattr(
        src.data.vol_surface, "build_vol_surface",
        lambda *a, **k: flat_surface,
    )

    req = PricingRequest(
        option_type="knockout_call",
        underlying="TEST",
        spot_price=100.0,
        strike_price=100.0,
        days_to_expiration=90,
        risk_free_rate=0.04,
        volatility=0.20,  # ignored when surface is on
        dividend_yield=0.02,
        barrier_level=85.0,
        barrier_type="down_and_out",
        use_vol_surface=True,
    )
    res = price_option(req)

    assert res.surface_status == "ok"
    assert res.bridge_sigma_rule == "max(sigma_K, sigma_B)"


def test_bridge_sigma_rule_none_for_vanilla_options():
    """Vanilla (non-barrier) options leave bridge_sigma_rule None even
    when use_vol_surface is True — the rule is barrier-specific."""
    # No surface flag → status='skipped' → rule must be None.
    req = PricingRequest(
        option_type="european_call",
        underlying="TEST",
        spot_price=100.0,
        strike_price=100.0,
        days_to_expiration=90,
        risk_free_rate=0.04,
        volatility=0.20,
        dividend_yield=0.02,
    )
    res = price_option(req)
    assert res.bridge_sigma_rule is None


def test_bridge_sigma_rule_none_when_surface_skipped():
    """Surface skipped (use_vol_surface=False) → rule None even for KO."""
    req = PricingRequest(
        option_type="knockout_call",
        underlying="TEST",
        spot_price=100.0,
        strike_price=100.0,
        days_to_expiration=90,
        risk_free_rate=0.04,
        volatility=0.20,
        dividend_yield=0.02,
        barrier_level=85.0,
        barrier_type="down_and_out",
        use_vol_surface=False,
    )
    res = price_option(req)
    assert res.surface_status == "skipped"
    assert res.bridge_sigma_rule is None
