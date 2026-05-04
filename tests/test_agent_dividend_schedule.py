"""Verifies dividend_schedule plumbing in the structuring co-pilot's PricingAgent.

The agent layer used to silently drop discrete cash dividends — `Leg` rejected
the field via `extra="forbid"` and `_price_leg` never forwarded it to the
router. American legs were therefore priced with continuous-yield only, even
when the user supplied a discrete schedule via the Quick Pricer in the same
session. This module locks in the parity between agent-side and direct-router
pricing for that path.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pytest

from src.agents.pricing import PricingAgent, _convert_dividend_schedule
from src.agents.state import Leg, MarketRegime
from src.engines import router


# Single-name, ITM, ~1% effective dividend yield setup. ITM American calls
# are the regime where discrete dividends bite hardest (early-exercise
# decision concentrated on ex-div dates), so the gap between continuous-q
# and discrete-schedule pricing is reliably ~50-150 bp here.
_SPOT = 100.0
_STRIKE = 90.0
_R = 0.045
_SIGMA = 0.25
_EXPIRY_DAYS = 180
_REGIME = MarketRegime(
    underlying="TEST",
    spot=_SPOT,
    dividend_yield=0.0,  # zero continuous → only the discrete schedule moves the price
    risk_free_rate=_R,
    realised_vol_30d=_SIGMA,
)


def _two_div_schedule() -> list[list]:
    """Two cash dividends across the 180-day life ≈ 1% annual yield equivalent."""
    today = date.today()
    return [
        [(today + timedelta(days=45)).isoformat(), 0.25],
        [(today + timedelta(days=135)).isoformat(), 0.25],
    ]


def test_leg_accepts_dividend_schedule_field():
    """Pydantic must accept the new field — the bug was `extra='forbid'` + no
    declaration meant `Leg(..., dividend_schedule=[...])` raised a validation
    error before the router could ever see the data."""
    leg = Leg(
        option_type="american_call",
        strike=_STRIKE,
        expiry_days=_EXPIRY_DAYS,
        quantity=1.0,
        dividend_schedule=_two_div_schedule(),
    )
    assert leg.dividend_schedule is not None
    assert len(leg.dividend_schedule) == 2


def test_american_leg_with_dividend_schedule_uses_discrete_engine():
    """ITM American call: discrete schedule must produce a measurably different
    price from the same leg with `dividend_schedule=None`. We expect a 50-150 bp
    gap (in price-of-spot terms)."""
    base_leg = Leg(
        option_type="american_call",
        strike=_STRIKE,
        expiry_days=_EXPIRY_DAYS,
        quantity=1.0,
    )
    div_leg = Leg(
        option_type="american_call",
        strike=_STRIKE,
        expiry_days=_EXPIRY_DAYS,
        quantity=1.0,
        dividend_schedule=_two_div_schedule(),
    )

    base_price, _, _ = PricingAgent._price_leg(base_leg, _REGIME, _SIGMA)
    div_price, _, _ = PricingAgent._price_leg(div_leg, _REGIME, _SIGMA)

    gap = abs(base_price - div_price)
    gap_bps = gap / _SPOT * 10000.0
    # Lower bound: confirms the discrete schedule isn't being silently dropped.
    # Upper bound is loose; the assertion is "this is meaningful, not noise".
    assert gap_bps > 5.0, (
        f"Expected discrete-vs-continuous gap > 5bp, got {gap_bps:.2f}bp "
        f"(base={base_price:.4f}, div={div_price:.4f}). "
        "Likely the dividend_schedule field is being dropped before the router."
    )
    # Discrete cash divs reduce a long American call vs. the no-div case.
    assert div_price < base_price, (
        f"Expected discrete-div American call price < no-div price; "
        f"got div={div_price:.4f} vs base={base_price:.4f}"
    )


def test_american_leg_discrete_matches_router():
    """Parity check: the agent-side price must equal what `router.route(...)`
    returns when called directly with the same converted schedule."""
    schedule = _two_div_schedule()
    leg = Leg(
        option_type="american_call",
        strike=_STRIKE,
        expiry_days=_EXPIRY_DAYS,
        quantity=1.0,
        dividend_schedule=schedule,
    )

    agent_price, _, _ = PricingAgent._price_leg(leg, _REGIME, _SIGMA)

    pricer, _, _ = router.route("american_call")
    converted = _convert_dividend_schedule(schedule)
    direct_price, _, _ = pricer(
        S=_SPOT,
        K=_STRIKE,
        r=_R,
        sigma=_SIGMA,
        T=_EXPIRY_DAYS / 365.0,
        q=0.0,
        dividend_schedule=converted,
    )

    assert agent_price == pytest.approx(direct_price, rel=1e-9, abs=1e-9), (
        f"Agent path ({agent_price}) and direct router path ({direct_price}) "
        "must agree to machine precision when both see the same converted schedule."
    )


def test_non_american_leg_with_dividend_schedule_warns(caplog):
    """A `dividend_schedule` on a non-American leg must log a warning. Silent
    drop is acceptable, but the operator deserves to know their input was
    ignored — same contract as the parallel API-router fix."""
    leg = Leg(
        option_type="european_call",
        strike=_STRIKE,
        expiry_days=_EXPIRY_DAYS,
        quantity=1.0,
        dividend_schedule=_two_div_schedule(),
    )
    plain_leg = Leg(
        option_type="european_call",
        strike=_STRIKE,
        expiry_days=_EXPIRY_DAYS,
        quantity=1.0,
    )

    with caplog.at_level(logging.WARNING, logger="src.agents.pricing"):
        warned_price, _, _ = PricingAgent._price_leg(leg, _REGIME, _SIGMA)
    plain_price, _, _ = PricingAgent._price_leg(plain_leg, _REGIME, _SIGMA)

    assert any(
        "dividend_schedule supplied on european_call leg" in rec.message
        for rec in caplog.records
    ), f"Expected warning about ignored dividend_schedule; got: {[r.message for r in caplog.records]}"
    # Silent drop: the price should be unchanged because the engine never saw it.
    assert warned_price == pytest.approx(plain_price, rel=1e-12, abs=1e-12)
