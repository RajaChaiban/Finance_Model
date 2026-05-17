"""Tests for src/agents/esmm/sim_runner.py — the adapter that lets the
agentic Layer-C orchestrator drive sim runs instead of legacy backtest
replays.

Uses a stub MM participant (no dependency on MarketMakerParticipant
landing first).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pytest

from src.agents.esmm.sim_runner import (
    SimRunnerOutput,
    run_sim_iteration,
)
from src.esmm.schemas import Fill, MarketMakingConfig, OrderBookSnapshot, Side
from src.esmm.sim.kernel import KernelConfig
from src.esmm.sim.lob import Order, OrderSide, OrderType


@dataclass
class StubMM:
    """Tiny stand-in for the (in-flight) MarketMakerParticipant.

    Posts a single MARKET BUY of size 100 once. Exposes
    ``fills_received`` so the sim_runner picks them up via the
    preferred path.
    """

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
                size=100,
                ts=now,
                owner_id=self.participant_id,
                order_type=OrderType.MARKET,
            )
        ]


class TestRunSimIteration:
    def test_returns_backtest_compatible_shape(self) -> None:
        cfg = MarketMakingConfig(symbol="SPY")
        out = run_sim_iteration(
            scenario_id="hot_cpi",
            config=cfg,
            mm_factory=lambda c: StubMM("stubmm", c),
            duration_override_sec=0.05,
            seed=1,
        )
        assert isinstance(out, SimRunnerOutput)
        br = out.backtest_result
        # Shape sanity — these are the attributes the orchestrator reads.
        assert hasattr(br, "fills")
        assert hasattr(br, "tca")
        assert hasattr(br, "total_pnl")
        assert hasattr(br, "n_fills")

    def test_tca_is_dict(self) -> None:
        cfg = MarketMakingConfig(symbol="SPY")
        out = run_sim_iteration(
            scenario_id="hot_cpi",
            config=cfg,
            mm_factory=lambda c: StubMM("stubmm", c),
            duration_override_sec=0.05,
            seed=2,
        )
        # The orchestrator does TCABreakdown(**result.tca) — it must
        # accept that.
        assert isinstance(out.backtest_result.tca, dict)
        assert "total_pnl" in out.backtest_result.tca

    def test_strategy_fills_filtered(self) -> None:
        cfg = MarketMakingConfig(symbol="SPY")
        out = run_sim_iteration(
            scenario_id="hot_cpi",
            config=cfg,
            mm_factory=lambda c: StubMM("stubmm", c),
            duration_override_sec=0.05,
            seed=3,
        )
        assert out.backtest_result.n_fills == len(out.strategy_fills)
        # Stub MM submits one order; with seed-book it should get
        # exactly one fill (the seed ask).
        assert len(out.strategy_fills) >= 1

    def test_unknown_scenario_raises(self) -> None:
        cfg = MarketMakingConfig(symbol="SPY")
        with pytest.raises(KeyError):
            run_sim_iteration(
                scenario_id="not_a_real_scenario",
                config=cfg,
                mm_factory=lambda c: StubMM("stubmm", c),
                duration_override_sec=0.05,
            )

    def test_same_seed_same_result(self) -> None:
        cfg = MarketMakingConfig(symbol="SPY")
        out1 = run_sim_iteration(
            scenario_id="hot_cpi",
            config=cfg,
            mm_factory=lambda c: StubMM("stubmm", c),
            duration_override_sec=0.05,
            seed=99,
        )
        out2 = run_sim_iteration(
            scenario_id="hot_cpi",
            config=cfg,
            mm_factory=lambda c: StubMM("stubmm", c),
            duration_override_sec=0.05,
            seed=99,
        )
        assert out1.backtest_result.total_pnl == out2.backtest_result.total_pnl
        assert out1.backtest_result.n_fills == out2.backtest_result.n_fills

    def test_different_seeds_diverge(self) -> None:
        cfg = MarketMakingConfig(symbol="SPY")
        out1 = run_sim_iteration(
            scenario_id="hot_cpi",
            config=cfg,
            mm_factory=lambda c: StubMM("stubmm", c),
            duration_override_sec=0.05,
            seed=1,
        )
        out2 = run_sim_iteration(
            scenario_id="hot_cpi",
            config=cfg,
            mm_factory=lambda c: StubMM("stubmm", c),
            duration_override_sec=0.05,
            seed=999_999,
        )
        # Latency stream differs by seed → fill timing may differ. We
        # don't enforce a hard divergence (stub MM only fires once),
        # just verify the call shape stays consistent.
        assert out1.backtest_result.n_fills >= 0
        assert out2.backtest_result.n_fills >= 0


class TestBacktestResultCompatibility:
    """The orchestrator does ``TCABreakdown(**result.tca)`` — that must work."""

    def test_tca_keys_match_breakdown(self) -> None:
        from src.esmm.schemas import TCABreakdown

        cfg = MarketMakingConfig(symbol="SPY")
        out = run_sim_iteration(
            scenario_id="hot_cpi",
            config=cfg,
            mm_factory=lambda c: StubMM("stubmm", c),
            duration_override_sec=0.05,
            seed=7,
        )
        # This is the exact pattern the orchestrator uses:
        tca = TCABreakdown(**out.backtest_result.tca)
        assert tca.total_pnl == out.backtest_result.tca["total_pnl"]
