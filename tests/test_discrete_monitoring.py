"""End-to-end discrete monitoring on knockout pricing.

Pricing invariants:
  1. Daily-monitored KO > continuous-monitored KO (less knock-out probability).
  2. Weekly > Daily > Continuous (longer between fixes → less likely to hit).
  3. As Δt → 0 we recover the continuous price.

Magnitude check: for an ATM-ish 1Y 25-vol DOI call with B near 90, the
discrete-vs-continuous gap should be ≥ 30bp of the option's value.
"""

import pytest

pytest.importorskip("QuantLib")

from src.engines import knockout, quantlib_engine, router


# Standard test contract: 1Y DOI call with barrier 10% below spot.
S, K, B, r, sigma, T, q = 100.0, 100.0, 90.0, 0.05, 0.25, 1.0, 0.0
OPT = "call"


def test_discrete_monitoring_increases_qlbarrier_price():
    """QL engine: daily-monitored DOI call > continuous DOI call."""
    p_cont, _, _ = quantlib_engine.price_knockout_ql(S, K, B, r, sigma, T, q, OPT,
                                                     monitoring="continuous")
    p_daily, _, _ = quantlib_engine.price_knockout_ql(S, K, B, r, sigma, T, q, OPT,
                                                      monitoring="daily")
    assert p_daily > p_cont


def test_weekly_between_daily_and_monthly():
    """Monotone in monitoring frequency: continuous < daily < weekly < monthly."""
    p_cont, _, _ = quantlib_engine.price_knockout_ql(S, K, B, r, sigma, T, q, OPT,
                                                     monitoring="continuous")
    p_daily, _, _ = quantlib_engine.price_knockout_ql(S, K, B, r, sigma, T, q, OPT,
                                                      monitoring="daily")
    p_weekly, _, _ = quantlib_engine.price_knockout_ql(S, K, B, r, sigma, T, q, OPT,
                                                       monitoring="weekly")
    p_monthly, _, _ = quantlib_engine.price_knockout_ql(S, K, B, r, sigma, T, q, OPT,
                                                        monitoring="monthly")
    assert p_cont < p_daily < p_weekly < p_monthly


def test_discrete_monitoring_magnitude_meaningful():
    """Discrete-vs-continuous gap is structurally significant (≥30bp on premium)."""
    p_cont, _, _ = quantlib_engine.price_knockout_ql(S, K, B, r, sigma, T, q, OPT,
                                                     monitoring="continuous")
    p_daily, _, _ = quantlib_engine.price_knockout_ql(S, K, B, r, sigma, T, q, OPT,
                                                      monitoring="daily")
    rel_gap = (p_daily - p_cont) / p_cont
    assert rel_gap >= 0.003, (
        f"Daily-monitor uplift {rel_gap*100:.2f}% suspiciously small — BGK shift not applied?"
    )


def test_default_remains_continuous():
    """No monitoring kwarg → continuous (back-compat)."""
    p_default, _, _ = quantlib_engine.price_knockout_ql(S, K, B, r, sigma, T, q, OPT)
    p_cont, _, _ = quantlib_engine.price_knockout_ql(S, K, B, r, sigma, T, q, OPT,
                                                     monitoring="continuous")
    assert p_default == p_cont


def test_router_passes_monitoring_through():
    """router.route('knockout_call') honours monitoring kwarg."""
    pricer, _, _ = router.route("knockout_call")
    p_cont, _, _ = pricer(S=S, K=K, r=r, sigma=sigma, T=T, q=q, barrier_level=B,
                          monitoring="continuous")
    p_daily, _, _ = pricer(S=S, K=K, r=r, sigma=sigma, T=T, q=q, barrier_level=B,
                           monitoring="daily")
    assert p_daily > p_cont


def test_reiner_rubinstein_fallback_supports_monitoring():
    """The pure-Python knockout engine accepts monitoring too."""
    p_cont, *_ = knockout.price_knockout(S, K, B, r, sigma, T, q, OPT,
                                         monitoring="continuous")
    p_daily, *_ = knockout.price_knockout(S, K, B, r, sigma, T, q, OPT,
                                          monitoring="daily")
    assert p_daily > p_cont
