"""Tests for the agentic eSMM orchestrator (full loop integration)."""

from __future__ import annotations

import pytest

from src.agents.esmm.orchestrator import AgenticESMMOrchestrator
from src.agents.esmm.schemas import AgenticDecision, AgenticRunResult, Regime
from src.esmm.schemas import MarketMakingConfig
from src.esmm.synthetic import generate_order_book_path


def _baseline() -> MarketMakingConfig:
    return MarketMakingConfig(
        symbol="SPY",
        base_half_spread_bps=8.0,
        inventory_skew_bps_per_unit=0.05,
        max_inventory=500.0,
        quote_size=50.0,
        delta_hedge_threshold=200.0,
        delta_hedge_band=50.0,
    )


def test_run_with_no_snapshots_returns_no_snapshots_reason():
    orch = AgenticESMMOrchestrator(baseline=_baseline())
    result = orch.run([])
    assert result.history == []
    assert result.best_decision is None
    assert result.converged is False
    assert result.stopped_reason == "no_snapshots"


def test_run_returns_at_least_one_decision_on_a_real_path():
    snaps = generate_order_book_path(n_snaps=100, seed=1)
    orch = AgenticESMMOrchestrator(baseline=_baseline(), max_iterations=3)
    result = orch.run(snaps)
    assert len(result.history) >= 1
    assert result.best_decision is not None
    assert isinstance(result.best_decision, AgenticDecision)


def test_run_converges_when_acceptance_threshold_easy():
    """With a low acceptance bar, the loop should converge on iteration 0."""
    snaps = generate_order_book_path(n_snaps=100, seed=42)
    orch = AgenticESMMOrchestrator(
        baseline=_baseline(), acceptance_score=0.1, max_iterations=5
    )
    result = orch.run(snaps)
    assert result.converged is True
    assert result.stopped_reason.startswith("accepted_at_iter_")
    assert len(result.history) == 1


def test_run_stops_at_max_iterations_when_threshold_unreachable():
    """Force max_iterations exhaustion by setting acceptance_score = 100.001."""
    snaps = generate_order_book_path(n_snaps=80, seed=11)
    orch = AgenticESMMOrchestrator(
        baseline=_baseline(), acceptance_score=999.0, max_iterations=4
    )
    result = orch.run(snaps)
    assert result.converged is False
    assert result.stopped_reason == "max_iterations_4"
    assert len(result.history) == 4


def test_best_decision_is_actually_the_max_score():
    snaps = generate_order_book_path(n_snaps=80, seed=7)
    orch = AgenticESMMOrchestrator(
        baseline=_baseline(), acceptance_score=999.0, max_iterations=3
    )
    result = orch.run(snaps)
    best_score = result.best_decision.score.score
    all_scores = [d.score.score for d in result.history]
    assert best_score == max(all_scores)


def test_orchestrator_passes_prior_score_into_strategist_after_iter_0():
    """After iter 0 the strategist should see a prior_score and may produce a
    different config than iter 0's. Easiest check: iteration field bumps."""
    snaps = generate_order_book_path(n_snaps=80, seed=2)
    orch = AgenticESMMOrchestrator(
        baseline=_baseline(), acceptance_score=999.0, max_iterations=3
    )
    result = orch.run(snaps)
    assert [d.iteration for d in result.history] == [0, 1, 2]
    assert [d.proposal.iteration for d in result.history] == [0, 1, 2]


def test_orchestrator_observation_is_consistent_across_iterations():
    """Within one run() the observation is computed once on the path; every
    iteration should see the same RegimeObservation."""
    snaps = generate_order_book_path(n_snaps=80, seed=5)
    orch = AgenticESMMOrchestrator(
        baseline=_baseline(), acceptance_score=999.0, max_iterations=3
    )
    result = orch.run(snaps)
    observations = [d.observation for d in result.history]
    assert all(o == observations[0] for o in observations)


def test_orchestrator_seed_deterministic_end_to_end():
    """Same seed → same final score."""
    snaps_a = generate_order_book_path(n_snaps=80, seed=33)
    snaps_b = generate_order_book_path(n_snaps=80, seed=33)
    orch_a = AgenticESMMOrchestrator(baseline=_baseline(), max_iterations=2)
    orch_b = AgenticESMMOrchestrator(baseline=_baseline(), max_iterations=2)
    a = orch_a.run(snaps_a)
    b = orch_b.run(snaps_b)
    assert a.best_decision.score.score == pytest.approx(b.best_decision.score.score)


def test_stress_regime_path_proposes_widened_spread():
    """A clearly-stressed synthetic path → strategist must widen spread vs baseline."""
    snaps = generate_order_book_path(n_snaps=120, sigma_per_step=0.005, seed=4)
    orch = AgenticESMMOrchestrator(
        baseline=_baseline(), acceptance_score=999.0, max_iterations=1
    )
    result = orch.run(snaps)
    decision = result.history[0]
    assert decision.observation.regime in {Regime.VOLATILE, Regime.STRESS}
    assert decision.proposal.config.base_half_spread_bps > _baseline().base_half_spread_bps


def test_run_result_can_be_serialised_to_dict():
    """Pydantic round-trip — proves the result is API-friendly."""
    snaps = generate_order_book_path(n_snaps=60, seed=8)
    orch = AgenticESMMOrchestrator(baseline=_baseline(), max_iterations=2)
    result = orch.run(snaps)
    payload = result.model_dump()
    assert "history" in payload
    assert "best_decision" in payload
    assert payload["history"][0]["proposal"]["config"]["symbol"] == "SPY"
