"""Correlated multi-asset GBM Monte Carlo.

Cholesky-correlated Brownian motion, antithetic variates, fixed seed for CRN.

Conventions:
  - Returns shape (n_paths, n_steps+1, n_assets) — time axis includes t=0.
  - Antithetic: internally uses n_paths//2 base draws then mirrors them;
    the returned path count always equals the requested n_paths.
  - q (dividend yield) is per-asset, broadcast-compatible with sigma.
"""
from __future__ import annotations
import numpy as np


def simulate_correlated_gbm(
    *,
    S0: np.ndarray,
    r: float,
    q: np.ndarray,
    sigma: np.ndarray,
    rho: np.ndarray,
    T: float,
    n_steps: int,
    n_paths: int,
    seed: int | None = None,
    antithetic: bool = True,
) -> np.ndarray:
    """Simulate correlated GBM paths for n_assets assets.

    Args:
        S0:        Initial spot prices, shape (n_assets,).
        r:         Risk-free rate (scalar).
        q:         Dividend yields, shape (n_assets,).
        sigma:     Asset vols, shape (n_assets,).
        rho:       Correlation matrix, shape (n_assets, n_assets).
        T:         Time horizon in years.
        n_steps:   Number of time steps (equidistant).
        n_paths:   Number of Monte Carlo paths to return.
        seed:      RNG seed for reproducibility (None = unseeded).
        antithetic: Use antithetic variates (halves base draw count,
                    mirrors to recover n_paths).

    Returns:
        paths: np.ndarray of shape (n_paths, n_steps+1, n_assets).
               paths[:, 0, :] == S0 for all paths.
    """
    n_assets = S0.shape[0]
    rng = np.random.default_rng(seed)
    L = np.linalg.cholesky(rho)  # (n_assets, n_assets)
    dt = T / n_steps

    half = n_paths // 2 if antithetic else n_paths
    # z shape: (half, n_steps, n_assets)
    z = rng.standard_normal((half, n_steps, n_assets))
    if antithetic:
        z = np.concatenate([z, -z], axis=0)  # (n_paths, n_steps, n_assets)

    # Correlate: z @ L.T maps uncorrelated N(0,1) to correlated N(0, rho)
    z_corr = z @ L.T  # (n_paths, n_steps, n_assets)

    # GBM log-increments
    drift = (r - q - 0.5 * sigma ** 2) * dt          # (n_assets,)
    diffusion = sigma * np.sqrt(dt)                    # (n_assets,)
    log_increments = drift + diffusion * z_corr        # (n_paths, n_steps, n_assets)

    # Prepend zero (t=0 log-return) then cumsum to get log-paths
    log_paths = np.concatenate(
        [np.zeros((n_paths, 1, n_assets)), np.cumsum(log_increments, axis=1)],
        axis=1,
    )  # (n_paths, n_steps+1, n_assets)

    return S0 * np.exp(log_paths)


def price_worst_of_european_put(
    *,
    S0: np.ndarray,
    K: float,
    r: float,
    q: np.ndarray,
    sigma: np.ndarray,
    rho: np.ndarray,
    T: float,
    n_paths: int = 20000,
    seed: int | None = None,
) -> float:
    """Price a worst-of European put via Monte Carlo.

    The payoff is max(K - min_i(S_i(T)), 0) where min is taken over all
    assets, using each asset's terminal price normalised by its spot so
    that heterogeneous spot levels are handled correctly.

    Args:
        S0:      Initial spots, shape (n_assets,).
        K:       Strike (same currency as S0; assumed S0 are all near K).
        r:       Risk-free rate.
        q:       Dividend yields, shape (n_assets,).
        sigma:   Vols, shape (n_assets,).
        rho:     Correlation matrix.
        T:       Maturity in years.
        n_paths: MC path count.
        seed:    RNG seed.

    Returns:
        Discounted expected payoff (float).
    """
    paths = simulate_correlated_gbm(
        S0=S0, r=r, q=q, sigma=sigma, rho=rho, T=T,
        n_steps=1, n_paths=n_paths, seed=seed,
    )
    S_T = paths[:, -1, :]                        # (n_paths, n_assets)
    worst_perf = np.min(S_T / S0, axis=1)         # (n_paths,) — fractional perf
    # Payoff: K * max(1 - worst_perf, 0)  (normalised strike = K/S0[0] when S0 uniform)
    payoff = np.maximum(K - worst_perf * S0[0], 0.0)
    return float(np.exp(-r * T) * payoff.mean())
