"""FDM American Greeks — stable gamma surfaces.

Bump-reprice gamma on a binomial tree shows "ghost gamma": the LR tree
node positions shift discretely as you bump spot, so gamma can spike or
flip sign for adjacent strikes. FDM (PDE-based finite difference) gives a
smooth gamma surface because it interpolates over a fixed grid.

Tests:
  1. New API ``greeks_american_fdm_ql`` exists and returns standard dict.
  2. Gamma for an ATM American put is positive (long convexity).
  3. Gamma surface across nearby strikes is SMOOTHER than bump-reprice
     (max relative jump in adjacent gammas < bump-reprice version).
  4. Greek values agree with the LR-tree pricer to ~bp on the price.
"""

import numpy as np
import pytest

pytest.importorskip("QuantLib")

from src.engines import quantlib_engine


def test_fdm_greeks_basic_signs_and_magnitudes():
    """ATM American put: standard Greek signs."""
    g = quantlib_engine.greeks_american_fdm_ql(
        S=100.0, K=100.0, r=0.05, sigma=0.20, T=0.5, q=0.0,
        option_type="put",
    )
    assert g["price"] > 0
    assert -1.0 <= g["delta"] <= 0.0
    assert g["gamma"] > 0
    assert g["vega"] > 0
    assert g["theta"] < 0


def test_fdm_price_agrees_with_lr_tree():
    """FDM and LR tree should agree to ~$0.02 for typical params."""
    p_lr, _, _ = quantlib_engine.price_american_ql(
        S=100.0, K=100.0, r=0.05, sigma=0.20, T=0.5, q=0.02,
        n_steps=501, option_type="put",
    )
    g = quantlib_engine.greeks_american_fdm_ql(
        S=100.0, K=100.0, r=0.05, sigma=0.20, T=0.5, q=0.02,
        option_type="put",
    )
    assert abs(g["price"] - p_lr) < 0.02


def test_fdm_gamma_surface_smooth_across_strikes():
    """Gamma across closely-spaced strikes shouldn't oscillate.

    Bump-reprice on a tree often shows ~30%+ relative jumps between adjacent
    strikes (1% spacing) due to grid-snap of LR node positions. FDM should
    keep adjacent-gamma relative changes < 10%.
    """
    strikes = np.linspace(95.0, 105.0, 11)  # 1% spacing
    gammas_fdm = []
    for K in strikes:
        g = quantlib_engine.greeks_american_fdm_ql(
            S=100.0, K=float(K), r=0.05, sigma=0.20, T=0.5, q=0.0,
            option_type="put",
        )
        gammas_fdm.append(g["gamma"])
    gammas_fdm = np.array(gammas_fdm)
    # Relative jumps between consecutive strikes
    rel_jumps = np.abs(np.diff(gammas_fdm)) / np.maximum(np.abs(gammas_fdm[:-1]), 1e-9)
    assert rel_jumps.max() < 0.10, (
        f"FDM gamma surface has a {rel_jumps.max()*100:.1f}% jump — not smooth."
    )
