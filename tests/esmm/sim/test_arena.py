"""Tests for the arena bake-off harness.

Uses mock strategies + mock flow participants (no dependency on the
final NoiseTrader / InformedTrader implementations being agent-built).

Covers:
  * single-strategy run produces a summary
  * multi-strategy run produces ordered summaries
  * comparison block highlights best/worst P&L
  * deterministic: same seed + factories → same arena result
  * factories are called fresh per strategy (no shared participant state)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pytest

from src.esmm.schemas import Fill, OrderBookSnapshot
from src.esmm.sim.arena import Arena, ArenaConfig
from src.esmm.sim.kernel import KernelConfig
from src.esmm.sim.lob import Order, OrderSide, OrderType


# ---------------------------------------------------------------------------
# Mock participants
# ---------------------------------------------------------------------------
@dataclass
class FixedBuyer:
    """Strategy that submits one MARKET BUY of fixed size at ts >= ts0."""

    participant_id: str
    size: float
    ts0: float = 0.005
    fired: bool = False
    fills_received: list[Fill] = field(default_factory=list)

    def on_book(self, snap: OrderBookSnapshot) -> None:
        pass

    def on_fill(self, fill: Fill) -> None:
        self.fills_received.append(fill)

    def decide(self, now: float) -> list[Order]:
        if self.fired or now < self.ts0:
            return []
        self.fired = True
        return [
            Order(
                order_id=0,
                symbol="SPY",
                side=OrderSide.BUY,
                price=math.nan,
                size=self.size,
                ts=now,
                owner_id=self.participant_id,
                order_type=OrderType.MARKET,
            )
        ]


@dataclass
class FlowGenerator:
    """Tiny scripted ambient flow. Fires a SELL midway through."""

    participant_id: str = "ambient_sell"
    ts0: float = 0.02
    size: float = 50
    fired: bool = False

    def on_book(self, snap: OrderBookSnapshot) -> None:
        pass

    def on_fill(self, fill: Fill) -> None:
        pass

    def decide(self, now: float) -> list[Order]:
        if self.fired or now < self.ts0:
            return []
        self.fired = True
        return [
            Order(
                order_id=0,
                symbol="SPY",
                side=OrderSide.SELL,
                price=math.nan,
                size=self.size,
                ts=now,
                owner_id=self.participant_id,
                order_type=OrderType.MARKET,
            )
        ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestArenaSetup:
    def test_requires_at_least_one_strategy(self) -> None:
        with pytest.raises(ValueError):
            Arena(
                config=ArenaConfig(
                    kernel_config=KernelConfig(duration_sec=0.1, enable_latency=False)
                ),
                strategies={},
            )


class TestSingleStrategy:
    def test_one_strategy_one_summary(self) -> None:
        arena = Arena(
            config=ArenaConfig(
                kernel_config=KernelConfig(
                    duration_sec=0.05,
                    tick_interval_sec=0.001,
                    enable_latency=False,
                ),
                seed=1,
            ),
            strategies={
                "buyer100": lambda cfg: FixedBuyer("buyer100", size=100),
            },
        )
        r = arena.run()
        assert len(r.per_strategy) == 1
        s = r.per_strategy[0]
        assert s.strategy_id == "buyer100"
        assert s.final_inventory == 100.0
        assert "best_pnl" in r.comparison

    def test_with_flow_factory(self) -> None:
        arena = Arena(
            config=ArenaConfig(
                kernel_config=KernelConfig(
                    duration_sec=0.05,
                    tick_interval_sec=0.001,
                    enable_latency=False,
                ),
                seed=1,
                flow_factory=lambda cfg: [FlowGenerator()],
            ),
            strategies={
                "buyer50": lambda cfg: FixedBuyer("buyer50", size=50),
            },
        )
        r = arena.run()
        assert r.per_strategy[0].final_inventory == 50.0


class TestMultiStrategy:
    def test_two_strategies(self) -> None:
        arena = Arena(
            config=ArenaConfig(
                kernel_config=KernelConfig(
                    duration_sec=0.05,
                    tick_interval_sec=0.001,
                    enable_latency=False,
                ),
                seed=2,
            ),
            strategies={
                "small": lambda cfg: FixedBuyer("small", size=50),
                "big": lambda cfg: FixedBuyer("big", size=300),
            },
        )
        r = arena.run()
        assert {s.strategy_id for s in r.per_strategy} == {"small", "big"}
        # Bigger buyer holds more inventory.
        inv = {s.strategy_id: s.final_inventory for s in r.per_strategy}
        assert inv["big"] == 300.0
        assert inv["small"] == 50.0

    def test_comparison_block(self) -> None:
        # Two strategies of differing size produce different P&L. Big
        # buyer is long more → more sensitive to mid direction.
        arena = Arena(
            config=ArenaConfig(
                kernel_config=KernelConfig(
                    duration_sec=0.05,
                    tick_interval_sec=0.001,
                    enable_latency=False,
                ),
                seed=3,
            ),
            strategies={
                "small": lambda cfg: FixedBuyer("small", size=50),
                "big": lambda cfg: FixedBuyer("big", size=300),
            },
        )
        r = arena.run()
        c = r.comparison
        assert "best_pnl" in c and "worst_pnl" in c
        assert "pnl_mean" in c and "pnl_stdev" in c
        assert c["pnl_range"] >= 0


class TestDeterminism:
    def test_same_seed_same_arena_result(self) -> None:
        def build():
            return Arena(
                config=ArenaConfig(
                    kernel_config=KernelConfig(
                        duration_sec=0.05,
                        tick_interval_sec=0.001,
                        enable_latency=True,
                    ),
                    seed=99,
                ),
                strategies={
                    "alpha": lambda cfg: FixedBuyer("alpha", size=200),
                    "beta": lambda cfg: FixedBuyer("beta", size=400),
                },
            )

        r1 = build().run()
        r2 = build().run()
        pnl1 = {s.strategy_id: s.pnl for s in r1.per_strategy}
        pnl2 = {s.strategy_id: s.pnl for s in r2.per_strategy}
        assert pnl1 == pnl2

    def test_factories_called_fresh_per_strategy(self) -> None:
        call_counts = {"a": 0, "b": 0}

        def make_a(cfg):
            call_counts["a"] += 1
            return FixedBuyer("a", size=100)

        def make_b(cfg):
            call_counts["b"] += 1
            return FixedBuyer("b", size=100)

        arena = Arena(
            config=ArenaConfig(
                kernel_config=KernelConfig(
                    duration_sec=0.05,
                    tick_interval_sec=0.001,
                    enable_latency=False,
                ),
                seed=1,
            ),
            strategies={"a": make_a, "b": make_b},
        )
        arena.run()
        assert call_counts == {"a": 1, "b": 1}


class TestSerializable:
    def test_to_dict(self) -> None:
        arena = Arena(
            config=ArenaConfig(
                kernel_config=KernelConfig(
                    duration_sec=0.05,
                    tick_interval_sec=0.001,
                    enable_latency=False,
                ),
                seed=4,
            ),
            strategies={"x": lambda cfg: FixedBuyer("x", size=100)},
        )
        d = arena.run().to_dict()
        assert "run_id" in d
        assert "strategies" in d
        assert "per_strategy" in d
        assert "comparison" in d
        assert isinstance(d["per_strategy"][0], dict)
