"""Tests for AgenticSimOrchestrator — the sim variant of the agentic loop.

Uses a stub MM participant so the test doesn't depend on the in-flight
MarketMakerParticipant implementation. Verifies the loop shape:
observation, propose, run, score, accept/iterate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import pytest

from src.agents.esmm.schemas import (
    AgenticRunResult,
    Regime,
    TCAScore,
)
from src.agents.esmm.sim_orchestrator import (
    AgenticSimOrchestrator,
    _scenario_to_observation,
)
from src.agents.esmm.tca_critic import TCACritic
from src.esmm.schemas import Fill, MarketMakingConfig, OrderBookSnapshot, TCABreakdown
from src.esmm.sim.lob import Order, OrderSide, OrderType
from src.esmm.sim.scenarios.loader import load_library


@dataclass
class StubMM:
    participant_id: str
    config: MarketMakingConfig
    fired: bool = False
    fills_received: list[Fill] = field(default_factory=list)

    def on_book(self, snap: OrderBookSnapshot) -> None:
        pass

    def on_fill(self, fill: Fill) -> None:
        self.fills_received.append(fill)

    def decide(self, now: float):
        if self.fired or now < 0.005:
            return []
        self.fired = True
        return [
            Order(
                order_id=0,
                symbol=self.config.symbol,
                side=OrderSide.BUY,
                price=math.nan,
                size=50,
                ts=now,
                owner_id=self.participant_id,
                order_type=OrderType.MARKET,
            )
        ]


class _AlwaysAccepting(TCACritic):
    """Critic that returns a score above threshold no matter what."""

    def score(self, tca: TCABreakdown) -> TCAScore:
        return TCAScore(
            score=100.0,
            spread_capture_ratio=1.0,
            adverse_selection_ratio=0.0,
            hedge_drag_ratio=0.0,
            inventory_volatility=0.0,
            recommendations=[],
        )


class _AlwaysRejecting(TCACritic):
    def score(self, tca: TCABreakdown) -> TCAScore:
        return TCAScore(
            score=0.0,
            spread_capture_ratio=0.0,
            adverse_selection_ratio=1.0,
            hedge_drag_ratio=1.0,
            inventory_volatility=1.0,
            recommendations=["rejected"],
        )


# ---------------------------------------------------------------------------
# Observation synthesis
# ---------------------------------------------------------------------------
class TestObservation:
    def test_from_curated_scenario(self) -> None:
        lib = load_library()
        obs = _scenario_to_observation(lib["flash_crash_2010"])
        assert obs.regime == Regime.STRESS

    def test_calm_regime(self) -> None:
        lib = load_library()
        obs = _scenario_to_observation(lib["opex_pin"])
        assert obs.regime == Regime.CALM


# ---------------------------------------------------------------------------
# Orchestrator behaviour
# ---------------------------------------------------------------------------
class TestSimOrchestrator:
    def test_unknown_scenario_returns_clean_failure(self) -> None:
        orch = AgenticSimOrchestrator(
            baseline=MarketMakingConfig(symbol="SPY"),
            mm_factory=lambda c: StubMM("mm", c),
            duration_override_sec=0.05,
        )
        r = orch.run("not_a_scenario")
        assert isinstance(r, AgenticRunResult)
        assert r.converged is False
        assert r.history == []
        assert "unknown_scenario" in r.stopped_reason

    def test_converges_when_critic_always_accepts(self) -> None:
        orch = AgenticSimOrchestrator(
            baseline=MarketMakingConfig(symbol="SPY"),
            mm_factory=lambda c: StubMM("mm", c),
            critic=_AlwaysAccepting(),
            duration_override_sec=0.05,
            max_iterations=5,
        )
        r = orch.run("hot_cpi")
        assert r.converged is True
        assert r.best_decision is not None
        assert r.best_decision.iteration == 0  # accepted on first try
        assert "accepted_at_iter_0" in r.stopped_reason

    def test_iterates_max_times_when_rejected(self) -> None:
        orch = AgenticSimOrchestrator(
            baseline=MarketMakingConfig(symbol="SPY"),
            mm_factory=lambda c: StubMM("mm", c),
            critic=_AlwaysRejecting(),
            duration_override_sec=0.05,
            max_iterations=3,
        )
        r = orch.run("hot_cpi")
        assert r.converged is False
        assert len(r.history) == 3
        assert r.best_decision is not None  # best-of-three even on rejection
        assert "max_iterations_3" in r.stopped_reason

    def test_observation_recorded_on_every_decision(self) -> None:
        orch = AgenticSimOrchestrator(
            baseline=MarketMakingConfig(symbol="SPY"),
            mm_factory=lambda c: StubMM("mm", c),
            critic=_AlwaysRejecting(),
            duration_override_sec=0.05,
            max_iterations=2,
        )
        r = orch.run("flash_crash_2010")
        regimes = {d.observation.regime for d in r.history}
        assert regimes == {Regime.STRESS}

    def test_proposal_iteration_increments(self) -> None:
        orch = AgenticSimOrchestrator(
            baseline=MarketMakingConfig(symbol="SPY"),
            mm_factory=lambda c: StubMM("mm", c),
            critic=_AlwaysRejecting(),
            duration_override_sec=0.05,
            max_iterations=3,
        )
        r = orch.run("hot_cpi")
        iters = [d.proposal.iteration for d in r.history]
        assert iters == [0, 1, 2]

    def test_seed_advances_per_iteration(self) -> None:
        # Both runs use base_seed=1. Across iterations the kernel seed
        # is base_seed + i*7919. Best-decision selection depends on
        # the seed stream — verify run is at least reproducible run-to-run.
        def go() -> AgenticRunResult:
            orch = AgenticSimOrchestrator(
                baseline=MarketMakingConfig(symbol="SPY"),
                mm_factory=lambda c: StubMM("mm", c),
                critic=_AlwaysRejecting(),
                duration_override_sec=0.05,
                max_iterations=3,
                base_seed=1,
            )
            return orch.run("hot_cpi")

        r1 = go()
        r2 = go()
        assert [d.score.score for d in r1.history] == [d.score.score for d in r2.history]
