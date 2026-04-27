"""Closed-form analytical vs QuantLib regression.

These tests are the first line of model validation. Any disagreement between
the closed-form and the QuantLib engines points to one of:

  - A bug in the analytical formula (sign, factor, missing term)
  - A day-count / calendar convention mismatch at the QL boundary
  - Precision drift due to date quantization (always integer days)

Two layers of test:

  1. **Day-exact grid** (1e-10 tol): T values where T*365 is integer, so QL's
     date arithmetic introduces zero quantization. This is the pure numerical
     agreement test — failure here means a real bug.

  2. **Arbitrary-T grid** (small dollar tol): tests quantization is bounded.
     Tolerance reflects ~half-day worst-case quantization × ∂Price/∂T.
"""

import numpy as np
import pytest

pytest.importorskip("QuantLib")

from src.engines import black_scholes, knockout, quantlib_engine


# Numerical-noise tolerance: BS closed-form vs QL closed-form, identical T.
TIGHT_TOL = 1e-10


def _drift_tolerance(S, K, r, sigma, T, q, opt):
    """First-principles bound for half-day T-quantization drift.

    With round-half-up, max |T_input - T_effective| = 0.5/365 yr.
    Resulting price drift ≤ 0.5 × |theta_per_day| (theta carries the day rate).
    Use 1.5× as headroom for option convexity in T; floor at $0.001 to handle
    near-zero-theta degenerate cases.
    """
    g = black_scholes.greeks_european(S, K, r, sigma, T, q, opt)
    theta_per_day = abs(g["theta"])
    return max(1.5 * theta_per_day, 1e-3)


# Day-exact grid: (S, K, r, sigma, days, q). T = days / 365 exactly.
DAYS_EXACT_GRID = [
    (100.0, 100.0, 0.05, 0.20, 365, 0.0),    # 1Y ATM
    (100.0, 100.0, 0.05, 0.20, 182, 0.0),    # ~6m ATM
    (100.0, 100.0, 0.05, 0.20, 91, 0.02),    # ~3m ATM with div
    (100.0, 110.0, 0.05, 0.20, 182, 0.0),    # OTM call / ITM put
    (100.0, 90.0, 0.05, 0.20, 182, 0.0),     # ITM call / OTM put
    (100.0, 100.0, 0.03, 0.10, 730, 0.04),   # 2Y low vol high div
    (100.0, 100.0, 0.05, 0.50, 91, 0.0),     # high vol short tenor
    (50.0, 50.0, 0.04, 0.30, 273, 0.01),     # different scale
    (200.0, 195.0, 0.045, 0.18, 30, 0.015),  # 1m
]

# Arbitrary T (may not quantize cleanly).
ARB_T_GRID = [
    (100.0, 100.0, 0.05, 0.20, 0.5, 0.0),
    (100.0, 100.0, 0.05, 0.20, 0.25, 0.02),
    (100.0, 100.0, 0.05, 0.20, 0.75, 0.0),
    (100.0, 100.0, 0.03, 0.30, 1.5, 0.02),
]


# --------------------------------------------------------------------------- #
# Layer 1 — TIGHT: identical T (day-exact), should agree to float noise.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("S,K,r,sigma,days,q", DAYS_EXACT_GRID)
@pytest.mark.parametrize("opt", ["call", "put"])
def test_european_bs_matches_quantlib_dayexact(S, K, r, sigma, days, q, opt):
    """BS closed-form ≡ QL analytic engine when T is day-exact."""
    T = days / 365.0
    bs = black_scholes.price_european(S, K, r, sigma, T, q, opt)
    ql_res = quantlib_engine.greeks_ql(S, K, r, sigma, T, q, option_type=opt, is_american=False)
    assert abs(bs - ql_res["price"]) < TIGHT_TOL, (
        f"{opt} S={S} K={K} days={days} q={q}: "
        f"BS={bs:.12f} QL={ql_res['price']:.12f} diff={abs(bs - ql_res['price']):.2e}"
    )


@pytest.mark.parametrize("S,K,r,sigma,days,q", DAYS_EXACT_GRID)
@pytest.mark.parametrize("opt", ["call", "put"])
def test_european_greeks_match_dayexact(S, K, r, sigma, days, q, opt):
    """Delta/Gamma/Vega closed-form ≡ QL when T day-exact."""
    T = days / 365.0
    bs = black_scholes.greeks_european(S, K, r, sigma, T, q, opt)
    ql = quantlib_engine.greeks_ql(S, K, r, sigma, T, q, option_type=opt, is_american=False)
    assert abs(bs["delta"] - ql["delta"]) < 1e-9
    assert abs(bs["gamma"] - ql["gamma"]) < 1e-9
    assert abs(bs["vega"] - ql["vega"]) < 1e-8


# --------------------------------------------------------------------------- #
# Knockout: Reiner-Rubinstein ≡ ql.AnalyticBarrierEngine
# --------------------------------------------------------------------------- #
KO_DAYS_EXACT_GRID = [
    # (S, K, B, r, sigma, days, q, opt)
    (100.0, 100.0, 80.0, 0.05, 0.20, 182, 0.0, "call"),    # DOI call
    (100.0, 100.0, 90.0, 0.05, 0.20, 91, 0.02, "call"),    # DOI call near barrier
    (100.0, 110.0, 80.0, 0.05, 0.30, 365, 0.0, "call"),
    (100.0, 100.0, 120.0, 0.05, 0.20, 182, 0.0, "put"),    # UOI put
    (100.0, 90.0, 130.0, 0.05, 0.25, 273, 0.0, "put"),
    (100.0, 100.0, 115.0, 0.05, 0.20, 182, 0.02, "put"),
]


@pytest.mark.parametrize("S,K,B,r,sigma,days,q,opt", KO_DAYS_EXACT_GRID)
def test_knockout_reiner_rubinstein_matches_quantlib(S, K, B, r, sigma, days, q, opt):
    """RR closed-form ≡ QL barrier engine, day-exact T."""
    T = days / 365.0
    rr_price, _, _, _ = knockout.price_knockout(S, K, B, r, sigma, T, q, opt)
    ql_price, _, _ = quantlib_engine.price_knockout_ql(S, K, B, r, sigma, T, q, opt)
    assert abs(rr_price - ql_price) < TIGHT_TOL, (
        f"KO {opt} S={S} K={K} B={B} days={days}: "
        f"RR={rr_price:.12f} QL={ql_price:.12f} diff={abs(rr_price - ql_price):.2e}"
    )


# --------------------------------------------------------------------------- #
# Layer 2 — DRIFT: arbitrary T. Tolerance reflects day-count quantization.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("S,K,r,sigma,T,q", ARB_T_GRID)
@pytest.mark.parametrize("opt", ["call", "put"])
def test_european_drift_bounded_by_dayquant(S, K, r, sigma, T, q, opt):
    """Arbitrary-T disagreement bounded by half-day theta drift.

    A failure here means quantization exceeds 1.5× one-day's theta, which
    points to an int-truncation bug or a calendar/day-count mismatch — not
    legitimate convention noise.
    """
    tol = _drift_tolerance(S, K, r, sigma, T, q, opt)
    bs = black_scholes.price_european(S, K, r, sigma, T, q, opt)
    ql_res = quantlib_engine.greeks_ql(S, K, r, sigma, T, q, option_type=opt, is_american=False)
    drift = abs(bs - ql_res["price"])
    assert drift < tol, (
        f"{opt} S={S} K={K} T={T}: drift=${drift:.4f} exceeds half-day tol=${tol:.4f}. "
        f"Likely a convention bug, not quantization."
    )
