"""Broadie-Glasserman-Kou continuity correction.

Discretely-monitored barrier options have HIGHER survival probability than
continuously-monitored ones (you only check at fix times — no chance of
intra-fix breaches). To make a continuous-monitoring closed-form match a
discrete monitor, shift the barrier AWAY from spot:

  B_adj = B * exp(+0.5826 * σ * sqrt(Δt))    if B > S (upper barrier)
  B_adj = B * exp(−0.5826 * σ * sqrt(Δt))    if B < S (lower barrier)

The constant 0.5826 = ζ(1/2) / sqrt(2π); see Broadie-Glasserman-Kou (1997).
"""

import numpy as np
import pytest

from src.engines.knockout import bgk_adjusted_barrier


def test_upper_barrier_shifts_up():
    """B > S → B_adj > B (barrier moves further from spot)."""
    S, B, sigma, dt = 100.0, 110.0, 0.20, 1 / 252
    B_adj = bgk_adjusted_barrier(B, S, sigma, dt)
    assert B_adj > B


def test_lower_barrier_shifts_down():
    """B < S → B_adj < B."""
    S, B, sigma, dt = 100.0, 90.0, 0.20, 1 / 252
    B_adj = bgk_adjusted_barrier(B, S, sigma, dt)
    assert B_adj < B


def test_continuous_limit_no_shift():
    """As Δt → 0, the shift vanishes."""
    S, B, sigma = 100.0, 110.0, 0.20
    B_adj = bgk_adjusted_barrier(B, S, sigma, 0.0)
    assert B_adj == pytest.approx(B, abs=1e-12)


def test_known_numerical_value_daily():
    """Daily monitoring (Δt=1/252), σ=20%: shift factor exp(0.5826*0.2*sqrt(1/252)).

    For S=100, B=110: factor = exp(0.5826 * 0.2 * 0.0630) = exp(0.007343) = 1.00737.
    B_adj = 110 * 1.00737 = 110.811.
    """
    S, B, sigma, dt = 100.0, 110.0, 0.20, 1 / 252
    expected_shift = np.exp(0.5826 * 0.20 * np.sqrt(1 / 252))
    expected_B_adj = B * expected_shift
    B_adj = bgk_adjusted_barrier(B, S, sigma, dt)
    assert B_adj == pytest.approx(expected_B_adj, rel=1e-9)
    assert B_adj == pytest.approx(110.811, rel=1e-3)  # numerical sanity


def test_shift_scales_with_sqrt_dt():
    """Doubling Δt should multiply the log-shift by sqrt(2)."""
    S, B, sigma = 100.0, 110.0, 0.20
    B1 = bgk_adjusted_barrier(B, S, sigma, 1 / 252)
    B2 = bgk_adjusted_barrier(B, S, sigma, 2 / 252)
    log_shift_1 = np.log(B1 / B)
    log_shift_2 = np.log(B2 / B)
    assert log_shift_2 / log_shift_1 == pytest.approx(np.sqrt(2.0), rel=1e-10)


def test_shift_scales_linearly_with_sigma():
    """Doubling σ doubles the log-shift."""
    S, B, dt = 100.0, 110.0, 1 / 252
    B1 = bgk_adjusted_barrier(B, S, 0.20, dt)
    B2 = bgk_adjusted_barrier(B, S, 0.40, dt)
    log_shift_1 = np.log(B1 / B)
    log_shift_2 = np.log(B2 / B)
    assert log_shift_2 / log_shift_1 == pytest.approx(2.0, rel=1e-10)


def test_at_the_money_barrier_raises():
    """B == S is degenerate (both directions). Reject explicitly."""
    with pytest.raises(ValueError):
        bgk_adjusted_barrier(100.0, 100.0, 0.20, 1 / 252)


def test_negative_dt_rejected():
    with pytest.raises(ValueError):
        bgk_adjusted_barrier(110.0, 100.0, 0.20, -1 / 252)
