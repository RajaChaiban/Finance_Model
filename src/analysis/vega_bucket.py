"""Vega bucket grid — tenor × strike sensitivity decomposition.

A scalar vega tells you nothing about *where* on the surface the vol risk
sits. A trader hedging a 5y autocall doesn't want to know "vega = 1.2k$ per
vol-pt"; they want the bucketed view:

    Tenor →     1M    3M    6M    1Y    2Y    5Y
    Strike ↓
    90% K     0.0  0.0  0.0  0.1  0.2  0.4   ← long-dated wing risk
    100% K    0.0  0.1  0.2  0.3  0.4  0.6
    110% K    0.0  0.0  0.1  0.1  0.1  0.2

This module builds that grid by bumping the ATM IV at each (T_b, K_b) cell
of a tenor-by-strike grid and re-pricing.

Conventions:
- Bumps are **absolute** in vol units (e.g. +0.005 = +50bps σ).
- Output is "$ per 1% absolute σ at this (T, K) point" — so each cell sums
  to roughly the scalar vega when bumps are independent.
- For products with no exposure to a given (T, K) cell (e.g. a 6M put has
  near-zero exposure to 5y vol), the cell will be ~0; a real desk masks
  these in the UI to reduce noise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np


@dataclass
class VegaBucket:
    """A single (tenor, strike) cell."""
    tenor_years: float
    strike_pct_of_spot: float
    vega: float                     # $ per 1% σ bump at this point


@dataclass
class VegaBucketGrid:
    tenors_years: list[float]
    strikes_pct_of_spot: list[float]
    grid: list[list[float]]         # rows = strikes, cols = tenors
    total_vega_check: float         # for sanity: sum(grid) ≈ scalar vega

    def to_dict(self) -> dict:
        return {
            "tenors_years": list(self.tenors_years),
            "strikes_pct_of_spot": list(self.strikes_pct_of_spot),
            "grid": [list(row) for row in self.grid],
            "total_vega_check": self.total_vega_check,
        }


def compute_vega_buckets(
    *,
    price_fn: Callable[[float], float],
    sigma_atm: float,
    spot: float,
    expiry_years: float,
    tenors_years: Sequence[float] = (0.083, 0.25, 0.5, 1.0, 2.0, 5.0),
    strikes_pct: Sequence[float] = (0.80, 0.90, 1.00, 1.10, 1.20),
    bump_size: float = 0.0050,
) -> VegaBucketGrid:
    """Compute the vega bucket grid by repeatedly bumping the surface.

    For Phase 1 we don't have a true (T, K) bumping mechanism on the live
    surface — we approximate by re-pricing at ``sigma_atm + bump`` only when
    the cell's tenor is closest to the product's expiry and the cell's strike
    is closest to ATM. For all other cells we report ~0. This gives a
    correctly-magnituded ATM vega in the right cell and zeros elsewhere —
    enough to surface the bucket *concept* in the UI.

    v2 will plug a real (T, K)-localised vol-surface bump.

    Parameters
    ----------
    price_fn : callable(sigma) -> price
        A closure that prices the structure at a given scalar σ.
    sigma_atm : float
        The current ATM IV.
    spot : float
        Underlying spot.
    expiry_years : float
        The structure's actual expiry — used to pick the dominant tenor cell.
    tenors_years : Sequence[float]
        Tenor axis of the bucket grid.
    strikes_pct : Sequence[float]
        Strike axis as fraction of spot.
    bump_size : float
        Absolute bump for finite-difference vega (default 50bps).

    Returns
    -------
    VegaBucketGrid
    """
    p_up = price_fn(sigma_atm + bump_size)
    p_dn = price_fn(sigma_atm - bump_size)
    p_0 = price_fn(sigma_atm)
    # Vega per 1% absolute σ
    scalar_vega = (p_up - p_dn) / (2.0 * bump_size) / 100.0

    tenors = list(tenors_years)
    strikes = list(strikes_pct)
    grid = [[0.0 for _ in tenors] for _ in strikes]

    # Place the bulk of the vega in the cell closest to (expiry, ATM=1.0).
    t_idx = int(np.argmin([abs(t - expiry_years) for t in tenors]))
    s_idx = int(np.argmin([abs(s - 1.0) for s in strikes]))
    grid[s_idx][t_idx] = scalar_vega

    return VegaBucketGrid(
        tenors_years=tenors,
        strikes_pct_of_spot=strikes,
        grid=grid,
        total_vega_check=scalar_vega,
    )
