"""Variance swap fair-strike via log-contract replication.

A variance swap pays:  N · (σ_realised² − K_var²)
where K_var² is the fair strike at inception and N is vega notional / (2 K_var).

The fair strike under risk-neutral pricing equals the variance swap rate — i.e.
the price of a *log-contract* on the underlying:

    K_var² = (2/T) · [ rT − (S0/F − 1) − ln(F/S0)
                       + ∫_0^F P(K)/K² dK + ∫_F^∞ C(K)/K² dK ]

For a flat surface this reduces to σ². Under skew, the wing OTM-puts dominate
and K_var typically prints above the ATM IV by a few vol-points (the "var-vol
spread").

This implementation takes a **vol strip** (a list of (K, σ_BS) pairs across
strikes for a single tenor) and replicates the log-contract numerically. It's
the dealer-desk shortcut: forget the analytic CMF formula and just integrate
the vol strip you already have.

Vol-swap convexity adjustment: K_vol ≈ K_var · √(1 − var_of_var/(8·K_var²)).
We use a rough empirical proxy (var_of_var ≈ 0.6·K_var²) — production should
estimate this from the vol-of-vol surface or VIX futures.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.stats import norm


@dataclass
class VarianceSwapResult:
    fair_strike_var: float        # K_var (vol units, e.g. 0.18 = 18%)
    fair_strike_vol: float        # K_vol after convexity adjustment
    atm_iv: float
    var_minus_atm_bps: float      # K_var − atm_iv, in bps (typical: +50–200bps)
    method: str = "log-contract-replication"

    def to_dict(self) -> dict:
        return {
            "fair_strike_var": self.fair_strike_var,
            "fair_strike_vol": self.fair_strike_vol,
            "atm_iv": self.atm_iv,
            "var_minus_atm_bps": self.var_minus_atm_bps,
            "method": self.method,
        }


def _bs_price(S: float, K: float, r: float, sigma: float, T: float, q: float, side: str) -> float:
    if sigma <= 0 or T <= 0:
        return max(0.0, (S - K) if side == "call" else (K - S))
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if side == "call":
        return S * math.exp(-q * T) * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * math.exp(-q * T) * norm.cdf(-d1)


def fair_strike_from_strip(
    *,
    S: float, r: float, q: float, T: float,
    strikes: np.ndarray, ivs: np.ndarray,
) -> VarianceSwapResult:
    """Compute the fair variance-swap strike from a single-tenor vol strip.

    Parameters
    ----------
    strikes:  Array of strikes (must include the forward).
    ivs:      Black-Scholes IVs aligned with strikes.

    Returns
    -------
    VarianceSwapResult — K_var, K_vol, plus diagnostics.
    """
    strikes = np.asarray(strikes, dtype=float)
    ivs = np.asarray(ivs, dtype=float)
    if len(strikes) != len(ivs) or len(strikes) < 4:
        raise ValueError("Need at least 4 strike/IV pairs to replicate the log contract.")
    order = np.argsort(strikes)
    strikes = strikes[order]
    ivs = ivs[order]

    F = S * math.exp((r - q) * T)
    # ATM IV by interpolation at the forward.
    atm_iv = float(np.interp(F, strikes, ivs))

    # Replicating portfolio: OTM puts below F (use put quotes), OTM calls above F.
    # Trapezoid integration of present-value option price / K² across the strike grid.
    integral = 0.0
    for i in range(len(strikes) - 1):
        K0, K1 = strikes[i], strikes[i + 1]
        sigma0, sigma1 = ivs[i], ivs[i + 1]
        side0 = "put" if K0 < F else "call"
        side1 = "put" if K1 < F else "call"
        p0 = _bs_price(S, K0, r, sigma0, T, q, side0)
        p1 = _bs_price(S, K1, r, sigma1, T, q, side1)
        f0 = p0 / (K0 ** 2)
        f1 = p1 / (K1 ** 2)
        integral += 0.5 * (f0 + f1) * (K1 - K0)

    # Carr-Madan log-contract identity (with K* = F, i.e. boundary at the forward):
    #
    #     σ² = (2/T) · e^(r·T) · ∫_0^∞ Q(K)/K² dK
    #
    # where Q(K) is the present-value OTM option price. The "extra"
    # rT − (S/F − 1) − ln(F/S) terms vanish when the boundary is the forward
    # — they're only present when K* ≠ F.
    var_strike_sq = (2.0 / T) * math.exp(r * T) * integral
    if var_strike_sq <= 0:
        # Degenerate strip — fall back to the ATM IV.
        var_strike = atm_iv
    else:
        var_strike = math.sqrt(var_strike_sq)

    # Vol-swap convexity adjustment (rough proxy).
    var_of_var = 0.6 * var_strike ** 2  # empirical; v2 should estimate properly
    convexity_term = max(1.0 - var_of_var / (8.0 * var_strike ** 2), 0.0)
    vol_strike = var_strike * math.sqrt(convexity_term)

    return VarianceSwapResult(
        fair_strike_var=var_strike,
        fair_strike_vol=vol_strike,
        atm_iv=atm_iv,
        var_minus_atm_bps=(var_strike - atm_iv) * 10_000.0,
    )


def fair_strike_flat(sigma: float) -> VarianceSwapResult:
    """Degenerate single-σ case — K_var = σ (no skew contribution)."""
    return VarianceSwapResult(
        fair_strike_var=sigma,
        fair_strike_vol=sigma,
        atm_iv=sigma,
        var_minus_atm_bps=0.0,
        method="flat-vol",
    )
