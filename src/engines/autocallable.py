"""Phoenix autocallable Monte Carlo pricer.

Worst-of underlier basket. Discrete observation dates. On each observation:
  - if worst-of perf >= autocall_barrier  -> redeem at par + accrued coupons, stop.
  - else if worst-of perf >= coupon_barrier -> pay coupon (memory: pay accrued too).
  - else (memory=True) -> accrue the coupon for later.
At final maturity (if never autocalled):
  - if worst-of perf >= protection_barrier -> return par.
  - else -> return par * worst-of perf (capital loss tracks worst underlier).

Discount convention: each cashflow is discounted at its own payment time t_k
using df = exp(-r * t_k).

Path simulation: delegates to multi_asset_mc.simulate_correlated_gbm with
n_steps = int(round(T * 252)) so that each business day is one step, making
observation date mapping via index lookup exact.
"""
from __future__ import annotations

import numpy as np

from src.agents.state import AutocallTerms, ObservationSchedule
from .multi_asset_mc import simulate_correlated_gbm


def price_phoenix_autocallable(
    *,
    S0: np.ndarray,
    r: float,
    q: np.ndarray,
    sigma: np.ndarray,
    rho: np.ndarray,
    terms: AutocallTerms,
    schedule: ObservationSchedule,
    notional: float = 1_000_000.0,
    n_paths: int = 20_000,
    seed: int | None = None,
) -> float:
    """Price a phoenix autocallable note via Monte Carlo.

    Args:
        S0:       Initial spot prices, shape (n_assets,).
        r:        Risk-free rate (scalar, continuously compounded).
        q:        Dividend yields, shape (n_assets,).
        sigma:    Asset vols, shape (n_assets,).
        rho:      Correlation matrix, shape (n_assets, n_assets).
        terms:    AutocallTerms — barriers, coupon rate, memory flag.
        schedule: ObservationSchedule — list of observation times in years.
        notional: Note face value (USD or any currency).
        n_paths:  Number of Monte Carlo paths.
        seed:     RNG seed (None = unseeded).

    Returns:
        Monte Carlo estimate of the note's fair value (same units as notional).
    """
    obs_years = np.asarray(schedule.dates_years, dtype=float)
    T = float(obs_years[-1])
    n_steps = max(1, int(round(T * 252)))

    paths = simulate_correlated_gbm(
        S0=S0, r=r, q=q, sigma=sigma, rho=rho, T=T,
        n_steps=n_steps, n_paths=n_paths, seed=seed,
    )
    # paths shape: (n_paths_actual, n_steps+1, n_assets)
    # antithetic doubling may mean paths.shape[0] != n_paths for odd n_paths;
    # use actual count throughout.
    n_paths_actual = paths.shape[0]

    # Map each observation time to the nearest step index (clipped to valid range).
    obs_indices = np.clip(
        np.round(obs_years / T * n_steps).astype(int),
        0, n_steps,
    )

    # Compute worst-of performance at each observation date.
    # perf_at_obs: (n_paths_actual, n_obs, n_assets)
    perf_at_obs = paths[:, obs_indices, :] / S0[np.newaxis, np.newaxis, :]
    worst_perf = perf_at_obs.min(axis=2)  # (n_paths_actual, n_obs)

    # --- Walk through observations accumulating PV per path ---
    pv = np.zeros(n_paths_actual, dtype=float)
    accrued = np.zeros(n_paths_actual, dtype=float)  # notional-units accrued coupon
    alive = np.ones(n_paths_actual, dtype=bool)       # True until autocalled

    for k, t_k in enumerate(obs_years):
        wp = worst_perf[:, k]          # (n_paths_actual,)
        df = np.exp(-r * t_k)

        # --- Coupon logic ---
        coupon_amount = terms.coupon_rate * notional
        pays_coupon = (wp >= terms.coupon_barrier) & alive

        if terms.memory:
            # Paths that pay: receive accrued + this period's coupon.
            # Paths that miss: accrue this period's coupon.
            payable = accrued + coupon_amount
            pv += np.where(pays_coupon, df * payable, 0.0)
            # Reset accrued where paid; accumulate where missed (only for alive paths).
            accrued = np.where(
                pays_coupon,
                0.0,
                np.where(alive, accrued + coupon_amount, accrued),
            )
        else:
            # No memory: pay coupon only if barrier met; no carry-forward.
            pv += np.where(pays_coupon, df * coupon_amount, 0.0)

        # --- Autocall logic ---
        # Paths that autocall: redeem at par. Any remaining accrued coupon is
        # already captured above (memory paths that hit coupon barrier first).
        # If autocall_barrier > coupon_barrier, a path that autocalls always
        # satisfies coupon_barrier too, so accrued was already paid out above.
        autocalls = (wp >= terms.autocall_barrier) & alive
        pv += np.where(autocalls, df * notional, 0.0)
        alive = alive & ~autocalls

    # --- Maturity redemption for surviving paths ---
    final_perf = worst_perf[:, -1]
    df_T = np.exp(-r * T)
    redemption = np.where(
        final_perf >= terms.protection_barrier,
        notional,
        notional * final_perf,  # downside: proportional capital loss
    )
    pv += np.where(alive, df_T * redemption, 0.0)

    return float(pv.mean())
