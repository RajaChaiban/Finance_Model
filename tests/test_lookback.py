"""Validation tests for lookback options.

Validation strategy mirrors ``tests/test_engine_consistency.py``:
  * TIGHT layer (1e-10): independent NumPy implementations of the published
    closed forms — Goldman-Sosin-Gatto 1979 (floating-strike) and
    Conze-Viswanathan 1991 (fixed-strike) — cross-checked against QuantLib's
    AnalyticContinuous{Floating,Fixed}LookbackEngine. Same day count, same
    evaluation date, no MC. Match must be numerically identical.
  * IDENTITY layer: dominance bounds (lookback >= vanilla), vol monotonicity,
    Greek-sign bounds — invariants that hold by construction.

The TIGHT layer is the "100% sure formula is correct" cross-check the spec
requires: a NumPy implementation of the original 1979 / 1991 formulas gives
an independent reference, not just QL self-consistency.
"""

import numpy as np
import pytest
import QuantLib as ql
from scipy.stats import norm

from src.engines import lookback, black_scholes


# ---------------------------------------------------------------------------
# Independent closed-form implementations (NumPy + scipy.stats.norm)
# ---------------------------------------------------------------------------

def _goldman_sosin_gatto_floating(S: float, M: float, r: float, q: float,
                                   sigma: float, T: float, opt: str) -> float:
    """Floating-strike lookback closed form (Goldman, Sosin, Gatto 1979).

    M is the running extremum: S_min for a call, S_max for a put.
    Requires b = r - q != 0 (the σ²/(2b) term is singular at b=0).

    Reference: Haug (2007), 'Complete Guide to Option Pricing Formulas', §4.1.
    """
    b = r - q
    if abs(b) < 1e-12:
        raise ValueError("b = r - q must be non-zero (formula singular)")
    sqrt_T = np.sqrt(T)
    sst = sigma * sqrt_T

    if opt == "call":
        a1 = (np.log(S / M) + (b + 0.5 * sigma ** 2) * T) / sst
        a2 = a1 - sst
        t1 = S * np.exp(-q * T) * norm.cdf(a1)
        t2 = -M * np.exp(-r * T) * norm.cdf(a2)
        bracket = (
            (S / M) ** (-2 * b / sigma ** 2) * norm.cdf(-a1 + 2 * b * sqrt_T / sigma)
            - np.exp(b * T) * norm.cdf(-a1)
        )
        t3 = S * np.exp(-r * T) * sigma ** 2 / (2 * b) * bracket
        return float(t1 + t2 + t3)

    # put
    b1 = (np.log(S / M) + (b + 0.5 * sigma ** 2) * T) / sst
    b2 = b1 - sst
    t1 = M * np.exp(-r * T) * norm.cdf(-b2)
    t2 = -S * np.exp(-q * T) * norm.cdf(-b1)
    bracket = (
        -(S / M) ** (-2 * b / sigma ** 2) * norm.cdf(b1 - 2 * b * sqrt_T / sigma)
        + np.exp(b * T) * norm.cdf(b1)
    )
    t3 = S * np.exp(-r * T) * sigma ** 2 / (2 * b) * bracket
    return float(t1 + t2 + t3)


def _conze_viswanathan_fixed(S: float, K: float, r: float, q: float,
                              sigma: float, T: float, opt: str,
                              M: float | None = None,
                              m: float | None = None) -> float:
    """Fixed-strike lookback closed form (Conze, Viswanathan 1991).

    M = running max (used for call), m = running min (used for put).
    Defaults to fresh option: M = m = S.
    Requires b = r - q != 0.

    Reference: Haug (2007), §4.2.
    """
    b = r - q
    if abs(b) < 1e-12:
        raise ValueError("b = r - q must be non-zero (formula singular)")
    if M is None:
        M = float(S)
    if m is None:
        m = float(S)
    sqrt_T = np.sqrt(T)
    sst = sigma * sqrt_T

    if opt == "call":
        if K >= M:
            d1 = (np.log(S / K) + (b + 0.5 * sigma ** 2) * T) / sst
            d2 = d1 - sst
            t1 = S * np.exp(-q * T) * norm.cdf(d1)
            t2 = -K * np.exp(-r * T) * norm.cdf(d2)
            bracket = (
                -(S / K) ** (-2 * b / sigma ** 2) * norm.cdf(d1 - 2 * b * sqrt_T / sigma)
                + np.exp(b * T) * norm.cdf(d1)
            )
            t3 = S * np.exp(-r * T) * sigma ** 2 / (2 * b) * bracket
            return float(t1 + t2 + t3)
        # K < M (already ITM via running max)
        intrinsic = (M - K) * np.exp(-r * T)
        e1 = (np.log(S / M) + (b + 0.5 * sigma ** 2) * T) / sst
        e2 = e1 - sst
        t1 = S * np.exp(-q * T) * norm.cdf(e1)
        t2 = -M * np.exp(-r * T) * norm.cdf(e2)
        bracket = (
            -(S / M) ** (-2 * b / sigma ** 2) * norm.cdf(e1 - 2 * b * sqrt_T / sigma)
            + np.exp(b * T) * norm.cdf(e1)
        )
        t3 = S * np.exp(-r * T) * sigma ** 2 / (2 * b) * bracket
        return float(intrinsic + t1 + t2 + t3)

    # put
    if K <= m:
        d1 = (np.log(S / K) + (b + 0.5 * sigma ** 2) * T) / sst
        d2 = d1 - sst
        t1 = K * np.exp(-r * T) * norm.cdf(-d2)
        t2 = -S * np.exp(-q * T) * norm.cdf(-d1)
        bracket = (
            (S / K) ** (-2 * b / sigma ** 2) * norm.cdf(-d1 + 2 * b * sqrt_T / sigma)
            - np.exp(b * T) * norm.cdf(-d1)
        )
        t3 = S * np.exp(-r * T) * sigma ** 2 / (2 * b) * bracket
        return float(t1 + t2 + t3)
    # K > m (already ITM via running min)
    intrinsic = (K - m) * np.exp(-r * T)
    f1 = (np.log(S / m) + (b + 0.5 * sigma ** 2) * T) / sst
    f2 = f1 - sst
    t1 = -S * np.exp(-q * T) * norm.cdf(-f1)
    t2 = m * np.exp(-r * T) * norm.cdf(-f2)
    bracket = (
        (S / m) ** (-2 * b / sigma ** 2) * norm.cdf(-f1 + 2 * b * sqrt_T / sigma)
        - np.exp(b * T) * norm.cdf(-f1)
    )
    t3 = S * np.exp(-r * T) * sigma ** 2 / (2 * b) * bracket
    return float(intrinsic + t1 + t2 + t3)


def _T_mat_yr(T_input: float) -> float:
    """Return the T (years) the engine actually uses, after day rounding."""
    days = max(int(T_input * 365.0 + 0.5), 1)
    return days / 365.0


# ---------------------------------------------------------------------------
# Layer 1 — TIGHT cross-check (1e-10)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("S, M", [(100, 95), (100, 100), (100, 105)])
@pytest.mark.parametrize("sigma", [0.15, 0.30])
@pytest.mark.parametrize("T", [0.25, 1.0])
@pytest.mark.parametrize("opt", ["call", "put"])
def test_floating_lookback_matches_gsg_numpy(S, M, sigma, T, opt):
    """Engine ≡ Goldman-Sosin-Gatto 1979 NumPy closed form, to 1e-10."""
    r, q = 0.05, 0.01
    # For the engine, K parameter == running extremum M.
    actual, _, _ = lookback.price_lookback(
        S, M, r, sigma, T, q,
        option_type=opt, lookback_type="floating",
    )
    T_eff = _T_mat_yr(T)
    expected = _goldman_sosin_gatto_floating(S, M, r, q, sigma, T_eff, opt)
    assert abs(actual - expected) < 1e-10, (
        f"GSG mismatch: actual={actual:.12f}, expected={expected:.12f}, "
        f"diff={actual - expected:.2e} (S={S}, M={M}, σ={sigma}, T={T}, opt={opt})"
    )


@pytest.mark.parametrize("S, K", [(100, 90), (100, 100), (100, 110)])
@pytest.mark.parametrize("sigma", [0.15, 0.30])
@pytest.mark.parametrize("T", [0.25, 1.0])
@pytest.mark.parametrize("opt", ["call", "put"])
def test_fixed_lookback_matches_cv_numpy(S, K, sigma, T, opt):
    """Engine ≡ Conze-Viswanathan 1991 NumPy closed form, to 1e-10."""
    r, q = 0.05, 0.01
    actual, _, _ = lookback.price_lookback(
        S, K, r, sigma, T, q,
        option_type=opt, lookback_type="fixed",
    )
    T_eff = _T_mat_yr(T)
    expected = _conze_viswanathan_fixed(S, K, r, q, sigma, T_eff, opt)
    assert abs(actual - expected) < 1e-10, (
        f"CV mismatch: actual={actual:.12f}, expected={expected:.12f}, "
        f"diff={actual - expected:.2e} (S={S}, K={K}, σ={sigma}, T={T}, opt={opt})"
    )


# ---------------------------------------------------------------------------
# Layer 2 — Identity / bound tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("opt", ["call", "put"])
@pytest.mark.parametrize("lt", ["fixed", "floating"])
def test_lookback_dominates_european(opt, lt):
    """Lookback (fixed or floating) >= European at the same K, fresh option."""
    S, K, r, sigma, T, q = 100.0, 100.0, 0.05, 0.20, 1.0, 0.01
    p_lookback, _, _ = lookback.price_lookback(
        S, K, r, sigma, T, q, option_type=opt, lookback_type=lt,
    )
    p_eur = black_scholes.price_european(S, K, r, sigma, T, q, opt)
    assert p_lookback >= p_eur - 1e-10, (
        f"Lookback < European: {p_lookback:.4f} vs {p_eur:.4f} ({lt} {opt})"
    )


def test_floating_call_intrinsic_bound():
    """Floating-strike call price >= S * exp(-qT) - M * exp(-rT)
    (the forward-style intrinsic of S_T - S_min, with S_min frozen at M)."""
    S, M, r, sigma, T, q = 100.0, 100.0, 0.05, 0.30, 1.0, 0.02
    p, _, _ = lookback.price_lookback(
        S, M, r, sigma, T, q, option_type="call", lookback_type="floating",
    )
    intrinsic_fwd = S * np.exp(-q * T) - M * np.exp(-r * T)
    assert p >= intrinsic_fwd - 1e-10, (
        f"Floating call below forward intrinsic: {p:.4f} < {intrinsic_fwd:.4f}"
    )


@pytest.mark.parametrize("opt", ["call", "put"])
@pytest.mark.parametrize("lt", ["fixed", "floating"])
def test_lookback_increases_with_volatility(opt, lt):
    """Lookback price strictly increases in σ (more dispersion → higher extrema)."""
    S, K, r, T, q = 100.0, 100.0, 0.05, 1.0, 0.01
    sigmas = [0.10, 0.20, 0.40]
    prices = [
        lookback.price_lookback(S, K, r, s, T, q, option_type=opt, lookback_type=lt)[0]
        for s in sigmas
    ]
    assert prices[0] < prices[1] < prices[2], (
        f"Vol monotonicity violated for {lt} {opt}: {prices}"
    )


@pytest.mark.parametrize("opt", ["call", "put"])
@pytest.mark.parametrize("lt", ["fixed", "floating"])
def test_lookback_greeks_bounds(opt, lt):
    """Delta and vega bound checks for lookbacks.

    Note: lookback delta does NOT obey the vanilla [-1, 1] bound because the
    payoff depends on a path extremum, not just S_T. Standard property: a
    fixed-strike lookback call near the running max has delta > 1 (bumping
    spot lifts BOTH S_T and S_max). A floating-strike put can have small
    positive delta (S_max increases with S, partially offsetting the loss
    in (S_max - S_T)). We only require finite, sane sign on the dominant
    Greeks: vega > 0 always; delta in [-2, 2] (well within finite range).
    """
    S, K, r, sigma, T, q = 100.0, 100.0, 0.05, 0.20, 1.0, 0.01
    g = lookback.greeks_lookback(
        S, K, r, sigma, T, q, option_type=opt, lookback_type=lt,
    )
    assert -2.0 < g["delta"] < 2.0, f"delta out of [-2, 2]: {g['delta']} ({lt} {opt})"
    assert g["vega"] > 0.0, f"vega should be positive: {g['vega']} ({lt} {opt})"
    # Sign check on the *dominant* component: call delta >= 0 (more spot →
    # more chance of hitting higher max for both fixed and floating call);
    # put delta has no clean sign for floating, but for fixed it's <= 0.
    if opt == "call":
        assert g["delta"] >= -1e-6, f"call delta should be >= 0: {g['delta']} ({lt})"
    if opt == "put" and lt == "fixed":
        assert g["delta"] <= 1e-6, f"fixed put delta should be <= 0: {g['delta']}"


def test_short_T_lookback_premium_finite():
    """At T → 0, the lookback price → 0 in absolute terms (premium / S → 0).

    Note: the lookback / European RATIO does NOT go to 1 at short T because
    the σ²/(2b) prefactor of the extremum-tracking term remains O(1). The
    correct limit is the absolute one: both prices vanish proportionally.
    """
    S, K, r, sigma, q = 100.0, 100.0, 0.05, 0.20, 0.01
    T = 1.0 / 365.0
    p_fixed, _, _ = lookback.price_lookback(
        S, K, r, sigma, T, q, option_type="call", lookback_type="fixed",
    )
    # Absolute scale: lookback premium / spot < 1% at 1d.
    assert p_fixed / S < 0.01, (
        f"1-day lookback price too large in absolute terms: {p_fixed:.4f} / {S}"
    )
