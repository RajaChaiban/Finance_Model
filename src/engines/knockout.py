"""Knockout (barrier) option pricing engine.

Implements the Reiner-Rubinstein closed-form for continuously-monitored
single-barrier options under GBM with no rebate. Direction (Down vs Up)
is inferred from the barrier level relative to spot.
"""

import numpy as np
from scipy.stats import norm
from . import black_scholes


def price_knockout(S: float, K: float, B: float, r: float, sigma: float, T: float, q: float = 0,
                   option_type: str = "call") -> tuple:
    """Price a knock-out option via Reiner-Rubinstein.

    Args:
        S: Spot price
        K: Strike price
        B: Barrier level (B < S => Down-and-Out; B > S => Up-and-Out)
        r: Risk-free rate
        sigma: Volatility (annual)
        T: Time to expiration (years)
        q: Dividend yield
        option_type: "call" or "put"

    Returns:
        (knockout_price, vanilla_price, barrier_discount_ratio, lambda_param)
    """
    if option_type.lower() not in ("call", "put"):
        raise ValueError("option_type must be 'call' or 'put'")

    vanilla = black_scholes.price_european(S, K, r, sigma, T, q, option_type)

    phi = 1.0 if option_type.lower() == "call" else -1.0
    eta = 1.0 if B < S else -1.0  # +1 Down barrier, -1 Up barrier

    sqT = sigma * np.sqrt(T)
    lam = (r - q + 0.5 * sigma ** 2) / (sigma ** 2)

    x1 = np.log(S / K) / sqT + lam * sqT
    x2 = np.log(S / B) / sqT + lam * sqT
    y1 = np.log(B ** 2 / (S * K)) / sqT + lam * sqT
    y2 = np.log(B / S) / sqT + lam * sqT

    A = (phi * S * np.exp(-q * T) * norm.cdf(phi * x1)
         - phi * K * np.exp(-r * T) * norm.cdf(phi * x1 - phi * sqT))
    Bt = (phi * S * np.exp(-q * T) * norm.cdf(phi * x2)
          - phi * K * np.exp(-r * T) * norm.cdf(phi * x2 - phi * sqT))
    C = (phi * S * np.exp(-q * T) * (B / S) ** (2 * lam) * norm.cdf(eta * y1)
         - phi * K * np.exp(-r * T) * (B / S) ** (2 * lam - 2) * norm.cdf(eta * y1 - eta * sqT))
    D = (phi * S * np.exp(-q * T) * (B / S) ** (2 * lam) * norm.cdf(eta * y2)
         - phi * K * np.exp(-r * T) * (B / S) ** (2 * lam - 2) * norm.cdf(eta * y2 - eta * sqT))

    # Down-and-Out (B < S, eta=+1)
    if eta == 1.0 and phi == 1.0:                  # DO call
        price = (A - C) if K > B else (Bt - D)
    elif eta == 1.0 and phi == -1.0:               # DO put
        price = (A - Bt + C - D) if K > B else 0.0
    elif eta == -1.0 and phi == 1.0:               # UO call
        price = 0.0 if K > B else (A - Bt + C - D)
    else:                                           # UO put
        price = (Bt - D) if K > B else (A - C)

    price = max(float(price), 0.0)
    adjustment = price / vanilla if vanilla > 0 else 1.0
    return price, float(vanilla), float(adjustment), float(lam)


def greeks_knockout(S: float, K: float, B: float, r: float, sigma: float, T: float, q: float = 0,
                    option_type: str = "call") -> dict:
    """Calculate Greeks for knockout option (bump-and-reprice).

    Args:
        S, K, B, r, sigma, T, q, option_type: Option parameters

    Returns:
        Dict with keys: delta, gamma, vega, theta, rho, barrier_discount
    """
    # Base case
    price_base, vanilla_base, adj_base, _ = price_knockout(S, K, B, r, sigma, T, q, option_type)

    # Delta
    bump_pct = 0.01
    S_up = S * (1 + bump_pct)
    S_down = S * (1 - bump_pct)
    price_up, _, _, _ = price_knockout(S_up, K, B, r, sigma, T, q, option_type)
    price_down, _, _, _ = price_knockout(S_down, K, B, r, sigma, T, q, option_type)
    delta = (price_up - price_down) / (S_up - S_down)

    # Gamma
    price_base_2, _, _, _ = price_knockout(S, K, B, r, sigma, T, q, option_type)
    delta_up = (price_up - price_base_2) / (S_up - S)
    delta_down = (price_base_2 - price_down) / (S - S_down)
    gamma = (delta_up - delta_down) / (S_up - S_down)

    # Vega
    vol_bump = 0.01
    price_vol_up, _, _, _ = price_knockout(S, K, B, r, sigma + vol_bump, T, q, option_type)
    vega = (price_vol_up - price_base) / vol_bump / 100

    # Theta
    T_down = max(T - 1 / 365, 0.001)
    price_t_down, _, _, _ = price_knockout(S, K, B, r, sigma, T_down, q, option_type)
    theta = (price_t_down - price_base) / (T_down - T)

    # Rho
    rate_bump = 0.01
    price_r_up, _, _, _ = price_knockout(S, K, B, r + rate_bump, sigma, T, q, option_type)
    rho = (price_r_up - price_base) / rate_bump / 100

    # Barrier discount
    barrier_discount_pct = (1 - adj_base) * 100

    return {
        "delta": float(delta),
        "gamma": float(gamma),
        "vega": float(vega),
        "theta": float(theta),
        "rho": float(rho),
        "price": float(price_base),
        "vanilla_price": float(vanilla_base),
        "barrier_discount_pct": float(barrier_discount_pct),
    }
