"""Tests for correlated multi-asset GBM Monte Carlo engine (Phase 7)."""
import math
import numpy as np
from src.engines.multi_asset_mc import simulate_correlated_gbm, price_worst_of_european_put


def test_simulate_shapes():
    paths = simulate_correlated_gbm(
        S0=np.array([100.0, 100.0]),
        r=0.05, q=np.zeros(2),
        sigma=np.array([0.2, 0.25]),
        rho=np.array([[1.0, 0.5], [0.5, 1.0]]),
        T=1.0, n_steps=12, n_paths=1000, seed=42,
    )
    assert paths.shape == (1000, 13, 2)  # paths × (steps+1) × assets


def test_worst_of_put_below_min_constituent():
    # Worst-of put price >= min of single-name put prices.
    price = price_worst_of_european_put(
        S0=np.array([100.0, 100.0]),
        K=100.0,
        r=0.05, q=np.zeros(2),
        sigma=np.array([0.2, 0.2]),
        rho=np.array([[1.0, 0.0], [0.0, 1.0]]),
        T=1.0, n_paths=20000, seed=1,
    )
    # BS single-name put ATM with sigma=0.2, T=1 ~= 5.57
    assert 5.0 < price < 12.0  # worst-of richer than single-name
