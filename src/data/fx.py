"""FX vanilla pricing — Garman-Kohlhagen (skeleton).

This is the FX cousin of Black-Scholes: replace the dividend yield q with the
foreign risk-free rate r_f. The domestic currency is the "spot" currency the
notional is paid in, and the foreign currency is the asset.

Notation:
- S       : spot quote, ccy_dom per 1 unit of ccy_for (e.g. USD/EUR = 1.08)
- r_d     : domestic risk-free rate (continuous)
- r_f     : foreign risk-free rate (continuous)
- σ       : log-spot volatility
- K       : strike, in ccy_dom per ccy_for
- T       : maturity in years

The price of a EUR call USD put (right to BUY 1 EUR for K USD at T) is:
    S · exp(−r_f T) · N(d1) − K · exp(−r_d T) · N(d2)

This is what the market calls a "EURUSD call" by convention — *call on the
base currency*.

Status: SKELETON. v2 needs:
- FX vol-surface conventions (delta-strike rather than absolute strike;
  ATM-DNS straddle vs. forward, 25Δ risk reversal, butterfly).
- Premium-included delta (FX desks quote premium-included delta, not the
  Black-Scholes delta returned here).
- Cut-off conventions (NY cut, Tokyo cut).
- Multi-leg FX (touch/no-touch) requires barrier engine adapted for FX.

Use this module for indicative pricing on plain FX vanillas. Anything more
exotic (touch options, target-redemption forwards, accumulators) needs a
proper FX engine.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from scipy.stats import norm


@dataclass
class FXQuote:
    """An FX-rate quote: ccy_dom per 1 unit of ccy_for."""
    ccy_for: str                      # base ccy (the asset)
    ccy_dom: str                      # quote ccy (the cash)
    spot: float
    forward_points_to_T: float = 0.0  # outright forward = spot + points

    @property
    def pair(self) -> str:
        return f"{self.ccy_for}{self.ccy_dom}"

    def forward(self) -> float:
        return self.spot + self.forward_points_to_T


def price_fx_vanilla(
    *,
    S: float, K: float, T: float,
    r_d: float, r_f: float,
    sigma: float,
    side: Literal["call", "put"] = "call",
) -> tuple[float, dict]:
    """Garman-Kohlhagen FX vanilla.

    Returns
    -------
    (price, greeks)
        price : price in domestic ccy per 1 unit of foreign notional.
        greeks : {delta, gamma, vega, theta, rho_d, rho_f}.

    Greeks conventions match the rest of the repo:
    - vega per 1% absolute σ
    - theta per calendar day
    - rho_d, rho_f per 1% absolute rate
    """
    if T <= 0 or sigma <= 0:
        intrinsic = (S - K) if side == "call" else (K - S)
        return max(intrinsic, 0.0), {
            "delta": 0.0, "gamma": 0.0, "vega": 0.0,
            "theta": 0.0, "rho_d": 0.0, "rho_f": 0.0,
        }
    d1 = (math.log(S / K) + (r_d - r_f + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if side == "call":
        price = S * math.exp(-r_f * T) * norm.cdf(d1) - K * math.exp(-r_d * T) * norm.cdf(d2)
        delta = math.exp(-r_f * T) * norm.cdf(d1)
    else:
        price = K * math.exp(-r_d * T) * norm.cdf(-d2) - S * math.exp(-r_f * T) * norm.cdf(-d1)
        delta = -math.exp(-r_f * T) * norm.cdf(-d1)

    gamma = math.exp(-r_f * T) * norm.pdf(d1) / (S * sigma * math.sqrt(T))
    vega_unit = S * math.exp(-r_f * T) * norm.pdf(d1) * math.sqrt(T)
    vega = vega_unit / 100.0
    theta_yr = -(S * math.exp(-r_f * T) * norm.pdf(d1) * sigma) / (2 * math.sqrt(T))
    if side == "call":
        theta_yr += r_f * S * math.exp(-r_f * T) * norm.cdf(d1) - r_d * K * math.exp(-r_d * T) * norm.cdf(d2)
    else:
        theta_yr += -r_f * S * math.exp(-r_f * T) * norm.cdf(-d1) + r_d * K * math.exp(-r_d * T) * norm.cdf(-d2)
    theta = theta_yr / 365.0

    if side == "call":
        rho_d = K * T * math.exp(-r_d * T) * norm.cdf(d2) / 100.0
        rho_f = -S * T * math.exp(-r_f * T) * norm.cdf(d1) / 100.0
    else:
        rho_d = -K * T * math.exp(-r_d * T) * norm.cdf(-d2) / 100.0
        rho_f = S * T * math.exp(-r_f * T) * norm.cdf(-d1) / 100.0

    return float(price), {
        "delta": float(delta), "gamma": float(gamma), "vega": float(vega),
        "theta": float(theta), "rho_d": float(rho_d), "rho_f": float(rho_f),
    }
