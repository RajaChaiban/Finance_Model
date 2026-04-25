"""Knockout (barrier) option pricing engine."""

import numpy as np
from . import black_scholes


def price_knockout(S: float, K: float, B: float, r: float, sigma: float, T: float, q: float = 0,
                   option_type: str = "call") -> tuple:
    """Price knockout (barrier) option using Merton formula.

    The knockout price is: vanilla_price * (B/S)^(2*lambda - 1)
    where lambda = (r - q + 0.5*sigma^2) / sigma^2

    Args:
        S: Spot price
        K: Strike price
        B: Barrier level
        r: Risk-free rate
        sigma: Volatility (annual)
        T: Time to expiration (years)
        q: Dividend yield
        option_type: "call" or "put"

    Returns:
        (knockout_price, vanilla_price, barrier_adjustment, lambda_param)
    """
    # Vanilla option price
    vanilla = black_scholes.price_european(S, K, r, sigma, T, q, option_type)

    # Merton barrier adjustment
    lambda_param = (r - q + 0.5 * sigma ** 2) / (sigma ** 2)
    barrier_ratio = B / S
    adjustment = barrier_ratio ** (2 * lambda_param - 1)

    knockout = vanilla * adjustment

    return knockout, vanilla, adjustment, lambda_param


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
