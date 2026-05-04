"""Tests for build_vol_surface + sample_sigma_for_closed_form."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

ql = pytest.importorskip("QuantLib")

from src.data.iv_grid import IVGrid
from src.data.vol_surface import build_vol_surface, sample_sigma_for_closed_form


REF_DATE = date(2026, 4, 26)


def _flat_grid(sigma: float = 0.20) -> IVGrid:
    strikes = np.array([475.0, 500.0, 525.0])
    expiries = np.array([0.25, 0.5, 1.0])
    iv = np.full((len(expiries), len(strikes)), sigma)
    return IVGrid(
        strikes=strikes,
        expiries=expiries,
        iv=iv,
        success_rate=1.0,
        n_quotes_total=9,
        n_quotes_inverted=9,
    )


def _smile_grid() -> IVGrid:
    strikes = np.array([475.0, 500.0, 525.0])
    expiries = np.array([0.25, 0.5, 1.0])
    # V-shape skew: deeper IV in the wings, lower at ATM.
    iv = np.array(
        [
            [0.30, 0.18, 0.22],
            [0.28, 0.18, 0.22],
            [0.26, 0.18, 0.22],
        ]
    )
    return IVGrid(
        strikes=strikes,
        expiries=expiries,
        iv=iv,
        success_rate=1.0,
        n_quotes_total=9,
        n_quotes_inverted=9,
    )


def test_flat_surface_returns_input_sigma():
    """A flat 20 % grid → surface.blackVol returns 0.20 at every (T, K)."""
    sigma = 0.20
    grid = _flat_grid(sigma)
    surface = build_vol_surface(grid, ref_date=REF_DATE)

    for T in (0.25, 0.5, 0.75, 1.0):
        for K in (475.0, 487.5, 500.0, 512.5, 525.0):
            assert abs(surface.blackVol(T, K) - sigma) < 1e-9, (T, K)


def test_smile_surface_recovers_skew():
    """Skewed grid → surface preserves the V-shape at the input grid points."""
    grid = _smile_grid()
    surface = build_vol_surface(grid, ref_date=REF_DATE)

    # At the grid point T=0.5, σ at K=475 must be > σ at K=500.
    assert surface.blackVol(0.5, 475.0) > surface.blackVol(0.5, 500.0)
    assert surface.blackVol(0.5, 525.0) > surface.blackVol(0.5, 500.0)


def test_surface_rejects_too_sparse():
    """A 1-strike OR 1-expiry grid must raise."""
    iv = np.array([[0.20]])
    g = IVGrid(
        strikes=np.array([500.0]),
        expiries=np.array([0.5]),
        iv=iv,
        success_rate=1.0,
        n_quotes_total=1,
        n_quotes_inverted=1,
    )
    with pytest.raises(ValueError):
        # Caught at IVGrid post-init OR at build_vol_surface — both acceptable.
        build_vol_surface(g, ref_date=REF_DATE)


def test_surface_fills_nan_along_strike():
    """Surface builder must fill NaN holes via linear interpolation."""
    strikes = np.array([475.0, 500.0, 525.0])
    expiries = np.array([0.25, 0.5])
    iv = np.array(
        [
            [0.20, np.nan, 0.20],
            [0.20, 0.20, 0.20],
        ]
    )
    g = IVGrid(
        strikes=strikes,
        expiries=expiries,
        iv=iv,
        success_rate=5 / 6,
        n_quotes_total=6,
        n_quotes_inverted=5,
    )
    surface = build_vol_surface(g, ref_date=REF_DATE)
    # The hole at (T=0.25, K=500) interpolated linearly from neighbours → 0.20.
    assert abs(surface.blackVol(0.25, 500.0) - 0.20) < 1e-9


def test_sample_european_evaluates_at_strike():
    grid = _smile_grid()
    surface = build_vol_surface(grid, ref_date=REF_DATE)
    sigma = sample_sigma_for_closed_form(
        surface, K=525.0, T=0.5, S=500.0, barrier=None
    )
    assert abs(sigma - surface.blackVol(0.5, 525.0)) < 1e-12


def test_sample_knockout_uses_barrier_vol():
    """Knockout: vol must be evaluated at the barrier itself — that's the
    smile point that drives breach probability. The V-shape grid has the
    deepest σ at K=475 (downside skew); a down-out barrier at 475 should
    pull that fatter vol into the closed-form scalar."""
    grid = _smile_grid()
    surface = build_vol_surface(grid, ref_date=REF_DATE)

    # Down-out: barrier below spot. ATM strike, barrier in the wing.
    sigma_atm = surface.blackVol(0.5, 500.0)
    sigma_b = sample_sigma_for_closed_form(
        surface, K=500.0, T=0.5, S=500.0, barrier=475.0
    )
    assert abs(sigma_b - surface.blackVol(0.5, 475.0)) < 1e-12
    assert sigma_b > sigma_atm  # smile lifts σ at the wing


def test_sample_up_out_uses_barrier_vol():
    """Up-out: same rule — sample at the barrier."""
    grid = _smile_grid()
    surface = build_vol_surface(grid, ref_date=REF_DATE)
    sigma = sample_sigma_for_closed_form(
        surface, K=500.0, T=0.5, S=500.0, barrier=525.0
    )
    assert abs(sigma - surface.blackVol(0.5, 525.0)) < 1e-12


def test_sample_rejects_zero_T():
    grid = _flat_grid()
    surface = build_vol_surface(grid, ref_date=REF_DATE)
    with pytest.raises(ValueError):
        sample_sigma_for_closed_form(surface, K=500.0, T=0.0, S=500.0)
