"""MC LSM Theta convention tests.

Theta convention across the pipeline (matching QuantLib):
  - Per-day, NOT per-year
  - Sign convention: ∂V/∂t (calendar time forward) → negative for long options

The MC engine had an inverted sign + per-year scale bug; these tests guard it.
"""

import numpy as np
import pytest

from src.engines import monte_carlo_lsm, black_scholes


@pytest.mark.parametrize("opt", ["call", "put"])
def test_mc_theta_sign_negative_for_long(opt):
    """Long ATM American option must have negative theta (loses value with time)."""
    S, K, r, sigma, T, q = 100.0, 100.0, 0.05, 0.20, 0.5, 0.0
    g = monte_carlo_lsm.greeks_american(S, K, r, sigma, T, q, n_paths=2000, n_steps=30,
                                         option_type=opt)
    assert g["theta"] < 0, f"{opt} theta should be negative, got {g['theta']:.6f}"


@pytest.mark.parametrize("opt", ["call", "put"])
def test_mc_theta_per_day_scale(opt):
    """MC theta must be per-day (not per-year). For ATM 6m option, |theta| < 0.1.

    Heuristic: a per-year theta would be ≳ |1.5| for a typical ATM option.
    A per-day theta is < 0.1 except for very-short-dated cases.
    """
    S, K, r, sigma, T, q = 100.0, 100.0, 0.05, 0.20, 0.5, 0.0
    g = monte_carlo_lsm.greeks_american(S, K, r, sigma, T, q, n_paths=2000, n_steps=30,
                                         option_type=opt)
    assert abs(g["theta"]) < 0.1, (
        f"{opt} theta={g['theta']:.4f} too large in magnitude — looks per-year not per-day"
    )


@pytest.mark.parametrize("opt", ["call", "put"])
def test_mc_theta_matches_bs_european_within_mc_noise(opt):
    """MC American theta should be within MC noise of analytical European theta.

    For an ATM option with small early-exercise premium, American≈European, so
    theta should agree to ~MC noise (a few cents). This validates magnitude
    and direction simultaneously.
    """
    S, K, r, sigma, T, q = 100.0, 100.0, 0.05, 0.20, 0.5, 0.02  # call carries div, put basically European
    bs = black_scholes.greeks_european(S, K, r, sigma, T, q, opt)
    mc = monte_carlo_lsm.greeks_american(S, K, r, sigma, T, q, n_paths=20000, n_steps=90,
                                          option_type=opt)
    # Tolerance: MC noise on theta is roughly 5x noise on price; with 20k paths
    # std error on price is ~$0.04 → on theta ~$0.02 per-day. Use $0.05 with buffer.
    assert abs(mc["theta"] - bs["theta"]) < 0.05, (
        f"MC theta {mc['theta']:.4f} vs BS theta {bs['theta']:.4f}"
    )
