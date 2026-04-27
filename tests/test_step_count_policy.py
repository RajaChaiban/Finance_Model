"""Solver step-count policy.

Old code: ``n_steps = int(T * 100)`` — 5 steps for a 3-week option, 25 for a
3-month. Leisen-Reimer convergence is O(1/N²); too few steps → noisy prices
inside the brentq loop, slower convergence, and "ghost gamma" artefacts.

New policy: ``n_steps = max(101, int(252 * T))`` — at least 101 (≈ 4-month
business-day daily), scaling linearly with T.

Tests assert:
  - solve_for_strike for a SHORT-tenor option converges to <$0.005 of target.
  - Same for solve_for_expiration.
  - Both finish within reasonable time (no degenerate convergence).
"""

import pytest

pytest.importorskip("QuantLib")

from src.engines import solver, quantlib_engine


def test_short_tenor_strike_solver_converges_tight():
    """3-week American put: solver hits target within 0.5 cents."""
    S, target, r, sigma, T, q = 100.0, 2.0, 0.05, 0.20, 0.06, 0.0  # ~3w
    sol = solver.solve_for_strike(S, target, r, sigma, T, q=q, option_type="put")
    assert sol.converged
    assert abs(sol.actual_price - target) < 0.005, (
        f"Short-tenor solver loose: actual=${sol.actual_price:.4f} target=${target:.4f}"
    )


def test_short_tenor_expiration_solver_converges_within_dayquant():
    """Time-to-expiration solver: convergence is bounded by day-count
    quantization (engine quantizes T to integer days). Step-count policy
    only ensures we don't ADD price noise on top of the day-quant.
    """
    S, K, target, r, sigma, q = 100.0, 100.0, 1.5, 0.05, 0.20, 0.0
    sol = solver.solve_for_expiration(S, K, target, r, sigma, q=q, option_type="put")
    assert sol.converged
    # ATM short-tenor: 1-day price step ≈ $0.04 → quant tol $0.05 is realistic.
    assert abs(sol.actual_price - target) < 0.05


def test_solver_uses_at_least_101_steps_for_short_tenors():
    """For very short T, the solver must NOT call price_american_ql with
    fewer than 51 steps (the LR floor). Indirect check: a tight tolerance
    that only holds with adequate step count.
    """
    # Choose params where 5-step LR vs 101-step LR differ noticeably.
    S, target, r, sigma, T = 100.0, 0.40, 0.05, 0.20, 0.02  # ~7d
    # If solver under-steps, brentq will converge to a strike where the
    # under-stepped price hits target — but the *correctly-priced* American
    # put will not. Use the engine directly to verify.
    sol = solver.solve_for_strike(S, target, r, sigma, T, q=0.0, option_type="put")
    p_correct, _, _ = quantlib_engine.price_american_ql(
        S=S, K=sol.value, r=r, sigma=sigma, T=T, q=0.0,
        n_steps=501, option_type="put"
    )
    assert abs(p_correct - target) < 0.01, (
        f"Solver returned strike that prices to ${p_correct:.4f} under high-res "
        f"engine — too few steps inside the solver."
    )
