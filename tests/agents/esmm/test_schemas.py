"""Schema-level smoke tests."""

from __future__ import annotations

import pytest

from src.agents.esmm.schemas import (
    AgenticDecision,
    AgenticRunResult,
    ConfigProposal,
    Regime,
    RegimeObservation,
    TCAScore,
)
from src.esmm.schemas import MarketMakingConfig, TCABreakdown


def test_regime_enum_has_all_four_levels():
    assert {r.value for r in Regime} == {"calm", "trending", "volatile", "stress"}


def test_regime_observation_freezes_after_construction():
    obs = RegimeObservation(
        regime=Regime.CALM, rv_fast=0, rv_slow=0, momentum=0,
        signed_flow=0, rv_ratio=1, n_snapshots=10,
    )
    with pytest.raises(Exception):
        obs.regime = Regime.STRESS  # frozen


def test_tca_score_score_field_clamped_at_construction():
    """score must be in [0, 100]."""
    with pytest.raises(Exception):
        TCAScore(
            score=150,  # over 100
            spread_capture_ratio=0,
            adverse_selection_ratio=0,
            hedge_drag_ratio=0,
            inventory_volatility=0,
        )
    with pytest.raises(Exception):
        TCAScore(
            score=-10,  # under 0
            spread_capture_ratio=0,
            adverse_selection_ratio=0,
            hedge_drag_ratio=0,
            inventory_volatility=0,
        )


def test_config_proposal_carries_baseline_config_payload():
    cp = ConfigProposal(
        config=MarketMakingConfig(symbol="SPY"),
        parent_regime=Regime.CALM,
        rationale="x",
    )
    assert cp.config.symbol == "SPY"
    assert cp.iteration == 0


def test_agentic_decision_records_full_loop_state():
    obs = RegimeObservation(
        regime=Regime.CALM, rv_fast=0, rv_slow=0, momentum=0,
        signed_flow=0, rv_ratio=1, n_snapshots=10,
    )
    proposal = ConfigProposal(
        config=MarketMakingConfig(symbol="X"),
        parent_regime=Regime.CALM, rationale="r",
    )
    tca = TCABreakdown(
        spread_capture_pnl=10, inventory_pnl=0, hedge_pnl=0,
        adverse_selection_pnl=0, fees_pnl=0, total_pnl=10,
        n_fills=5, avg_fill_size=20,
    )
    score = TCAScore(
        score=80, spread_capture_ratio=1, adverse_selection_ratio=0,
        hedge_drag_ratio=0, inventory_volatility=0,
    )
    decision = AgenticDecision(
        iteration=0, observation=obs, proposal=proposal,
        tca=tca, score=score, accepted=True,
    )
    assert decision.accepted
    assert decision.score.score == 80


def test_agentic_run_result_history_can_be_empty():
    r = AgenticRunResult(
        history=[], best_decision=None,
        converged=False, stopped_reason="no_snapshots",
    )
    assert r.history == []
    assert r.best_decision is None
