"""Greeks-based P&L attribution (explain) for day-on-day option P&L decomposition.

Conventions (must match the repo — verified against black_scholes.py):
  - Vega   : per 1% absolute σ  (i.e. the stored value already has /100 baked in)
  - Theta  : per calendar day   (i.e. the stored value already has /365 baked in)
  - Rho    : per 1% absolute r  (i.e. the stored value already has /100 baked in)

Taylor expansion used:
  ΔP ≈ Δ·ΔS + 0.5·Γ·ΔS² + V·(Δσ·100) + Θ·dt + ρ·(Δr·100) + unexplained

where:
  ΔS   = curr_S    - prev_S
  Δσ   = curr_sigma - prev_sigma  (absolute, e.g. 0.20 → 0.22 ⟹ Δσ = 0.02)
  Δr   = curr_r     - prev_r      (absolute)
  dt   = dt_days                  (default 1 calendar day)

Because Vega is already per 1%-σ, a Δσ of 0.02 contributes  Vega * (0.02 * 100) = Vega * 2.
Same logic applies to Rho.
"""

from dataclasses import dataclass


@dataclass
class PnLAttribution:
    """Decomposition of one-day option P&L into Greek contributions."""

    delta_pnl: float      # Δ · ΔS
    gamma_pnl: float      # 0.5 · Γ · ΔS²
    vega_pnl: float       # V · (Δσ · 100)   [vega is per 1% σ]
    theta_pnl: float      # Θ · dt_days       [theta is per calendar day]
    rho_pnl: float        # ρ · (Δr · 100)    [rho is per 1% r]
    cross_pnl: float      # higher-order / cross terms (currently 0; reserved)
    unexplained: float    # total_observed − total_explained
    total_observed: float # curr_price − prev_price
    total_explained: float  # sum of the five first-order contributions


def explain_pnl(
    prev_price: float,
    prev_greeks: dict,
    prev_S: float,
    prev_sigma: float,
    prev_r: float,
    curr_price: float,
    curr_S: float,
    curr_sigma: float,
    curr_r: float,
    dt_days: float = 1,
) -> PnLAttribution:
    """Decompose the day-on-day P&L into Greek contributions.

    Parameters
    ----------
    prev_price  : Option price at the start of the period.
    prev_greeks : Dict with keys 'delta', 'gamma', 'vega', 'theta', 'rho'
                  using the repo's unit conventions (vega/rho per 1%, theta per day).
    prev_S      : Spot price at start of period.
    prev_sigma  : Implied vol at start of period (decimal, e.g. 0.20).
    prev_r      : Risk-free rate at start of period (decimal, e.g. 0.05).
    curr_price  : Option price at end of period.
    curr_S      : Spot price at end of period.
    curr_sigma  : Implied vol at end of period (decimal).
    curr_r      : Risk-free rate at end of period (decimal).
    dt_days     : Elapsed time in calendar days (default 1).

    Returns
    -------
    PnLAttribution with all components filled.
    """
    delta = prev_greeks["delta"]
    gamma = prev_greeks["gamma"]
    vega  = prev_greeks["vega"]   # per 1% absolute σ
    theta = prev_greeks["theta"]  # per calendar day
    rho   = prev_greeks["rho"]    # per 1% absolute r

    dS    = curr_S     - prev_S
    dsigma = curr_sigma - prev_sigma   # absolute change in vol (decimal)
    dr    = curr_r     - prev_r        # absolute change in rate (decimal)

    delta_pnl = delta * dS
    gamma_pnl = 0.5 * gamma * dS ** 2
    # vega is per 1% σ, so multiply by 100 * dsigma (= percentage-point move)
    vega_pnl  = vega  * (dsigma * 100)
    theta_pnl = theta * dt_days
    # rho is per 1% r, so multiply by 100 * dr
    rho_pnl   = rho   * (dr * 100)
    cross_pnl = 0.0   # reserved; currently zero

    total_observed = curr_price - prev_price
    total_explained = delta_pnl + gamma_pnl + vega_pnl + theta_pnl + rho_pnl + cross_pnl
    unexplained = total_observed - total_explained

    return PnLAttribution(
        delta_pnl=delta_pnl,
        gamma_pnl=gamma_pnl,
        vega_pnl=vega_pnl,
        theta_pnl=theta_pnl,
        rho_pnl=rho_pnl,
        cross_pnl=cross_pnl,
        unexplained=unexplained,
        total_observed=total_observed,
        total_explained=total_explained,
    )
