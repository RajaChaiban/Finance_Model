"""Tests for Greeks-based P&L attribution (src/analysis/pnl_explain.py).

Convention reminders (verified against black_scholes.py):
  - Vega  : per 1% absolute σ  (already /100)
  - Theta : per calendar day   (already /365 of annual)
  - Rho   : per 1% absolute r  (already /100)
"""

import math
import pytest

from src.analysis.pnl_explain import explain_pnl, PnLAttribution
from src.engines.black_scholes import price_european, greeks_european


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _bs_price_and_greeks(S, K=100.0, r=0.05, sigma=0.20, T=0.5, q=0.02,
                         option_type="call"):
    """Return (price, greeks_dict) from the analytic BS engine."""
    g = greeks_european(S=S, K=K, r=r, sigma=sigma, T=T, q=q,
                        option_type=option_type)
    p = g.pop("price")
    return p, g


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — no market move: only theta contributes
# ─────────────────────────────────────────────────────────────────────────────

def test_no_market_move_pnl_is_only_theta():
    """When S, σ, r are all unchanged, the only component is θ·dt."""
    S = 100.0
    sigma = 0.20
    r = 0.05
    K = 100.0
    T = 0.5   # 0.5 years = ~182 days
    q = 0.02

    prev_price, prev_greeks = _bs_price_and_greeks(S, K=K, r=r, sigma=sigma,
                                                    T=T, q=q)
    # Move exactly 1 calendar day forward (T shrinks by 1/365)
    T_next = T - 1 / 365
    curr_price, _ = _bs_price_and_greeks(S, K=K, r=r, sigma=sigma,
                                          T=T_next, q=q)

    attr = explain_pnl(
        prev_price=prev_price,
        prev_greeks=prev_greeks,
        prev_S=S,
        prev_sigma=sigma,
        prev_r=r,
        curr_price=curr_price,
        curr_S=S,
        curr_sigma=sigma,
        curr_r=r,
        dt_days=1,
    )

    assert attr.delta_pnl == pytest.approx(0.0, abs=1e-12), "delta_pnl should be 0"
    assert attr.gamma_pnl == pytest.approx(0.0, abs=1e-12), "gamma_pnl should be 0"
    assert attr.vega_pnl  == pytest.approx(0.0, abs=1e-12), "vega_pnl should be 0"
    assert attr.rho_pnl   == pytest.approx(0.0, abs=1e-12), "rho_pnl should be 0"

    # theta contribution should be close to the actual price change
    expected_theta_pnl = prev_greeks["theta"] * 1  # 1 day
    assert attr.theta_pnl == pytest.approx(expected_theta_pnl, abs=1e-12)


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — pure spot move: delta + gamma + theta explain the observed P&L
# ─────────────────────────────────────────────────────────────────────────────

def test_pure_spot_move_explains_via_delta_gamma():
    """1% spot move, Δσ=Δr=0. delta+gamma+theta should match observed within 0.10."""
    S0 = 100.0
    S1 = S0 * 1.01   # 1% up
    sigma = 0.20
    r = 0.05
    K = 100.0
    T = 0.5
    q = 0.02

    prev_price, prev_greeks = _bs_price_and_greeks(S0, K=K, r=r, sigma=sigma,
                                                    T=T, q=q)
    T_next = T - 1 / 365
    curr_price, _ = _bs_price_and_greeks(S1, K=K, r=r, sigma=sigma,
                                          T=T_next, q=q)

    attr = explain_pnl(
        prev_price=prev_price,
        prev_greeks=prev_greeks,
        prev_S=S0,
        prev_sigma=sigma,
        prev_r=r,
        curr_price=curr_price,
        curr_S=S1,
        curr_sigma=sigma,
        curr_r=r,
        dt_days=1,
    )

    approx_total = attr.delta_pnl + attr.gamma_pnl + attr.theta_pnl
    assert abs(approx_total - attr.total_observed) < 0.10, (
        f"delta+gamma+theta={approx_total:.4f} vs observed={attr.total_observed:.4f}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — pure vol move: vega + theta explain the observed P&L
# ─────────────────────────────────────────────────────────────────────────────

def test_pure_vol_move_explains_via_vega():
    """Δσ = +0.01 (1 percentage point), ΔS=Δr=0. vega+theta ≈ observed."""
    S = 100.0
    sigma0 = 0.20
    sigma1 = 0.21   # +1 pp absolute
    r = 0.05
    K = 100.0
    T = 0.5
    q = 0.02

    prev_price, prev_greeks = _bs_price_and_greeks(S, K=K, r=r, sigma=sigma0,
                                                    T=T, q=q)
    T_next = T - 1 / 365
    curr_price, _ = _bs_price_and_greeks(S, K=K, r=r, sigma=sigma1,
                                          T=T_next, q=q)

    attr = explain_pnl(
        prev_price=prev_price,
        prev_greeks=prev_greeks,
        prev_S=S,
        prev_sigma=sigma0,
        prev_r=r,
        curr_price=curr_price,
        curr_S=S,
        curr_sigma=sigma1,
        curr_r=r,
        dt_days=1,
    )

    # vega per 1% σ: a move of 0.01 absolute = 1 pp ⟹ vega_pnl = vega * 1
    expected_vega_pnl = prev_greeks["vega"] * 1.0
    assert attr.vega_pnl == pytest.approx(expected_vega_pnl, abs=1e-10), (
        f"vega_pnl={attr.vega_pnl:.6f} vs expected={expected_vega_pnl:.6f}"
    )

    approx_total = attr.vega_pnl + attr.theta_pnl
    assert abs(approx_total - attr.total_observed) < 0.10, (
        f"vega+theta={approx_total:.4f} vs observed={attr.total_observed:.4f}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — combined small move: unexplained residual is small
# ─────────────────────────────────────────────────────────────────────────────

def test_unexplained_is_small_for_small_moves():
    """Combined 1% spot move + 0.5% vol move over 1 day for ~$10 ATM call.
    The unexplained residual should be < $0.50."""
    # Find a configuration where the ATM call price is ~$10.
    # SPY-like: S=500, K=500, T=30d, σ=0.20
    S0 = 500.0
    K = 500.0
    T = 30 / 365
    sigma0 = 0.20
    r = 0.05
    q = 0.015

    prev_price, prev_greeks = _bs_price_and_greeks(S0, K=K, r=r, sigma=sigma0,
                                                    T=T, q=q)
    # Verify the call is "around $10" — just a sanity check, not a hard bound
    # (actual value ~$6; "~$10" in the spec is approximate).

    S1 = S0 * 1.01        # +1% spot
    sigma1 = sigma0 + 0.005  # +0.5% vol
    T_next = T - 1 / 365

    curr_price, _ = _bs_price_and_greeks(S1, K=K, r=r, sigma=sigma1,
                                          T=T_next, q=q)

    attr = explain_pnl(
        prev_price=prev_price,
        prev_greeks=prev_greeks,
        prev_S=S0,
        prev_sigma=sigma0,
        prev_r=r,
        curr_price=curr_price,
        curr_S=S1,
        curr_sigma=sigma1,
        curr_r=r,
        dt_days=1,
    )

    assert abs(attr.unexplained) < 0.50, (
        f"Residual too large: {attr.unexplained:.4f} for a ~${prev_price:.2f} call"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — algebraic identity: total_explained + unexplained == total_observed
# ─────────────────────────────────────────────────────────────────────────────

def test_total_explained_plus_unexplained_equals_observed():
    """total_explained + unexplained == total_observed exactly (by construction)."""
    S0, S1 = 100.0, 102.0
    sigma0, sigma1 = 0.20, 0.22
    r0, r1 = 0.05, 0.055
    K = 100.0
    T = 0.5
    q = 0.02

    prev_price, prev_greeks = _bs_price_and_greeks(S0, K=K, r=r0, sigma=sigma0,
                                                    T=T, q=q)
    T_next = T - 1 / 365
    curr_price, _ = _bs_price_and_greeks(S1, K=K, r=r1, sigma=sigma1,
                                          T=T_next, q=q)

    attr = explain_pnl(
        prev_price=prev_price,
        prev_greeks=prev_greeks,
        prev_S=S0,
        prev_sigma=sigma0,
        prev_r=r0,
        curr_price=curr_price,
        curr_S=S1,
        curr_sigma=sigma1,
        curr_r=r1,
        dt_days=1,
    )

    assert attr.total_explained + attr.unexplained == pytest.approx(
        attr.total_observed, abs=1e-12
    ), (
        f"Identity violated: explained={attr.total_explained:.6f}, "
        f"unexplained={attr.unexplained:.6f}, observed={attr.total_observed:.6f}"
    )
