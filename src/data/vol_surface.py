"""Build a QuantLib ``BlackVarianceSurface`` from an :class:`IVGrid`.

The grid carries the *raw* market surface — IVs inverted from listed mids
with NaN holes wherever a quote refused to invert. QuantLib's surface
constructor needs a complete strikes × expiries matrix, so this module
fills NaN cells by linear-in-strike interpolation along each expiry.

Sampling for closed-form pricers
--------------------------------

The hand-coded closed-form engines (``black_scholes.price_european``,
``knockout.price_knockout`` Reiner-Rubinstein) take a *scalar* σ — they
cannot consume a surface object directly. :func:`sample_sigma_for_closed_form`
evaluates the surface at the (K, T) point most relevant to the contract:

  * European → σ at strike (vanilla payoff is determined at K).
  * Knockout → ``max(σ at strike, σ at barrier)``. A single scalar fed
    into Reiner-Rubinstein has two competing effects: it raises the
    vanilla call/put value (good for the holder) AND raises knock-out
    probability (bad for the holder). On a down-and-out put with the
    barrier sitting on the steep put-wing of the smile, σ at barrier is
    materially higher than σ at strike, and the no-arb dominant side is
    that wing — so picking the larger σ keeps the bridge directionally
    on the right side of the surface effect.

    **This is a heuristic.** A single σ cannot separate the smile's
    effect on payoff value from its effect on breach probability — only
    a local-volatility PDE can. The QL ``AnalyticBarrierEngine`` is
    flat-vol by mathematical construction; getting the directional
    answer right under a steep smile requires routing KO products
    through ``FdBlackScholesBarrierEngine`` with
    ``ql.LocalVolSurface`` (Dupire). Tracked as Phase-2 follow-up.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

import numpy as np
import QuantLib as ql

from src.data.iv_grid import IVGrid
from src.engines._ql_session import days_from_T

logger = logging.getLogger(__name__)


_DAY_COUNTER = ql.Actual365Fixed()
_CALENDAR = ql.UnitedStates(ql.UnitedStates.NYSE)

# Cap extrapolated wing IVs at this percentile of in-grid IVs. Edge holes
# (typically deep-OTM strikes that didn't invert) get filled by nearest-
# neighbour np.interp, which on a steep smile means the deepest-OTM
# inverted IV gets pinned across an entire dead zone. Capping at p95 keeps
# the surface stable on the wings without distorting the bulk.
_WING_FILL_PERCENTILE = 95.0


def _to_ql_date(d: date) -> ql.Date:
    return ql.Date(d.day, d.month, d.year)


def _fill_nan_along_strike(iv: np.ndarray, strikes: np.ndarray) -> np.ndarray:
    """Linear-in-strike interpolation per expiry; edge holes get nearest neighbour
    and are then capped at the per-row 95th percentile of valid IVs.

    QuantLib BlackVarianceSurface requires a fully-populated matrix. Interior
    holes are well-handled by ``np.interp``; edge holes get the boundary value
    extrapolated as a constant. On a steep put-wing that constant can be a
    50%+ IV slammed across every dead strike below the deepest in-grid one,
    which manufactures spurious wing risk. Cap at p95 of valid in-row IVs
    so the surface stays stable.
    """
    out = iv.copy()
    for t in range(out.shape[0]):
        row = out[t]
        mask = np.isfinite(row)
        if mask.sum() == 0:
            raise ValueError(
                f"Expiry index {t}: every strike NaN — cannot interpolate."
            )
        if mask.sum() == len(row):
            continue
        idx = np.arange(len(row))
        filled = np.interp(idx, idx[mask], row[mask])
        # Cap fills (only the cells we just filled) at p95 of valid IVs.
        cap = float(np.percentile(row[mask], _WING_FILL_PERCENTILE))
        fill_mask = ~mask
        filled[fill_mask] = np.minimum(filled[fill_mask], cap)
        out[t] = filled
    return out


def build_vol_surface(
    grid: IVGrid,
    ref_date: Optional[date] = None,
) -> ql.BlackVarianceSurface:
    """Construct a ``ql.BlackVarianceSurface`` from an :class:`IVGrid`.

    Args:
        grid: Output of :func:`src.data.iv_grid.build_iv_grid`.
        ref_date: Reference (evaluation) date. Default = today.

    Returns:
        A QuantLib variance surface ready to wrap in a
        ``BlackVolTermStructureHandle``.

    Raises:
        ValueError: If the grid is too sparse (< 2 strikes or < 2 expiries),
            its expiries are non-monotone, or any expiry row is fully NaN.
    """
    if len(grid.strikes) < 2 or len(grid.expiries) < 2:
        raise ValueError(
            f"Surface requires ≥2 strikes and ≥2 expiries; got "
            f"{len(grid.strikes)}×{len(grid.expiries)}."
        )
    if not np.all(np.diff(grid.expiries) > 0):
        raise ValueError("Surface expiries must be strictly ascending in T.")

    iv_filled = _fill_nan_along_strike(grid.iv, grid.strikes)

    today = ref_date if ref_date is not None else date.today()
    ql_today = _to_ql_date(today)

    # Use the engine's shared days_from_T (round-half-up, floor at 1) so the
    # surface's expiry dates align bit-for-bit with what the pricing engines
    # see. Python's built-in round() is banker's rounding, which can drift
    # by 1 day at half-integer T relative to the engines.
    expiry_dates = [
        _to_ql_date(today + timedelta(days=days_from_T(T)))
        for T in grid.expiries
    ]

    # QL BlackVarianceSurface wants a Matrix of shape (n_strikes, n_expiries).
    # Our iv_filled is shape (n_expiries, n_strikes) → transpose.
    iv_T = iv_filled.T  # shape (n_strikes, n_expiries)
    matrix = ql.Matrix(int(iv_T.shape[0]), int(iv_T.shape[1]))
    for i in range(iv_T.shape[0]):
        for j in range(iv_T.shape[1]):
            matrix[i][j] = float(iv_T[i, j])

    surface = ql.BlackVarianceSurface(
        ql_today,
        _CALENDAR,
        expiry_dates,
        [float(k) for k in grid.strikes],
        matrix,
        _DAY_COUNTER,
    )
    surface.enableExtrapolation()

    logger.info(
        f"Built BlackVarianceSurface: {len(grid.strikes)} strikes × "
        f"{len(grid.expiries)} expiries; σ range "
        f"[{iv_filled.min():.4f}, {iv_filled.max():.4f}]."
    )
    return surface


def sample_sigma_for_closed_form(
    surface: ql.BlackVarianceSurface,
    K: float,
    T: float,
    S: float,
    *,
    barrier: Optional[float] = None,
) -> float:
    """Evaluate the surface to a single σ for hand-coded closed-form math.

    European: σ at strike. Knockout: ``max(σ at strike, σ at barrier)``
    so the smile's worse side dominates the scalar — see module docstring
    for the directional rationale and the local-vol PDE follow-up.

    Args:
        surface: Output of :func:`build_vol_surface`.
        K: Strike.
        T: Time to expiry in years.
        S: Spot — accepted for signature stability and future use.
        barrier: Barrier level if pricing a KO product, else ``None``.

    Returns:
        σ as a positive float.
    """
    if T <= 0:
        raise ValueError(f"T must be positive, got {T}")

    sigma_strike = float(surface.blackVol(T, K, True))
    if barrier is None:
        return sigma_strike
    sigma_barrier = float(surface.blackVol(T, barrier, True))
    return max(sigma_strike, sigma_barrier)
