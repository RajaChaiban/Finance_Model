"""Black-Scholes pricing engine for European options."""

import numpy as np
from scipy.stats import norm


def price_european(S: float, K: float, r: float, sigma: float, T: float, q: float = 0,
                   option_type: str = "put") -> float:
    """Price European option using Black-Scholes formula.

    Args:
        S: Spot price
        K: Strike price
        r: Risk-free rate
        sigma: Volatility (annual)
        T: Time to expiration (years)
        q: Dividend yield
        option_type: "put" or "call"

    Returns:
        Option price
    """
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)

    if option_type == "call":
        price = S * np.exp(-q * T) * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    else:  # put
        price = K * np.exp(-r * T) * norm.cdf(-d2) - S * np.exp(-q * T) * norm.cdf(-d1)

    return price


def greeks_european(S: float, K: float, r: float, sigma: float, T: float, q: float = 0,
                    option_type: str = "put") -> dict:
    """Calculate Greeks for European option (analytical).

    Args:
        S, K, r, sigma, T, q: Option parameters
        option_type: "put" or "call"

    Returns:
        Dict with keys: delta, gamma, vega, theta, rho
    """
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)

    pdf_d1 = norm.pdf(d1)

    # Greeks (same for put and call where noted)
    if option_type == "call":
        delta = np.exp(-q * T) * norm.cdf(d1)
        theta = (-S * np.exp(-q * T) * pdf_d1 * sigma / (2 * np.sqrt(T))
                 - r * K * np.exp(-r * T) * norm.cdf(d2)
                 + q * S * np.exp(-q * T) * norm.cdf(d1)) / 365
    else:  # put
        delta = -np.exp(-q * T) * norm.cdf(-d1)
        theta = (-S * np.exp(-q * T) * pdf_d1 * sigma / (2 * np.sqrt(T))
                 + r * K * np.exp(-r * T) * norm.cdf(-d2)
                 - q * S * np.exp(-q * T) * norm.cdf(-d1)) / 365

    # Same for both
    gamma = np.exp(-q * T) * pdf_d1 / (S * sigma * np.sqrt(T))
    vega = S * np.exp(-q * T) * pdf_d1 * np.sqrt(T) / 100  # per 1% change
    rho = K * T * np.exp(-r * T) * norm.cdf(d2 if option_type == "call" else -d2) / 100  # per 1% change

    return {
        "delta": float(delta),
        "gamma": float(gamma),
        "vega": float(vega),
        "theta": float(theta),
        "rho": float(rho),
        "price": price_european(S, K, r, sigma, T, q, option_type),
    }
