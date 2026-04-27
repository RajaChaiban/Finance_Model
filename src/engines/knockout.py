"""Knockout (barrier) option pricing engine.

Implements the Reiner-Rubinstein closed-form for continuously-monitored
single-barrier options under GBM with no rebate. Direction (Down vs Up)
is inferred from the barrier level relative to spot.

For DISCRETELY-MONITORED barriers (the market norm — daily/weekly fix),
apply :func:`bgk_adjusted_barrier` to the barrier level before pricing.
Without this correction the engine systematically over-estimates knock-out
probability → under-prices the KO product. Bias is typically 30–80 bp on
a 1Y / 25-vol DOI call near the barrier; trade-blocking for any flow desk.
"""

import numpy as np
from scipy.stats import norm
from . import black_scholes


# Broadie-Glasserman-Kou continuity-correction constant.
# = -ζ(1/2) / √(2π) ≈ 0.5826  (Broadie, Glasserman, Kou 1997, "A continuity
# correction for discrete barrier options", Math. Finance 7(4)).
_BGK_CONSTANT = 0.5826


def bgk_adjusted_barrier(B: float, S: float, sigma: float, monitoring_dt: float) -> float:
    """Broadie-Glasserman-Kou continuity correction for discrete barrier monitoring.

    Returns the EQUIVALENT continuous-monitoring barrier that produces the
    same survival probability as a barrier monitored every ``monitoring_dt``
    years. Use this when a continuous-formula closed-form (Reiner-Rubinstein,
    QL ``AnalyticBarrierEngine``) is being applied to a discretely-monitored
    product.

    Direction:
      - Upper barrier (B > S): B_adj = B · exp(+0.5826 · σ · √Δt)  (shift UP)
      - Lower barrier (B < S): B_adj = B · exp(−0.5826 · σ · √Δt)  (shift DOWN)

    Args:
        B: Original (discretely-monitored) barrier level
        S: Spot price (used only to determine barrier direction)
        sigma: Volatility (annualised)
        monitoring_dt: Monitoring interval in years
            (1/252 = daily on US business days, 1/52 = weekly, 1/12 = monthly)

    Returns:
        Adjusted barrier level for use in continuous-monitoring engines.

    Raises:
        ValueError: If B == S (direction undefined) or monitoring_dt < 0.
    """
    if monitoring_dt < 0:
        raise ValueError(f"monitoring_dt must be non-negative, got {monitoring_dt}")
    if B == S:
        raise ValueError(
            "BGK shift undefined for at-the-spot barrier (B == S). "
            "Specify whether barrier is upper (B > S) or lower (B < S)."
        )
    eta = 1.0 if B > S else -1.0
    return float(B * np.exp(eta * _BGK_CONSTANT * sigma * np.sqrt(monitoring_dt)))


_MONITORING_DT = {
    "continuous": 0.0,
    "daily": 1.0 / 252.0,    # US business days
    "weekly": 1.0 / 52.0,
    "monthly": 1.0 / 12.0,
}


def _resolve_monitoring(monitoring) -> float:
    """Translate a monitoring spec to its Δt in years.

    Accepts a string (``continuous``/``daily``/``weekly``/``monthly``) or a
    raw numeric Δt for non-standard schedules.
    """
    if isinstance(monitoring, (int, float)):
        if monitoring < 0:
            raise ValueError(f"monitoring Δt must be ≥ 0, got {monitoring}")
        return float(monitoring)
    if monitoring not in _MONITORING_DT:
        raise ValueError(
            f"Unknown monitoring '{monitoring}'. Options: "
            f"{list(_MONITORING_DT)} or a numeric Δt in years."
        )
    return _MONITORING_DT[monitoring]


def price_knockout(S: float, K: float, B: float, r: float, sigma: float, T: float, q: float = 0,
                   option_type: str = "call", monitoring="continuous") -> tuple:
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
        monitoring: "continuous" (default, back-compat), "daily", "weekly",
            "monthly", or a numeric Δt in years. Discrete monitoring applies
            the Broadie-Glasserman-Kou shift to the barrier before pricing.

    Returns:
        (knockout_price, vanilla_price, barrier_discount_ratio, lambda_param)
    """
    if option_type.lower() not in ("call", "put"):
        raise ValueError("option_type must be 'call' or 'put'")

    monitoring_dt = _resolve_monitoring(monitoring)
    if monitoring_dt > 0:
        B = bgk_adjusted_barrier(B, S, sigma, monitoring_dt)

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
                    option_type: str = "call", monitoring="continuous") -> dict:
    """Calculate Greeks for knockout option (bump-and-reprice).

    Args:
        S, K, B, r, sigma, T, q, option_type: Option parameters
        monitoring: see :func:`price_knockout`. Forwarded to every reprice.

    Returns:
        Dict with keys: delta, gamma, vega, theta, rho, barrier_discount
    """
    # Base case
    price_base, vanilla_base, adj_base, _ = price_knockout(S, K, B, r, sigma, T, q, option_type,
                                                            monitoring=monitoring)

    # Delta
    bump_pct = 0.01
    S_up = S * (1 + bump_pct)
    S_down = S * (1 - bump_pct)
    price_up, _, _, _ = price_knockout(S_up, K, B, r, sigma, T, q, option_type, monitoring=monitoring)
    price_down, _, _, _ = price_knockout(S_down, K, B, r, sigma, T, q, option_type, monitoring=monitoring)
    delta = (price_up - price_down) / (S_up - S_down)

    # Gamma
    price_base_2, _, _, _ = price_knockout(S, K, B, r, sigma, T, q, option_type, monitoring=monitoring)
    delta_up = (price_up - price_base_2) / (S_up - S)
    delta_down = (price_base_2 - price_down) / (S - S_down)
    gamma = (delta_up - delta_down) / (S_up - S_down)

    # Vega
    vol_bump = 0.01
    price_vol_up, _, _, _ = price_knockout(S, K, B, r, sigma + vol_bump, T, q, option_type, monitoring=monitoring)
    vega = (price_vol_up - price_base) / vol_bump / 100

    # Theta
    T_down = max(T - 1 / 365, 0.001)
    price_t_down, _, _, _ = price_knockout(S, K, B, r, sigma, T_down, q, option_type, monitoring=monitoring)
    theta = (price_t_down - price_base) / (T_down - T)

    # Rho
    rate_bump = 0.01
    price_r_up, _, _, _ = price_knockout(S, K, B, r + rate_bump, sigma, T, q, option_type, monitoring=monitoring)
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
