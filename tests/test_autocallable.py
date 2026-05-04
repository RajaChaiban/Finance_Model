"""Tests for Phase 8 — Phoenix autocallable Monte Carlo pricer."""
import numpy as np
from src.agents.state import AutocallTerms, ObservationSchedule
from src.engines.autocallable import price_phoenix_autocallable


def test_phoenix_autocallable_price_in_normal_range():
    price = price_phoenix_autocallable(
        S0=np.array([100.0, 100.0, 100.0]),
        r=0.045, q=np.array([0.01, 0.005, 0.0]),
        sigma=np.array([0.30, 0.35, 0.40]),
        rho=0.5 * np.ones((3, 3)) + 0.5 * np.eye(3),
        terms=AutocallTerms(coupon_rate=0.10, autocall_barrier=1.0,
                            coupon_barrier=0.7, protection_barrier=0.6),
        schedule=ObservationSchedule.quarterly(2.0),
        notional=1_000_000,
        n_paths=20000, seed=7,
    )
    # Investor pays par; structure value should land near par for typical
    # phoenix at issue. +/-15% acceptance band.
    assert 850_000 < price < 1_150_000, f"Price out of band: {price:.0f}"


def test_phoenix_autocallable_deep_otm_low_value():
    # Coupon barrier so low it always pays -> value approaches sum-of-coupons + par PV.
    price = price_phoenix_autocallable(
        S0=np.array([100.0, 100.0]),
        r=0.045, q=np.zeros(2),
        sigma=np.array([0.2, 0.2]),
        rho=np.eye(2),
        terms=AutocallTerms(coupon_rate=0.05, autocall_barrier=1.0,
                            coupon_barrier=0.01, protection_barrier=0.0),
        schedule=ObservationSchedule.quarterly(1.0),
        notional=1_000_000,
        n_paths=10000, seed=3,
    )
    assert price > 950_000, f"Price too low: {price:.0f}"
