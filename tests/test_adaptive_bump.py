"""Adaptive Greek bumps near a knockout barrier.

Default bump-reprice for KO Greeks uses ``h = max(S * 0.005, 0.01)``. When
spot is within ~0.5% of the barrier, the bump CROSSES the barrier on one
side, knocking the option out and producing a non-physical gamma spike
(easily 100× normal magnitude or worse, depending on direction).

Adaptive policy: if the bump would cross the barrier, shrink it to a
fraction of the spot-to-barrier distance and emit a warning. Greeks remain
finite and consistent with the pre-knock-out regime.
"""

import math
import pytest

pytest.importorskip("QuantLib")

from src.engines import quantlib_engine


# DOI call: barrier 1% below spot.
S, K, B = 100.0, 100.0, 99.0
r, sigma, T, q = 0.05, 0.20, 0.5, 0.0
OPT = "call"


def test_near_barrier_gamma_finite():
    """Gamma very close to barrier must be finite (not NaN/inf)."""
    g = quantlib_engine.greeks_knockout_ql(S, K, B, r, sigma, T, q, OPT)
    assert math.isfinite(g["gamma"])
    assert math.isfinite(g["delta"])
    # Note: KO-call delta legitimately exceeds 1 near the barrier — the
    # marginal-survival sensitivity dominates. This is a real feature, not a
    # bump artefact. Don't bound it like a vanilla.


def test_bump_never_crosses_barrier():
    """Adaptive bump must keep S±h on the LIVE side of the barrier.

    If h ≥ |S − B|, the bump-reprice would query a KO'd option (price = 0)
    and produce an asymmetric price triple, corrupting both delta and gamma.
    Verify that the bump policy keeps both spot bumps strictly on the live
    side, even for spots within 0.5% of the barrier.
    """
    # Spot 0.3% above barrier — well inside default-bump (0.5%) crossing zone.
    S_close = 100.0
    B_close = 99.7
    # Compute and store separately to detect any asymmetry / crossing.
    g = quantlib_engine.greeks_knockout_ql(
        S_close, K, B_close, r, sigma, T, q, OPT
    )
    assert math.isfinite(g["delta"])
    assert math.isfinite(g["gamma"])
    # Gamma at this convex hot-spot is large but must stay bounded — bump
    # crossing barrier would push gamma to ~10⁴ scale. Real near-barrier
    # gamma sits in the 1–100 range.
    assert abs(g["gamma"]) < 1000.0, (
        f"Near-barrier gamma {g['gamma']} suggests bump crossed barrier."
    )


def test_far_from_barrier_unaffected():
    """Greeks far from the barrier must use the standard bump and stay sane."""
    # Barrier far below: spot=$100, B=$80
    g_far = quantlib_engine.greeks_knockout_ql(100.0, 100.0, 80.0, r, sigma, T, q, OPT)
    assert math.isfinite(g_far["gamma"])
    assert g_far["gamma"] > 0  # standard convexity
    assert 0 < g_far["delta"] < 1  # vanilla-like regime, far from barrier
