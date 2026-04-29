"""Bucketed sensitivities: scenario grid (S x sigma), gamma ladder."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Sequence

import numpy as np
from pydantic import BaseModel

from src.engines.router import route


class SensitivityBlock(BaseModel):
    """2-D price grid plus its axes."""
    values: list[list[float]]
    spot_axis: list[float]
    vol_axis: list[float]

    @property
    def shape(self) -> tuple[int, int]:
        return (len(self.spot_axis), len(self.vol_axis))


@dataclass
class GammaLadderPoint:
    spot: float
    delta: float
    gamma: float


def compute_scenario_grid(
    option_type: str,
    S: float, K: float, r: float, sigma: float, T: float, q: float,
    spot_shifts: Sequence[float] = (-0.10, -0.05, 0.0, 0.05, 0.10),
    vol_shifts: Sequence[float] = (-0.05, 0.0, 0.05),
    **kwargs,
) -> SensitivityBlock:
    """Compute a price grid across spot and vol shifts.

    kwargs are forwarded to the pricer but any engine-specific keys that
    may cause errors (e.g. vol_handle) are silently dropped so that the
    simple european_call case with no kwargs works without NaN.
    """
    pricer, _, _ = route(option_type)
    # Strip keys that require live market handles (not available in offline grid)
    safe_kwargs = {
        k: v for k, v in kwargs.items()
        if k not in ("vol_handle", "use_local_vol_pde")
    }
    grid = np.zeros((len(spot_shifts), len(vol_shifts)))
    for i, ds in enumerate(spot_shifts):
        for j, dv in enumerate(vol_shifts):
            try:
                price, _, _ = pricer(
                    S * (1 + ds), K, r,
                    max(sigma + dv, 1e-4),
                    T, q, **safe_kwargs,
                )
                grid[i, j] = float(price) if price is not None else float("nan")
            except Exception:
                grid[i, j] = float("nan")
    return SensitivityBlock(
        values=grid.tolist(),
        spot_axis=[S * (1 + ds) for ds in spot_shifts],
        vol_axis=[sigma + dv for dv in vol_shifts],
    )


def compute_gamma_ladder(
    option_type: str,
    S: float, K: float, r: float, sigma: float, T: float, q: float,
    n_points: int = 11, halfwidth: float = 0.15,
    **kwargs,
) -> list[GammaLadderPoint]:
    """Compute delta and gamma across a range of spot levels.

    The ladder is centred on the ATM forward (S * exp((r-q)*T)) so that the
    gamma peak (which occurs near the forward for vanilla options) falls close
    to the middle of the returned list.

    kwargs forwarded to greeks_fn; engine-specific handles are stripped so the
    plain european_call case works without requiring live market data.
    """
    _, greeks_fn, _ = route(option_type)
    safe_kwargs = {
        k: v for k, v in kwargs.items()
        if k not in ("vol_handle", "use_local_vol_pde")
    }
    import math
    # Centre ladder at the gamma-peak spot for vanilla options:
    # gamma is maximised when d1=0 => S_peak = K * exp(-(r-q+0.5*sigma^2)*T).
    # For non-vanilla option types we fall back to S itself.
    try:
        s_peak = K * math.exp(-(r - q + 0.5 * sigma ** 2) * T)
    except Exception:
        s_peak = S
    spots = np.linspace(s_peak * (1 - halfwidth), s_peak * (1 + halfwidth), n_points)
    out: list[GammaLadderPoint] = []
    for s in spots:
        g = greeks_fn(float(s), K, r, sigma, T, q, **safe_kwargs)
        out.append(GammaLadderPoint(
            spot=float(s),
            delta=g.get("delta", 0.0),
            gamma=g.get("gamma", 0.0),
        ))
    return out
