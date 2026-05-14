"""Tests for the config strategist."""

from __future__ import annotations

import pytest

from src.agents.esmm.config_strategist import (
    ConfigStrategist,
    CriticAdjustments,
    REGIME_MULTIPLIERS,
)
from src.agents.esmm.schemas import (
    ConfigProposal,
    Regime,
    RegimeObservation,
    TCAScore,
)
from src.esmm.schemas import MarketMakingConfig


def _obs(regime: Regime) -> RegimeObservation:
    return RegimeObservation(
        regime=regime, rv_fast=0, rv_slow=0, momentum=0,
        signed_flow=0, rv_ratio=1, n_snapshots=10,
    )


def _baseline() -> MarketMakingConfig:
    return MarketMakingConfig(
        symbol="SPY",
        base_half_spread_bps=10.0,
        inventory_skew_bps_per_unit=0.5,
        max_inventory=1000.0,
        delta_hedge_threshold=200.0,
        delta_hedge_band=50.0,
    )


def test_calm_regime_returns_baseline_unchanged():
    s = ConfigStrategist(baseline=_baseline())
    proposal = s.propose(_obs(Regime.CALM))
    assert proposal.config.base_half_spread_bps == pytest.approx(10.0)
    assert proposal.config.inventory_skew_bps_per_unit == pytest.approx(0.5)
    assert proposal.config.max_inventory == pytest.approx(1000.0)
    assert proposal.parent_regime == Regime.CALM


def test_volatile_regime_widens_spread_and_tightens_inventory():
    s = ConfigStrategist(baseline=_baseline())
    proposal = s.propose(_obs(Regime.VOLATILE))
    assert proposal.config.base_half_spread_bps > _baseline().base_half_spread_bps
    assert proposal.config.max_inventory < _baseline().max_inventory


def test_stress_regime_widens_dramatically():
    s = ConfigStrategist(baseline=_baseline())
    calm = s.propose(_obs(Regime.CALM))
    stress = s.propose(_obs(Regime.STRESS))
    # Stress half-spread should be at least double calm.
    assert stress.config.base_half_spread_bps >= 2.0 * calm.config.base_half_spread_bps
    # And max_inventory should drop hard.
    assert stress.config.max_inventory <= 0.25 * calm.config.max_inventory


def test_min_floor_on_max_inventory_and_hedge_threshold():
    """Stress multiplier of 0.2 on max_inv=200 should land at floor=50, not 40."""
    s = ConfigStrategist(baseline=MarketMakingConfig(symbol="X", max_inventory=200, delta_hedge_threshold=50))
    proposal = s.propose(_obs(Regime.STRESS))
    assert proposal.config.max_inventory >= 50.0
    assert proposal.config.delta_hedge_threshold >= 20.0


def test_critic_high_adverse_selection_widens_spread_further():
    s = ConfigStrategist(baseline=_baseline())
    bad_score = TCAScore(
        score=30, spread_capture_ratio=0.4,
        adverse_selection_ratio=0.8,  # well over default 0.4 threshold
        hedge_drag_ratio=0.1,
        inventory_volatility=0.1,
    )
    p_no_score = s.propose(_obs(Regime.CALM))
    p_with_score = s.propose(_obs(Regime.CALM), prior_score=bad_score)
    assert p_with_score.config.base_half_spread_bps > p_no_score.config.base_half_spread_bps
    assert "adverse selection" in p_with_score.rationale.lower()


def test_critic_high_hedge_drag_loosens_hedge_band():
    s = ConfigStrategist(baseline=_baseline())
    bad = TCAScore(
        score=30, spread_capture_ratio=0.4,
        adverse_selection_ratio=0.1, hedge_drag_ratio=0.9,
        inventory_volatility=0.1,
    )
    p_no = s.propose(_obs(Regime.CALM))
    p_with = s.propose(_obs(Regime.CALM), prior_score=bad)
    assert p_with.config.delta_hedge_band > p_no.config.delta_hedge_band
    assert p_with.config.delta_hedge_threshold > p_no.config.delta_hedge_threshold
    assert "hedge" in p_with.rationale.lower()


def test_critic_high_inventory_vol_tightens_max_inventory_and_bumps_skew():
    s = ConfigStrategist(baseline=_baseline())
    bad = TCAScore(
        score=30, spread_capture_ratio=0.4,
        adverse_selection_ratio=0.1, hedge_drag_ratio=0.1,
        inventory_volatility=2.0,  # well over 1.0 threshold
    )
    p_no = s.propose(_obs(Regime.CALM))
    p_with = s.propose(_obs(Regime.CALM), prior_score=bad)
    assert p_with.config.max_inventory < p_no.config.max_inventory
    assert p_with.config.inventory_skew_bps_per_unit > p_no.config.inventory_skew_bps_per_unit


def test_iteration_field_propagates_to_proposal():
    s = ConfigStrategist(baseline=_baseline())
    proposal = s.propose(_obs(Regime.CALM), iteration=3)
    assert proposal.iteration == 3


def test_regime_multipliers_table_covers_all_four_regimes():
    """Defensive: ensure every Regime is wired into the multiplier table."""
    for r in Regime:
        assert r in REGIME_MULTIPLIERS
        assert all(k in REGIME_MULTIPLIERS[r] for k in ("half_spread", "skew", "max_inv", "hedge_thresh"))
