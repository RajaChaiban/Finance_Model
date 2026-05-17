"""End-to-end smoke test: AgenticSimOrchestrator + real MarketMakerParticipant.

This is the Phase-4 acceptance gate. If this test passes, the marquee
wiring works: the agentic Layer-C loop can iterate on
``MarketMakingConfig`` proposals while running against curated stress
scenarios with a real LOB, latency, and adversarial flow underneath.
"""

from __future__ import annotations

import math

import pytest

from src.agents.esmm.sim_orchestrator import AgenticSimOrchestrator
from src.esmm.schemas import MarketMakingConfig
from src.esmm.sim.participants.market_maker import MarketMakerParticipant
from src.esmm.sim.participants.noise import NoiseTrader


class TestAgenticE2E:
    def test_loop_runs_against_hot_cpi(self) -> None:
        baseline = MarketMakingConfig(symbol="SPY", base_half_spread_bps=5.0, quote_size=100)

        def mm_factory(cfg: MarketMakingConfig):
            return MarketMakerParticipant(
                participant_id="mm",
                config=cfg,
                requote_interval_sec=0.02,
                use_hedger=False,  # keep test deterministic + simple
            )

        def flow_factory(kc, sc):
            return [
                NoiseTrader(
                    participant_id="noise",
                    symbol=kc.symbol,
                    arrival_rate_hz=5.0,
                    lot_min=50,
                    lot_max=150,
                    aggressive_pct=0.7,
                    seed=1,
                )
            ]

        orch = AgenticSimOrchestrator(
            baseline=baseline,
            mm_factory=mm_factory,
            flow_factory=flow_factory,
            max_iterations=2,
            base_seed=42,
            duration_override_sec=0.1,
        )
        result = orch.run("hot_cpi")
        # We don't assert convergence — depends on scoring threshold +
        # flow path. The contract is: loop runs to completion, history
        # contains the right shape.
        assert len(result.history) >= 1
        for d in result.history:
            assert d.tca is not None
            assert isinstance(d.tca.total_pnl, float)
            assert d.score is not None
            assert 0.0 <= d.score.score <= 100.0

    def test_loop_against_flash_crash(self) -> None:
        baseline = MarketMakingConfig(symbol="SPY", base_half_spread_bps=10.0, quote_size=100)

        def mm_factory(cfg):
            return MarketMakerParticipant(
                participant_id="mm",
                config=cfg,
                requote_interval_sec=0.02,
                use_hedger=False,
            )

        def flow_factory(kc, sc):
            return [
                NoiseTrader(
                    participant_id="noise",
                    symbol=kc.symbol,
                    arrival_rate_hz=3.0,
                    seed=2,
                )
            ]

        orch = AgenticSimOrchestrator(
            baseline=baseline,
            mm_factory=mm_factory,
            flow_factory=flow_factory,
            max_iterations=1,
            base_seed=42,
            duration_override_sec=0.1,
        )
        result = orch.run("flash_crash_2010")
        assert len(result.history) == 1
        # STRESS regime should propagate into the observation.
        from src.agents.esmm.schemas import Regime
        assert result.history[0].observation.regime == Regime.STRESS
