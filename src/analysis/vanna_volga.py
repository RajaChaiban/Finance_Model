"""Vanna and Volga — second-order vol cross-Greeks.

Vanna  = ∂²V/∂S∂σ      (rate of change of delta as vol moves; or vega-vs-spot)
Volga  = ∂²V/∂σ²       (vega convexity in vol)
Vomma  = volga (alias)

These dominate the risk picture for skew-sensitive products — barrier
options, autocallables, and any product with a digital component. A scalar
vega = 0 means nothing for a barrier near the strike: vanna can be huge.

Implementation: bump-and-reprice. Centred 5-point stencil for the cross
term, 3-point for volga. Returns dollars per (1%σ × $1 spot) for vanna and
dollars per (1%σ)² for volga, matching the rest of the repo's "vega per 1% σ"
convention.

Used by:
- ``src/api/handlers.py`` — fold vanna/volga into the Greeks dict for KO/KI
  and (eventually) autocall.
- ``src/agents/scenario.py`` — scenario P&L can include the vanna term.
"""

from __future__ import annotations

from typing import Callable


def compute_vanna_volga(
    *,
    price_fn: Callable[[float, float], float],
    spot: float,
    sigma: float,
    bump_S: float = 0.01,           # 1% spot bump
    bump_sigma: float = 0.005,      # 50bps σ bump
) -> dict:
    """Compute vanna and volga via finite differences.

    price_fn : callable(S, σ) -> price.

    Returns
    -------
    dict with keys 'vanna' and 'volga' in repo conventions:
      - vanna : $ per (1% σ × $1 spot)
      - volga : $ per (1% σ)²
    """
    h_S = max(spot * bump_S, 0.01)
    h_v = bump_sigma

    # Central 4-point stencil for the cross derivative ∂²V/∂S∂σ:
    #   ≈ [V(S+h, σ+k) - V(S-h, σ+k) - V(S+h, σ-k) + V(S-h, σ-k)] / (4·h·k)
    p_pp = price_fn(spot + h_S, sigma + h_v)
    p_mp = price_fn(spot - h_S, sigma + h_v)
    p_pm = price_fn(spot + h_S, sigma - h_v)
    p_mm = price_fn(spot - h_S, sigma - h_v)
    vanna_raw = (p_pp - p_mp - p_pm + p_mm) / (4.0 * h_S * h_v)
    # Convert to repo conventions: per 1% σ (i.e. /100 on the σ side).
    vanna = vanna_raw / 100.0

    # Volga: standard 3-point stencil ∂²V/∂σ² at fixed S.
    p_0 = price_fn(spot, sigma)
    p_up = price_fn(spot, sigma + h_v)
    p_dn = price_fn(spot, sigma - h_v)
    volga_raw = (p_up - 2.0 * p_0 + p_dn) / (h_v ** 2)
    # Convert to per (1% σ)² — divide by 100² = 10_000.
    volga = volga_raw / 10_000.0

    return {"vanna": float(vanna), "volga": float(volga)}
