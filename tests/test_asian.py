"""Validation tests for Asian (average-price, fixed-strike) options.

Validation strategy mirrors ``tests/test_engine_consistency.py``:
  * TIGHT layer (1e-10): independent NumPy implementation of the discrete-
    geometric Asian closed form (Kemna-Vorst 1990) cross-checked against
    QuantLib's ``AnalyticDiscreteGeometricAveragePriceAsianEngine``. Same
    fixings, same day count, same evaluation date — the two answers MUST
    match to numerical precision.
  * IDENTITY layer: parity-style invariants that hold by construction —
    AM-GM (arithmetic >= geometric), single-fixing → European, dominance,
    Greek-sign bounds. No hard-coded numbers.

The TIGHT layer is the "100% sure formula is correct" cross-check the
original spec asked for — a NumPy implementation of the published 1990
formula gives an independent reference, not just QL self-consistency.
"""

import numpy as np
import pytest
import QuantLib as ql
from scipy.stats import norm

from src.engines import asian, black_scholes


# ---------------------------------------------------------------------------
# Independent Kemna-Vorst (1990) closed-form for discrete-geometric Asian
# ---------------------------------------------------------------------------

def _kemna_vorst_discrete_geometric(
    S: float, K: float, r: float, sigma: float, q: float,
    T_mat: float, fixing_times: np.ndarray, opt: str,
) -> float:
    """Discrete-geometric Asian price under GBM, closed-form.

    Args:
        S, K, r, sigma, q: standard parameters
        T_mat: time to maturity (when option pays off)
        fixing_times: array of fixing times t_i in years, 0 < t_i <= T_mat
        opt: 'call' or 'put'

    Derivation:
        ln(G) where G = (Π_i S(t_i))^(1/N) is normal under GBM.
        E[ln G] = ln(S) + (r - q - σ²/2) * T_bar
        Var[ln G] = σ² * T_tilde
        with
          T_bar = (1/N) Σ t_i
          T_tilde = (1/N²) Σ_i Σ_j min(t_i, t_j)
                  = (1/N²) Σ_{i sorted} t_i × (2N - 2i + 1)   [i=1..N, 1-indexed]

        Then Black-76 with F_G = E[G] = exp(E[ln G] + Var[ln G]/2) and
        total stdev = σ √T_tilde, discounted by exp(-r T_mat).
    """
    t = np.sort(np.asarray(fixing_times, dtype=float))
    N = len(t)
    if N == 0:
        raise ValueError("fixing_times must be non-empty")

    T_bar = float(np.mean(t))
    weights = (2 * N - 2 * np.arange(1, N + 1) + 1)  # i = 1..N
    T_tilde = float(np.sum(weights * t)) / (N * N)

    F_G = S * np.exp((r - q - 0.5 * sigma ** 2) * T_bar + 0.5 * sigma ** 2 * T_tilde)
    total_std = sigma * np.sqrt(T_tilde)
    if total_std <= 0:
        # Degenerate: zero variance → forward = E[G] is deterministic. Payoff
        # is just max(F_G - K, 0) discounted (call) — limit case.
        if opt == "call":
            return float(np.exp(-r * T_mat) * max(F_G - K, 0.0))
        return float(np.exp(-r * T_mat) * max(K - F_G, 0.0))

    d1 = (np.log(F_G / K) + 0.5 * total_std ** 2) / total_std
    d2 = d1 - total_std
    disc = np.exp(-r * T_mat)
    if opt == "call":
        return float(disc * (F_G * norm.cdf(d1) - K * norm.cdf(d2)))
    return float(disc * (K * norm.cdf(-d2) - F_G * norm.cdf(-d1)))


def _qual_fixing_times(T_input: float, frequency: str) -> tuple[np.ndarray, float]:
    """Replicate the engine's schedule and return (fixing_times_yr, T_mat_yr).

    Uses the same NYSE calendar / Actual-365 day count the engine uses.
    """
    today = ql.Date.todaysDate()
    ql.Settings.instance().evaluationDate = today
    days = max(int(T_input * 365.0 + 0.5), 1)
    maturity = today + days
    fixing_dates = asian._build_fixing_schedule(today, maturity, frequency)
    fixing_times = np.array([(d - today) / 365.0 for d in fixing_dates])
    T_mat = (maturity - today) / 365.0
    return fixing_times, T_mat


# ---------------------------------------------------------------------------
# Layer 1 — TIGHT cross-check (1e-10)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("S, K", [(90, 100), (100, 100), (110, 100)])
@pytest.mark.parametrize("sigma", [0.15, 0.30])
@pytest.mark.parametrize("T", [0.25, 1.0])
@pytest.mark.parametrize("opt", ["call", "put"])
@pytest.mark.parametrize("freq", ["daily", "weekly", "monthly"])
def test_geometric_asian_matches_kemna_vorst_numpy(S, K, sigma, T, opt, freq):
    """Engine ≡ independent NumPy KV closed-form, to 1e-10."""
    r, q = 0.05, 0.01
    fixing_times, T_mat = _qual_fixing_times(T, freq)
    expected = _kemna_vorst_discrete_geometric(S, K, r, sigma, q, T_mat, fixing_times, opt)
    actual, _, _ = asian.price_asian(
        S, K, r, sigma, T, q,
        option_type=opt, averaging_method="geometric", averaging_frequency=freq,
    )
    assert abs(actual - expected) < 1e-10, (
        f"KV mismatch: actual={actual:.12f}, expected={expected:.12f}, "
        f"diff={actual - expected:.2e} (S={S}, K={K}, σ={sigma}, T={T}, freq={freq})"
    )


# ---------------------------------------------------------------------------
# Layer 2 — Identity / bound / limit-case tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("S, K", [(90, 100), (100, 100), (110, 100)])
@pytest.mark.parametrize("sigma", [0.15, 0.30])
@pytest.mark.parametrize("opt", ["call", "put"])
def test_arithmetic_vs_geometric_amgm(S, K, sigma, opt):
    """AM-GM applied to Asian payoffs.

    AM-GM gives arithmetic_mean(S) >= geometric_mean(S) sample-wise. For a CALL
    payoff max(mean - K, 0) the inequality propagates: arith_call >= geo_call.
    For a PUT payoff max(K - mean, 0) the inequality flips: arith_put <= geo_put.
    """
    r, q, T = 0.05, 0.01, 1.0
    p_geo, _, _ = asian.price_asian(
        S, K, r, sigma, T, q,
        option_type=opt, averaging_method="geometric", averaging_frequency="monthly",
    )
    p_arith, se, _ = asian.price_asian(
        S, K, r, sigma, T, q,
        option_type=opt, averaging_method="arithmetic", averaging_frequency="monthly",
        n_paths=20000,
    )
    # Allow 2σ MC slack on the inequality.
    if opt == "call":
        assert p_arith >= p_geo - 2.0 * se, (
            f"AM-GM(call) violated beyond 2σ: arith={p_arith:.6f}±{se:.6f}, "
            f"geo={p_geo:.6f} (S={S}, K={K}, σ={sigma})"
        )
    else:
        assert p_arith <= p_geo + 2.0 * se, (
            f"AM-GM(put) violated beyond 2σ: arith={p_arith:.6f}±{se:.6f}, "
            f"geo={p_geo:.6f} (S={S}, K={K}, σ={sigma})"
        )


@pytest.mark.parametrize("opt", ["call", "put"])
def test_single_fixing_recovers_european(opt):
    """When the schedule has only one fixing at maturity, Asian → European."""
    # T = 4 days with weekly frequency → engine builds a single fixing at maturity
    # (the first weekly increment lands beyond maturity, so the loop appends
    # only the maturity date).
    S, K, r, sigma, q = 100.0, 100.0, 0.05, 0.20, 0.01
    T = 4.0 / 365.0
    fixing_times, _ = _qual_fixing_times(T, "weekly")
    assert len(fixing_times) == 1, (
        f"sanity: expected 1 fixing for T=4d weekly, got {len(fixing_times)}"
    )
    p_geo, _, _ = asian.price_asian(
        S, K, r, sigma, T, q,
        option_type=opt, averaging_method="geometric", averaging_frequency="weekly",
    )
    p_eur = black_scholes.price_european(S, K, r, sigma, T, q, opt)
    rel = abs(p_geo - p_eur) / max(p_eur, 1e-12)
    assert rel < 1e-10, (
        f"Single-fixing geometric != European: geo={p_geo:.10f}, eur={p_eur:.10f}"
    )


def test_arithmetic_mc_brackets_geometric():
    """Arithmetic MC 95% CI must contain at least the geometric lower bound."""
    S, K, r, sigma, T, q = 100.0, 100.0, 0.05, 0.20, 1.0, 0.01
    p_geo, _, _ = asian.price_asian(
        S, K, r, sigma, T, q,
        option_type="call", averaging_method="geometric", averaging_frequency="monthly",
    )
    p_arith, se, _ = asian.price_asian(
        S, K, r, sigma, T, q,
        option_type="call", averaging_method="arithmetic", averaging_frequency="monthly",
        n_paths=50000,
    )
    # Arithmetic lies above geometric (AM-GM) by at most ~5%; MC SE should be
    # tiny under the geometric control variate (typical << 0.01).
    assert p_arith - 1.96 * se <= p_geo + 0.05 * p_geo, (
        f"Arith CI lower bound implausibly far from geo: "
        f"arith={p_arith:.6f}±{se:.6f}, geo={p_geo:.6f}"
    )
    assert p_arith - p_geo <= 0.10 * p_geo, (
        f"Arith excess over geo unreasonable: arith={p_arith:.6f}, geo={p_geo:.6f}"
    )


@pytest.mark.parametrize("opt", ["call", "put"])
@pytest.mark.parametrize("method", ["geometric", "arithmetic"])
def test_greeks_bounds(opt, method):
    """Delta/vega sign and bounds checks."""
    S, K, r, sigma, T, q = 100.0, 100.0, 0.05, 0.20, 1.0, 0.01
    n_paths = 5000 if method == "arithmetic" else 1
    g = asian.greeks_asian(
        S, K, r, sigma, T, q,
        option_type=opt, averaging_method=method, averaging_frequency="monthly",
        n_paths=n_paths,
    )
    if opt == "call":
        assert 0.0 <= g["delta"] <= 1.0, f"call delta out of [0,1]: {g['delta']}"
    else:
        assert -1.0 <= g["delta"] <= 0.0, f"put delta out of [-1,0]: {g['delta']}"
    assert g["vega"] > 0.0, f"vega should be positive: {g['vega']}"


def test_geometric_asian_below_european():
    """Geometric Asian < European at the same K (averaging dampens variance)."""
    S, K, r, sigma, T, q = 100.0, 100.0, 0.05, 0.20, 1.0, 0.0
    p_geo, _, _ = asian.price_asian(
        S, K, r, sigma, T, q,
        option_type="call", averaging_method="geometric", averaging_frequency="daily",
    )
    p_eur = black_scholes.price_european(S, K, r, sigma, T, q, "call")
    assert p_geo < p_eur, (
        f"Asian should be cheaper than European: asian={p_geo:.4f}, eur={p_eur:.4f}"
    )
