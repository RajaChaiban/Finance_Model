"""Digital option pricers — cash-or-nothing and asset-or-nothing.

Closed-form Black-Scholes. These are the basic binary options that pay a
fixed amount (or the asset) if the underlying finishes ITM.

Conventions match the rest of the repo:
- Vega per 1% absolute σ (i.e. analytic vega / 100)
- Theta per calendar day (analytic theta / 365)
- Rho per 1% absolute r (analytic rho / 100)

Used by:
- Router (option_type = "digital_call" | "digital_put")
- Strategist's shark-fin rule (call spread + UO digital cap).
"""

from __future__ import annotations

import math
from typing import Optional

from scipy.stats import norm


def _d2(S: float, K: float, r: float, sigma: float, T: float, q: float = 0.0) -> float:
    return (math.log(S / K) + (r - q - 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))


def price_digital_cash(
    S: float, K: float, r: float, sigma: float, T: float, q: float = 0.0,
    option_type: str = "call", cash_payout: float = 1.0,
) -> tuple[float, float, None]:
    """Cash-or-nothing digital. Pays ``cash_payout`` if ITM at T, else 0.

    BS price (call):  K_cash · exp(−rT) · N(d2)
    BS price (put):   K_cash · exp(−rT) · N(−d2)
    """
    if T <= 0 or sigma <= 0:
        intrinsic = (S > K) if option_type == "call" else (S < K)
        return float(cash_payout if intrinsic else 0.0), 0.0, None
    d2 = _d2(S, K, r, sigma, T, q)
    if option_type == "call":
        price = cash_payout * math.exp(-r * T) * norm.cdf(d2)
    else:
        price = cash_payout * math.exp(-r * T) * norm.cdf(-d2)
    return float(price), 0.0, None


def greeks_digital_cash(
    S: float, K: float, r: float, sigma: float, T: float, q: float = 0.0,
    option_type: str = "call", cash_payout: float = 1.0,
) -> dict:
    """Greeks for cash-or-nothing digital.

    Use bump-and-reprice for delta/gamma/vega (the analytic formulas have
    pin-risk-style spikes near the strike that propagate badly through
    finite-difference at small bumps; bump-reprice the closed-form gives a
    smoother answer for risk reporting).
    """
    if T <= 0 or sigma <= 0:
        return {
            "price": price_digital_cash(S, K, r, sigma, T, q, option_type, cash_payout)[0],
            "delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0, "rho": 0.0,
        }

    p0 = price_digital_cash(S, K, r, sigma, T, q, option_type, cash_payout)[0]
    h_S = max(S * 0.01, 0.01)
    h_sigma = 0.01
    h_r = 0.0001
    dt = 1.0 / 365.0

    p_up = price_digital_cash(S + h_S, K, r, sigma, T, q, option_type, cash_payout)[0]
    p_dn = price_digital_cash(S - h_S, K, r, sigma, T, q, option_type, cash_payout)[0]
    delta = (p_up - p_dn) / (2 * h_S)
    gamma = (p_up - 2 * p0 + p_dn) / (h_S ** 2)

    p_v_up = price_digital_cash(S, K, r, sigma + h_sigma, T, q, option_type, cash_payout)[0]
    p_v_dn = price_digital_cash(S, K, r, sigma - h_sigma, T, q, option_type, cash_payout)[0]
    # Vega: per 1% σ (i.e. /100 of the analytic per-unit-σ value).
    vega = (p_v_up - p_v_dn) / (2 * h_sigma) / 100.0

    p_t = price_digital_cash(S, K, r, sigma, max(T - dt, 1e-9), q, option_type, cash_payout)[0]
    theta = (p_t - p0) / 1.0  # already per calendar day

    p_r_up = price_digital_cash(S, K, r + h_r, sigma, T, q, option_type, cash_payout)[0]
    p_r_dn = price_digital_cash(S, K, r - h_r, sigma, T, q, option_type, cash_payout)[0]
    rho = (p_r_up - p_r_dn) / (2 * h_r) / 100.0

    return {
        "price": p0, "delta": delta, "gamma": gamma,
        "vega": vega, "theta": theta, "rho": rho,
    }


def price_digital_asset(
    S: float, K: float, r: float, sigma: float, T: float, q: float = 0.0,
    option_type: str = "call",
) -> tuple[float, float, None]:
    """Asset-or-nothing digital. Pays S(T) if ITM, else 0.

    BS price (call):  S · exp(−qT) · N(d1)
    BS price (put):   S · exp(−qT) · N(−d1)
    """
    if T <= 0 or sigma <= 0:
        intrinsic = (S > K) if option_type == "call" else (S < K)
        return float(S if intrinsic else 0.0), 0.0, None
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    if option_type == "call":
        price = S * math.exp(-q * T) * norm.cdf(d1)
    else:
        price = S * math.exp(-q * T) * norm.cdf(-d1)
    return float(price), 0.0, None


def price_shark_fin(
    S: float, K_low: float, K_high: float, B: float, r: float, sigma: float, T: float,
    q: float = 0.0, side: str = "call",
) -> tuple[float, float, None]:
    """Shark fin = capped call spread + knock-out cap.

    Builds the position as: long call(K_low) − long call(K_high) − UO call(K_high).
    Uses analytic European calls plus a digital approximation for the UO cap.
    Indicative pricing only — production should use the FDM barrier engine for
    the UO leg.
    """
    from .black_scholes import price_european
    if side != "call":
        raise NotImplementedError("Only call-side shark fins implemented; put-side is symmetric.")
    long_K_low = price_european(S, K_low, r, sigma, T, q, "call")
    short_K_high = price_european(S, K_high, r, sigma, T, q, "call")
    # Cap leg: rough approximation as a digital that pays (B − K_high) ITM at B.
    cap = price_digital_cash(S, B, r, sigma, T, q, "call", cash_payout=max(B - K_high, 0.0))[0]
    price = long_K_low - short_K_high - cap
    return float(price), 0.0, None
