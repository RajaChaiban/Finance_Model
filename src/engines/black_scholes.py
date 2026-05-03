"""Black-Scholes pricing engine for European options."""

from typing import Dict

import numpy as np
from scipy.stats import norm


def _validate(S: float, K: float, sigma: float, T: float, option_type: str) -> str:
    """Bounds + case-insensitive option_type. Returns canonical 'call'/'put'.

    Silent NaN propagation from log(0) or division by zero is a worst-case
    failure mode for pricing code — it shows up downstream as garbage Greeks
    that look like real numbers. Catch it at the boundary.
    """
    if not (S > 0 and K > 0):
        raise ValueError(f"S and K must be positive, got S={S}, K={K}")
    if sigma <= 0:
        raise ValueError(
            f"σ must be > 0; for σ=0 take intrinsic value max(S-K, 0)·e^-rT explicitly"
        )
    if T <= 0:
        raise ValueError(
            f"T must be > 0; for expired options return max(intrinsic, 0)"
        )
    opt = option_type.lower() if isinstance(option_type, str) else option_type
    if opt not in ("call", "put"):
        # Previously, any string other than "call" silently fell through to put.
        # That includes "Call", "PUT", typos, and None — all turned into a
        # "put" silently. Tighten to a closed set with an explicit error.
        raise ValueError(f"option_type must be 'call' or 'put', got {option_type!r}")
    return opt


def _d1_d2(S: float, K: float, r: float, sigma: float, T: float, q: float) -> tuple:
    """Shared d1/d2 computation. Single source of truth for the BS conventions."""
    sqrtT = np.sqrt(T)
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    return d1, d2


def price_european(S: float, K: float, r: float, sigma: float, T: float, q: float = 0.0,
                   option_type: str = "put") -> float:
    """Price European option using Black-Scholes formula.

    Args:
        S: Spot price
        K: Strike price
        r: Risk-free rate
        sigma: Volatility (annual)
        T: Time to expiration (years)
        q: Dividend yield
        option_type: "put" or "call" (case-insensitive)

    Returns:
        Option price

    Raises:
        ValueError: If S, K <= 0; sigma <= 0; T <= 0; or option_type not in
            {"call", "put"} (case-insensitive).
    """
    opt = _validate(S, K, sigma, T, option_type)
    d1, d2 = _d1_d2(S, K, r, sigma, T, q)

    if opt == "call":
        return float(S * np.exp(-q * T) * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2))
    return float(K * np.exp(-r * T) * norm.cdf(-d2) - S * np.exp(-q * T) * norm.cdf(-d1))


def greeks_european(S: float, K: float, r: float, sigma: float, T: float, q: float = 0.0,
                    option_type: str = "put") -> Dict[str, float]:
    """Calculate Greeks for European option (analytical).

    Args:
        S, K, r, sigma, T, q: Option parameters
        option_type: "put" or "call" (case-insensitive)

    Returns:
        Dict with keys: delta, gamma, vega, theta, rho, price
    """
    opt = _validate(S, K, sigma, T, option_type)
    d1, d2 = _d1_d2(S, K, r, sigma, T, q)
    pdf_d1 = norm.pdf(d1)
    sqrtT = np.sqrt(T)
    discount = np.exp(-r * T)
    div_discount = np.exp(-q * T)

    if opt == "call":
        delta = div_discount * norm.cdf(d1)
        theta = (-S * div_discount * pdf_d1 * sigma / (2 * sqrtT)
                 - r * K * discount * norm.cdf(d2)
                 + q * S * div_discount * norm.cdf(d1)) / 365
        rho = K * T * discount * norm.cdf(d2) / 100
        price = S * div_discount * norm.cdf(d1) - K * discount * norm.cdf(d2)
    else:
        delta = -div_discount * norm.cdf(-d1)
        theta = (-S * div_discount * pdf_d1 * sigma / (2 * sqrtT)
                 + r * K * discount * norm.cdf(-d2)
                 - q * S * div_discount * norm.cdf(-d1)) / 365
        rho = -K * T * discount * norm.cdf(-d2) / 100
        price = K * discount * norm.cdf(-d2) - S * div_discount * norm.cdf(-d1)

    gamma = div_discount * pdf_d1 / (S * sigma * sqrtT)
    vega = S * div_discount * pdf_d1 * sqrtT / 100  # per 1% absolute σ

    return {
        "delta": float(delta),
        "gamma": float(gamma),
        "vega": float(vega),
        "theta": float(theta),
        "rho": float(rho),
        "price": float(price),
    }
