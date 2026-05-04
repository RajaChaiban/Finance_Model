"""Tests that PricingRequest.monitoring is forwarded to the barrier engine
and triggers the BGK shift on the barrier for discretely-monitored knockouts.

Why this exists: pricing_params in handlers and main never carried a
``monitoring`` key, so every barrier price was implicitly continuous (the
default for the engine wrapper) — even when the structuring co-pilot path
(which DOES set it) was producing a different number for the same trade.
"""

import pytest

pytest.importorskip("QuantLib")

from src.api.handlers import price_option
from src.api.models import PricingRequest


def _ko_call_request(monitoring="continuous"):
    """Down-and-out call with B safely below S so a continuous-monitoring
    price is non-trivial; the BGK shift on a daily-monitored barrier moves
    B further from S, raising the live price by a few cents."""
    return PricingRequest(
        option_type="knockout_call",
        underlying="TEST",
        spot_price=100.0,
        strike_price=100.0,
        days_to_expiration=180,
        risk_free_rate=0.04,
        volatility=0.30,
        dividend_yield=0.02,
        barrier_level=85.0,
        barrier_type="down_and_out",
        monitoring=monitoring,
    )


def test_daily_monitoring_differs_from_continuous():
    """Daily-monitored KO call must be priced differently than the
    continuous-monitored equivalent — BGK shifts the barrier away from spot,
    making the discretely-monitored option more valuable.

    Tolerance: the shift on a 30%-vol 6M trade is ~0.5826 * σ * √(1/252) * B
    ≈ 0.93 in barrier units, which on a $100 spot moves the live price by
    several cents. Require > 1bp = $0.01.
    """
    res_cont = price_option(_ko_call_request(monitoring="continuous"))
    res_daily = price_option(_ko_call_request(monitoring="daily"))

    delta = abs(res_daily.price - res_cont.price)
    assert delta > 0.01, (
        f"daily-monitored KO price ${res_daily.price:.4f} barely differs "
        f"from continuous ${res_cont.price:.4f} (Δ=${delta:.4f}) — is "
        f"BGK firing? Is `monitoring` reaching the engine?"
    )

    # Direction sanity: daily monitoring on a down-and-out call shifts the
    # barrier DOWN (further from spot), making knockout LESS likely → KO
    # call price goes UP vs continuous.
    assert res_daily.price > res_cont.price, (
        f"daily KO call price ${res_daily.price:.4f} should be HIGHER than "
        f"continuous ${res_cont.price:.4f} (BGK shifts B further from S)"
    )


def test_weekly_and_monthly_round_trip_without_error():
    """Smoke: weekly/monthly monitoring don't raise + return finite prices.
    Lower bar than the daily test because the BGK shift is small enough on
    a low-vol short-dated trade that signal can be tiny."""
    for monitoring in ("weekly", "monthly"):
        res = price_option(_ko_call_request(monitoring=monitoring))
        assert res.price >= 0
        assert res.price == res.price  # not nan
