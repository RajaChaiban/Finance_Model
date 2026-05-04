"""Tests for build_iv_grid: synthetic chain → IV grid round-trip."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.data.iv_grid import IVGrid, build_iv_grid
from src.engines.black_scholes import price_european


REF_DATE = date(2026, 4, 26)


def _synthesise_chain(
    S: float,
    r: float,
    q: float,
    sigma: float,
    expiries: list[date],
    strikes: list[float],
) -> dict[date, pd.DataFrame]:
    """Build a chain whose mid prices are generated from a *known* flat sigma.

    Round-trip property: build_iv_grid on this chain should recover sigma at
    every (T, K) cell to several decimals.
    """
    chain: dict[date, pd.DataFrame] = {}
    for expiry in expiries:
        T = (expiry - REF_DATE).days / 365.0
        rows = []
        for K in strikes:
            for opt in ("call", "put"):
                price = price_european(S, K, r, sigma, T, q, option_type=opt)
                bid = max(price - 0.01, 0.001)
                ask = price + 0.01
                rows.append(
                    {
                        "strike": float(K),
                        "bid": bid,
                        "ask": ask,
                        "mid": price,
                        "option_type": opt,
                        "dte_days": (expiry - REF_DATE).days,
                        "moneyness": K / S - 1.0,
                    }
                )
        chain[expiry] = pd.DataFrame(rows)
    return chain


def test_build_iv_grid_round_trip_recovers_flat_sigma():
    """A chain priced at flat 20 % vol should invert back to ~20 % at every
    cell that's not deep-OTM-short-tenor. Far-wing short-tenor quotes are
    cents-priced and Brent will refuse them — that's expected, the build
    just leaves the cell NaN. Cell-level recovery accuracy is the assertion.
    """
    S, r, q, sigma = 500.0, 0.04, 0.015, 0.20
    expiries = [REF_DATE + timedelta(days=d) for d in (90, 180, 365)]
    strikes = [495.0, 500.0, 505.0]
    chain = _synthesise_chain(S, r, q, sigma, expiries, strikes)

    grid = build_iv_grid(chain, S=S, r=r, q=q, today=REF_DATE)

    assert isinstance(grid, IVGrid)
    assert grid.iv.shape == (len(expiries), len(strikes))
    finite = grid.iv[np.isfinite(grid.iv)]
    assert len(finite) == grid.iv.size, "tight near-ATM grid should fully invert"
    np.testing.assert_allclose(finite, sigma, atol=2e-4)


def test_build_iv_grid_strict_monotone_axes():
    """Strikes and expiries must come back strictly ascending."""
    S, r, q, sigma = 500.0, 0.04, 0.015, 0.18
    # Provide expiries DELIBERATELY out of order to exercise the sort.
    expiries = [REF_DATE + timedelta(days=d) for d in (200, 60, 120)]
    strikes = [510.0, 490.0, 500.0]
    chain = _synthesise_chain(S, r, q, sigma, expiries, strikes)

    grid = build_iv_grid(chain, S=S, r=r, q=q, today=REF_DATE)
    assert np.all(np.diff(grid.expiries) > 0)
    assert np.all(np.diff(grid.strikes) > 0)


def test_build_iv_grid_skips_unconvergent_quotes():
    """Sub-intrinsic / arbitrage-violating quotes should be silently skipped."""
    S, r, q, sigma = 500.0, 0.04, 0.015, 0.20
    expiries = [REF_DATE + timedelta(days=d) for d in (60, 120)]
    strikes = [490.0, 500.0, 510.0]
    chain = _synthesise_chain(S, r, q, sigma, expiries, strikes)

    # Inject a no-arb-violating mid on the put at K=490 — the OTM side that
    # build_iv_grid picks when K < S. A put can never be worth more than the
    # strike, so target=10_000 is outside the reachable range for any σ in
    # [0.001, 5.0] and the inverter raises → cell becomes NaN.
    df = chain[expiries[0]].copy()
    df.loc[(df["strike"] == 490.0) & (df["option_type"] == "put"), "mid"] = 10_000.0
    chain[expiries[0]] = df

    grid = build_iv_grid(chain, S=S, r=r, q=q, today=REF_DATE)
    assert grid.success_rate < 1.0
    assert grid.success_rate >= 0.6


def test_build_iv_grid_rejects_too_sparse():
    """A chain with only one expiry (or one strike) must raise."""
    S, r, q, sigma = 500.0, 0.04, 0.015, 0.20
    expiries = [REF_DATE + timedelta(days=60)]
    strikes = [490.0, 500.0]
    chain = _synthesise_chain(S, r, q, sigma, expiries, strikes)

    with pytest.raises(ValueError, match="too sparse"):
        build_iv_grid(chain, S=S, r=r, q=q, today=REF_DATE)


def test_build_iv_grid_empty_chain_raises():
    with pytest.raises(ValueError, match="empty"):
        build_iv_grid({}, S=500.0, r=0.04, q=0.015, today=REF_DATE)


def test_build_iv_grid_low_success_rate_raises():
    """If 90 % of quotes fail to invert, the build must abort."""
    S, r, q = 500.0, 0.04, 0.015
    expiries = [REF_DATE + timedelta(days=d) for d in (60, 120)]
    strikes = [490.0, 500.0, 510.0]
    chain = _synthesise_chain(S, r, q, 0.20, expiries, strikes)

    # Replace nearly all mids with garbage that won't invert (sub-intrinsic).
    for expiry, df in chain.items():
        df = df.copy()
        df.loc[df.index != 0, "mid"] = 0.0001
        chain[expiry] = df

    with pytest.raises(ValueError, match="success rate"):
        build_iv_grid(chain, S=S, r=r, q=q, today=REF_DATE, min_success_rate=0.6)
