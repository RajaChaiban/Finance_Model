"""Phase-3 gate: vol_handle path is additive and equivalent to scalar at flat-σ.

These tests guard the regression-safety contract: passing a *flat* synthetic
vol surface must reproduce the scalar-σ price to numerical precision. Any
divergence here means the engine wiring corrupted the flat-vol path.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

ql = pytest.importorskip("QuantLib")

from src.data.iv_grid import IVGrid
from src.data.vol_surface import build_vol_surface
from src.engines import quantlib_engine

# Use today() so the surface ref_date always matches the QL evaluation date
# the engines pull when ``evaluation_date`` is omitted — otherwise the surface
# looks slightly non-flat one day later and 1e-6 tolerances trip.
REF_DATE = date.today()


def _flat_handle(sigma: float = 0.20) -> ql.BlackVolTermStructureHandle:
    """Build a flat-σ surface and wrap it as a vol handle."""
    strikes = np.array([400.0, 500.0, 600.0])
    expiries = np.array([0.25, 0.5, 1.0, 2.0])
    iv = np.full((len(expiries), len(strikes)), sigma)
    grid = IVGrid(
        strikes=strikes,
        expiries=expiries,
        iv=iv,
        success_rate=1.0,
        n_quotes_total=12,
        n_quotes_inverted=12,
    )
    surface = build_vol_surface(grid, ref_date=REF_DATE)
    return ql.BlackVolTermStructureHandle(surface)


def _ql_today():
    return ql.Date(REF_DATE.day, REF_DATE.month, REF_DATE.year)


@pytest.mark.parametrize("option_type", ["call", "put"])
def test_european_flat_surface_matches_scalar(option_type):
    sigma = 0.20
    S, K, r, T, q = 500.0, 500.0, 0.04, 0.5, 0.015

    today = _ql_today()
    handle = _flat_handle(sigma)

    res_scalar = quantlib_engine.greeks_ql(
        S, K, r, sigma, T, q,
        option_type=option_type, is_american=False,
        evaluation_date=today,
    )
    res_surface = quantlib_engine.greeks_ql(
        S, K, r, sigma, T, q,
        option_type=option_type, is_american=False,
        evaluation_date=today,
        vol_handle=handle,
    )
    assert abs(res_scalar["price"] - res_surface["price"]) < 1e-6


@pytest.mark.parametrize("option_type", ["call", "put"])
def test_american_flat_surface_matches_scalar(option_type):
    sigma = 0.20
    S, K, r, T, q = 500.0, 500.0, 0.04, 0.5, 0.015

    today = _ql_today()
    handle = _flat_handle(sigma)

    p_scalar, _, _ = quantlib_engine.price_american_ql(
        S, K, r, sigma, T, q, n_steps=201, option_type=option_type,
        evaluation_date=today,
    )
    p_surface, _, _ = quantlib_engine.price_american_ql(
        S, K, r, sigma, T, q, n_steps=201, option_type=option_type,
        evaluation_date=today, vol_handle=handle,
    )
    assert abs(p_scalar - p_surface) < 1e-6


def test_knockout_flat_surface_matches_scalar():
    sigma = 0.20
    S, K, B, r, T, q = 500.0, 500.0, 450.0, 0.04, 0.5, 0.015

    today = _ql_today()
    handle = _flat_handle(sigma)

    p_scalar, _, _ = quantlib_engine.price_knockout_ql(
        S, K, B, r, sigma, T, q, "call", evaluation_date=today,
    )
    p_surface, _, _ = quantlib_engine.price_knockout_ql(
        S, K, B, r, sigma, T, q, "call",
        evaluation_date=today, vol_handle=handle,
    )
    assert abs(p_scalar - p_surface) < 1e-6


def test_fdm_greeks_flat_surface_matches_scalar():
    sigma = 0.20
    S, K, r, T, q = 500.0, 500.0, 0.04, 0.5, 0.015

    today = _ql_today()
    handle = _flat_handle(sigma)

    g_scalar = quantlib_engine.greeks_american_fdm_ql(
        S, K, r, sigma, T, q, option_type="put", evaluation_date=today,
    )
    g_surface = quantlib_engine.greeks_american_fdm_ql(
        S, K, r, sigma, T, q, option_type="put",
        evaluation_date=today, vol_handle=handle,
    )
    assert abs(g_scalar["price"] - g_surface["price"]) < 1e-6
    assert abs(g_scalar["delta"] - g_surface["delta"]) < 1e-6
    assert abs(g_scalar["gamma"] - g_surface["gamma"]) < 1e-6


def test_knockout_smile_via_closed_form_bridge_differs_from_flat():
    """The closed-form AnalyticBarrierEngine is flat-vol by construction —
    it can't consume a smile directly. The smile-aware behaviour for KOs
    comes from the *closed-form bridge* in main.py: sample the surface at
    the barrier-side strike and feed that scalar to the engine.

    This test exercises that bridge: a steep downside-skewed surface,
    sampled at the barrier point, must yield a materially different KO
    price than the same engine driven by the ATM σ.
    """
    from src.data.vol_surface import sample_sigma_for_closed_form

    S, K, B, r, T, q = 500.0, 500.0, 450.0, 0.04, 0.5, 0.015
    today = _ql_today()

    # Steep smile: 30 % at K=400 (deep OTM put), 25 % at K=450 (the barrier),
    # 20 % ATM (K=500), 18 % at K=600 upside. Barrier strike is in the grid
    # so the surface evaluates exactly to its grid value with no interpolation.
    strikes = np.array([400.0, 450.0, 500.0, 600.0])
    expiries = np.array([0.25, 0.5, 1.0, 2.0])
    iv = np.tile(np.array([0.30, 0.25, 0.20, 0.18]), (len(expiries), 1))
    smile_grid = IVGrid(
        strikes=strikes, expiries=expiries, iv=iv,
        success_rate=1.0, n_quotes_total=16, n_quotes_inverted=16,
    )
    surface = build_vol_surface(smile_grid, ref_date=REF_DATE)

    # Flat path: scalar σ at ATM (the plain "use 30-day historical" approach).
    sigma_atm = 0.20
    p_flat, _, _ = quantlib_engine.price_knockout_ql(
        S, K, B, r, sigma_atm, T, q, "call", evaluation_date=today,
    )

    # Smile-aware path: sample the surface at the barrier side.
    sigma_bridge = sample_sigma_for_closed_form(
        surface, K=K, T=T, S=S, barrier=B,
    )
    assert sigma_bridge > sigma_atm  # downside-skewed grid → fatter near-barrier vol
    p_smile, _, _ = quantlib_engine.price_knockout_ql(
        S, K, B, r, sigma_bridge, T, q, "call", evaluation_date=today,
    )

    # The price MUST move materially when σ jumps from 20 % (ATM) to 25 %
    # (barrier-side). Direction depends on which dominates — higher vol means
    # both more knockout *and* more upside; for a 10 %-barrier KO call those
    # two effects don't cancel cleanly. The point of the bridge is that the
    # smile is *visible* in the price, not that it pushes any specific way.
    assert abs(p_smile - p_flat) > 0.5, (
        f"Bridge-sampled smile σ should produce a materially different KO "
        f"price; got flat={p_flat:.4f} smile={p_smile:.4f}."
    )


def test_local_vol_pde_ko_call_differs_from_flat_extremes():
    """Local-vol PDE on a smile surface must give a *third* price that's
    neither the flat-at-ATM-σ nor the flat-at-σ_barrier value — the whole
    point of switching from ``AnalyticBarrierEngine`` to
    ``FdBlackScholesBarrierEngine`` is that the engine sees σ(S, t) along
    the path, not a single scalar collapsed at one (K, T) point.

    Synthesises a SPY-like smile (steep put-wing, flat call-wing), prices a
    down-and-out call three ways, and asserts the PDE result is materially
    distinct from both flat collapses. Directionality between the three
    is ambiguous (depends on barrier distance and smile geometry), so the
    test only checks materiality.
    """
    strikes = np.array([400., 430., 460., 500., 540., 580., 620.])
    expiries = np.array([0.05, 0.10, 0.25, 0.50, 1.0])
    iv = np.empty((len(expiries), len(strikes)))
    for ti, T_ in enumerate(expiries):
        for ki, K_ in enumerate(strikes):
            m = (K_ - 500.0) / 500.0
            iv[ti, ki] = (0.20 + abs(m) * 0.5) if m < 0 else (0.20 - m * 0.1)
            iv[ti, ki] -= 0.02 * np.log(1 + T_)
    grid = IVGrid(
        strikes=strikes, expiries=expiries, iv=iv,
        n_quotes_total=int(iv.size), n_quotes_inverted=int(iv.size),
        success_rate=1.0,
    )
    surface = build_vol_surface(grid, ref_date=REF_DATE)
    vol_handle = ql.BlackVolTermStructureHandle(surface)

    S, K, B, T, r, q = 500.0, 500.0, 450.0, 0.25, 0.035, 0.012
    today = _ql_today()
    s_atm = float(surface.blackVol(T, K, True))
    s_bar = float(surface.blackVol(T, B, True))
    assert s_bar > s_atm, "Synthetic surface should have steep put-wing."

    p_flat_atm, _, _ = quantlib_engine.price_knockout_ql(
        S, K, B, r, s_atm, T, q, "call", evaluation_date=today,
    )
    p_flat_bar, _, _ = quantlib_engine.price_knockout_ql(
        S, K, B, r, s_bar, T, q, "call", evaluation_date=today,
    )
    p_pde, _, _ = quantlib_engine.price_knockout_ql(
        S, K, B, r, s_atm, T, q, "call", evaluation_date=today,
        vol_handle=vol_handle, use_local_vol_pde=True,
    )
    assert abs(p_pde - p_flat_atm) > 0.1, (
        f"PDE local-vol KO should differ materially from flat-ATM σ; "
        f"got pde={p_pde:.4f} flat_atm={p_flat_atm:.4f}."
    )
    assert abs(p_pde - p_flat_bar) > 0.1, (
        f"PDE local-vol KO should differ materially from flat-σ_barrier; "
        f"got pde={p_pde:.4f} flat_bar={p_flat_bar:.4f}."
    )


def test_local_vol_pde_flat_surface_matches_analytic():
    """A flat synthetic surface fed to the FD-with-local-vol path must
    reproduce the AnalyticBarrierEngine price (within FD discretisation
    error) — proving the engine wiring is sound and isn't introducing a
    systematic bias on the flat-vol path."""
    sigma = 0.22
    handle = _flat_handle(sigma)
    S, K, B, T, r, q = 500.0, 500.0, 450.0, 0.25, 0.035, 0.012
    today = _ql_today()

    p_analytic, _, _ = quantlib_engine.price_knockout_ql(
        S, K, B, r, sigma, T, q, "call", evaluation_date=today,
    )
    p_pde, _, _ = quantlib_engine.price_knockout_ql(
        S, K, B, r, sigma, T, q, "call", evaluation_date=today,
        vol_handle=handle, use_local_vol_pde=True,
    )
    # FD with 200×200 grid + Dupire derivation introduces some error vs
    # the closed form. 1 % of price is comfortably tighter than the
    # smile-driven movements we care about (which are 5-30 % of price).
    assert abs(p_pde - p_analytic) / p_analytic < 0.01, (
        f"FD local-vol on a flat surface should match analytic within 1 %; "
        f"got pde={p_pde:.4f} analytic={p_analytic:.4f}."
    )


def test_router_forwards_vol_handle_through_kwargs():
    """The router must forward vol_handle on to the QL engines via kwargs.
    Otherwise the surface path silently degrades to flat. A flat-vol surface
    should round-trip through the router to the same price as the scalar."""
    from src.engines import router

    sigma = 0.20
    S, K, r, T, q = 500.0, 500.0, 0.04, 0.5, 0.015
    handle = _flat_handle(sigma)

    pricer, greeks_fn, _ = router.route("european_call")
    p_no_handle, _, _ = pricer(S=S, K=K, r=r, sigma=sigma, T=T, q=q)
    p_with_handle, _, _ = pricer(
        S=S, K=K, r=r, sigma=sigma, T=T, q=q, vol_handle=handle,
    )
    assert abs(p_no_handle - p_with_handle) < 1e-6

    # Same for greeks
    g_no = greeks_fn(S=S, K=K, r=r, sigma=sigma, T=T, q=q)
    g_with = greeks_fn(S=S, K=K, r=r, sigma=sigma, T=T, q=q, vol_handle=handle)
    assert abs(g_no["price"] - g_with["price"]) < 1e-6
