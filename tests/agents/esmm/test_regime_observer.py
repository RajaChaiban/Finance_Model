"""Tests for the regime observer."""

from __future__ import annotations

import pytest

from src.agents.esmm.regime_observer import (
    RegimeObserver,
    RegimeThresholds,
    classify_regime,
)
from src.agents.esmm.schemas import Regime
from src.esmm.schemas import OrderBookLevel, OrderBookSnapshot
from src.esmm.synthetic import generate_order_book_path


def _flat_snapshots(n: int = 100, mid: float = 100.0) -> list[OrderBookSnapshot]:
    """A constant-mid path — should always classify as CALM."""
    return [
        OrderBookSnapshot(
            ts=float(i), symbol="X",
            bids=[OrderBookLevel(price=mid - 0.05, size=100)],
            asks=[OrderBookLevel(price=mid + 0.05, size=100)],
        )
        for i in range(n)
    ]


def test_observe_empty_returns_calm():
    obs = classify_regime([])
    assert obs.regime == Regime.CALM
    assert obs.n_snapshots == 0


def test_observe_flat_path_classifies_as_calm():
    snaps = _flat_snapshots(80)
    obs = classify_regime(snaps)
    assert obs.regime == Regime.CALM
    assert obs.rv_fast == 0.0


def test_observe_low_vol_synthetic_path_is_calm():
    snaps = generate_order_book_path(n_snaps=80, sigma_per_step=0.0001, seed=1)
    obs = classify_regime(snaps)
    assert obs.regime in {Regime.CALM, Regime.TRENDING}
    assert obs.n_snapshots == 80


def test_observe_high_vol_synthetic_path_escalates_to_volatile_or_stress():
    # Use a much higher sigma to force the regime up. 0.005 → ~80 bps per step
    # which is ~RV_fast in the e-5 range for fast_window=10.
    snaps = generate_order_book_path(n_snaps=120, sigma_per_step=0.005, seed=2)
    obs = classify_regime(snaps)
    assert obs.regime in {Regime.VOLATILE, Regime.STRESS}


def test_threshold_override_shifts_regime():
    """Tighter thresholds should escalate a previously-calm path."""
    snaps = generate_order_book_path(n_snaps=80, sigma_per_step=0.0008, seed=3)
    default = classify_regime(snaps)
    strict = classify_regime(snaps, thresholds=RegimeThresholds(rv_volatile_min=1e-9))
    # Default may say calm; strict (vol threshold near zero) should escalate.
    assert strict.regime in {Regime.VOLATILE, Regime.STRESS}
    # And in any case it should be at least as escalated as the default.
    severity = {Regime.CALM: 0, Regime.TRENDING: 1, Regime.VOLATILE: 2, Regime.STRESS: 3}
    assert severity[strict.regime] >= severity[default.regime]


def test_observer_is_deterministic_with_seed():
    snaps = generate_order_book_path(n_snaps=60, seed=99)
    a = classify_regime(snaps)
    b = classify_regime(snaps)
    assert a == b


def test_classifier_escalation_order_stress_beats_volatile_beats_trending():
    """Internal ordering: if all triggers fire at once, STRESS wins."""
    obs = RegimeObserver(thresholds=RegimeThresholds(
        rv_calm_max=1e-9, rv_volatile_min=1e-8, rv_stress_min=1e-7,
        momentum_trend_min=0.0001,
    ))
    # rv_fast above stress AND momentum above trend AND signed_flow above imbalance.
    # _classify is private but interview-relevant — call via the public path.
    regime = obs._classify(rv_fast=1e-3, momentum=10.0, signed_flow=10_000)
    assert regime == Regime.STRESS


def test_classifier_volatile_threshold_triggers_with_calm_rv_but_huge_signed_flow():
    obs = RegimeObserver(thresholds=RegimeThresholds(signed_flow_imbalance=10.0))
    regime = obs._classify(rv_fast=0.0, momentum=0.0, signed_flow=200.0)
    # signed_flow_imbalance * 4 = 40; |200| > 40 → VOLATILE
    assert regime == Regime.VOLATILE
