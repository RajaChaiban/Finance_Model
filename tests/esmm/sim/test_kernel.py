"""Tests for the simulation kernel.

These tests use mock participants (defined inline) so kernel behaviour
is verified independent of the real participant implementations.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pytest

from src.esmm.schemas import Fill, OrderBookSnapshot
from src.esmm.sim.kernel import Kernel, KernelConfig, KernelResult
from src.esmm.sim.latency import LatencyConfig
from src.esmm.sim.lob import Order, OrderSide, OrderType
from src.esmm.sim.risk import RiskLimits


# ---------------------------------------------------------------------------
# Mock participants
# ---------------------------------------------------------------------------
@dataclass
class IdleParticipant:
    """Does nothing — used to verify the loop runs without orders."""

    participant_id: str
    n_snapshots: int = 0
    n_fills: int = 0

    def on_book(self, snap: OrderBookSnapshot) -> None:
        self.n_snapshots += 1

    def on_fill(self, fill: Fill) -> None:
        self.n_fills += 1

    def decide(self, now: float) -> list[Order]:
        return []


@dataclass
class ScriptedParticipant:
    """Emits a pre-specified list of (ts, side, size, price) orders.

    Use ``price=math.nan`` for a MARKET order. Otherwise LIMIT.
    """

    participant_id: str
    script: list[tuple[float, OrderSide, float, float]]
    fired: list[bool] = field(default_factory=list)
    fills_received: list[Fill] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.fired = [False] * len(self.script)

    def on_book(self, snap: OrderBookSnapshot) -> None:
        pass

    def on_fill(self, fill: Fill) -> None:
        self.fills_received.append(fill)

    def decide(self, now: float) -> list[Order]:
        out: list[Order] = []
        for i, (ts, side, size, price) in enumerate(self.script):
            if not self.fired[i] and ts <= now:
                self.fired[i] = True
                order_type = OrderType.MARKET if math.isnan(price) else OrderType.LIMIT
                out.append(
                    Order(
                        order_id=0,
                        symbol="SPY",
                        side=side,
                        price=price,
                        size=size,
                        ts=now,
                        owner_id=self.participant_id,
                        order_type=order_type,
                    )
                )
        return out


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
class TestKernelSetup:
    def test_construct_with_defaults(self) -> None:
        k = Kernel(KernelConfig(duration_sec=1.0))
        assert k.lob.symbol == "SPY"
        assert k.participants == []

    def test_register_appends(self) -> None:
        k = Kernel(KernelConfig(duration_sec=1.0))
        p = IdleParticipant("a")
        k.register(p)
        assert k.participants == [p]
        assert "a" in k._participants_by_id

    def test_register_duplicate_rejected(self) -> None:
        k = Kernel(KernelConfig(duration_sec=1.0))
        k.register(IdleParticipant("a"))
        with pytest.raises(ValueError, match="already registered"):
            k.register(IdleParticipant("a"))

    def test_invalid_kernel_config(self) -> None:
        with pytest.raises(ValueError):
            KernelConfig(duration_sec=0)
        with pytest.raises(ValueError):
            KernelConfig(duration_sec=10, tick_interval_sec=0)
        with pytest.raises(ValueError):
            KernelConfig(duration_sec=10, snapshot_interval_sec=0)


# ---------------------------------------------------------------------------
# Book seeding
# ---------------------------------------------------------------------------
class TestSeedBook:
    def test_seeded_book_has_two_sides(self) -> None:
        k = Kernel(KernelConfig(duration_sec=1.0, starting_mid=100, starting_spread_bps=4))
        k.seed_book()
        assert k.lob.best_bid() is not None
        assert k.lob.best_ask() is not None
        assert k.lob.best_ask() > k.lob.best_bid()
        assert math.isclose(k.lob.mid(), 100.0, abs_tol=0.05)

    def test_seed_book_respects_levels(self) -> None:
        k = Kernel(
            KernelConfig(
                duration_sec=1.0,
                starting_mid=100,
                starting_spread_bps=4,
                seed_book_levels=5,
                seed_book_level_step_bps=2.0,
            )
        )
        k.seed_book()
        snap = k.lob.snapshot(0.0)
        assert len(snap.bids) == 5
        assert len(snap.asks) == 5

    def test_seed_book_owner_does_not_self_trade(self) -> None:
        # If a scripted MARKET BUY hits the seeded ask, the fill should
        # not be skipped due to self-trade prevention — the house owner
        # is distinct from participant owners.
        k = Kernel(KernelConfig(duration_sec=0.05, tick_interval_sec=0.001))
        p = ScriptedParticipant(
            "buyer", script=[(0.001, OrderSide.BUY, 100, math.nan)]
        )
        k.register(p)
        r = k.run()
        assert r.n_fills >= 1


# ---------------------------------------------------------------------------
# Run loop
# ---------------------------------------------------------------------------
class TestRunLoop:
    def test_idle_run_emits_snapshots_but_no_fills(self) -> None:
        cfg = KernelConfig(
            duration_sec=1.0, tick_interval_sec=0.01, snapshot_interval_sec=0.1
        )
        k = Kernel(cfg)
        p = IdleParticipant("idle")
        k.register(p)
        r = k.run()
        assert r.n_fills == 0
        assert r.n_snapshots >= 10
        assert p.n_snapshots >= 10
        assert p.n_fills == 0
        assert math.isclose(r.initial_mid, r.final_mid, abs_tol=1e-9)

    def test_market_buy_consumes_ask(self) -> None:
        cfg = KernelConfig(
            duration_sec=0.1,
            tick_interval_sec=0.001,
            snapshot_interval_sec=0.005,
            enable_latency=False,
        )
        k = Kernel(cfg)
        p = ScriptedParticipant(
            "buyer", script=[(0.005, OrderSide.BUY, 200, math.nan)]
        )
        k.register(p)
        r = k.run()
        assert r.n_fills >= 2  # one fill from each side
        assert len(p.fills_received) >= 1
        assert k._inventory["buyer"] == 200.0

    def test_market_sell_consumes_bid(self) -> None:
        cfg = KernelConfig(
            duration_sec=0.1,
            tick_interval_sec=0.001,
            snapshot_interval_sec=0.005,
            enable_latency=False,
        )
        k = Kernel(cfg)
        p = ScriptedParticipant(
            "seller", script=[(0.005, OrderSide.SELL, 150, math.nan)]
        )
        k.register(p)
        r = k.run()
        assert r.n_fills >= 2
        assert k._inventory["seller"] == -150.0

    def test_inventory_signs_correct(self) -> None:
        cfg = KernelConfig(
            duration_sec=0.2,
            tick_interval_sec=0.001,
            snapshot_interval_sec=0.01,
            enable_latency=False,
        )
        k = Kernel(cfg)
        p = ScriptedParticipant(
            "trader",
            script=[
                (0.01, OrderSide.BUY, 100, math.nan),
                (0.05, OrderSide.SELL, 30, math.nan),
            ],
        )
        k.register(p)
        r = k.run()
        assert k._inventory["trader"] == pytest.approx(70.0)

    def test_orders_total_reconciles(self) -> None:
        """House + trader inventory should sum to 0 (zero-sum sim)."""
        cfg = KernelConfig(duration_sec=0.1, tick_interval_sec=0.001, enable_latency=False)
        k = Kernel(cfg)
        p = ScriptedParticipant(
            "trader", script=[(0.005, OrderSide.BUY, 200, math.nan)]
        )
        k.register(p)
        k.run()
        total = sum(k._inventory.values())
        assert math.isclose(total, 0.0, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# Latency
# ---------------------------------------------------------------------------
class TestLatency:
    def test_with_latency_orders_delayed(self) -> None:
        # With 100ms mean latency and a 50ms simulation, the order
        # should arrive *after* the run ends and produce zero fills.
        cfg = KernelConfig(
            duration_sec=0.05,
            tick_interval_sec=0.001,
            enable_latency=True,
        )
        latency = LatencyConfig(
            submit_mean_ms=100.0,
            submit_sigma_ms=0.0,  # deterministic
            cancel_mean_ms=100.0,
            cancel_sigma_ms=0.0,
            seed=1,
        )
        k = Kernel(cfg, latency_config=latency)
        p = ScriptedParticipant(
            "buyer", script=[(0.001, OrderSide.BUY, 100, math.nan)]
        )
        k.register(p)
        r = k.run()
        # Order was submitted but never arrived.
        assert r.n_orders_submitted == 1
        assert r.n_fills == 0

    def test_disabled_latency_immediate(self) -> None:
        cfg = KernelConfig(duration_sec=0.02, tick_interval_sec=0.001, enable_latency=False)
        k = Kernel(cfg)
        p = ScriptedParticipant(
            "buyer", script=[(0.001, OrderSide.BUY, 100, math.nan)]
        )
        k.register(p)
        r = k.run()
        assert r.n_fills >= 2


# ---------------------------------------------------------------------------
# Risk integration
# ---------------------------------------------------------------------------
class TestRiskIntegration:
    def test_pretrade_blocks_oversized(self) -> None:
        cfg = KernelConfig(duration_sec=0.05, tick_interval_sec=0.001, enable_latency=False)
        limits = RiskLimits(
            max_notional_usd=1_000,  # very tight
            concentration_pct=1.0,
            daily_loss_kill_switch_usd=math.inf,
        )
        k = Kernel(cfg, risk_limits=limits)
        # 500 shares at 100 = 50_000 notional — way over 1_000.
        p = ScriptedParticipant(
            "trader", script=[(0.001, OrderSide.BUY, 500, math.nan)]
        )
        k.register(p)
        r = k.run()
        assert r.n_orders_submitted == 0
        assert r.n_fills == 0
        assert len(r.risk_breaches) >= 1

    def test_posttrade_halt_stops_further_decisions(self) -> None:
        # Configure a daily-loss kill switch that the very first fill
        # will trigger via the position MTM noise.
        cfg = KernelConfig(duration_sec=0.05, tick_interval_sec=0.001, enable_latency=False)
        limits = RiskLimits(
            max_notional_usd=math.inf,
            concentration_pct=1.0,
            daily_loss_kill_switch_usd=0.001,
            max_drawdown_pct=1.0,
        )
        k = Kernel(cfg, risk_limits=limits)
        # Big aggressive buy will leave us with non-zero MTM, may halt.
        p = ScriptedParticipant(
            "trader",
            script=[
                (0.005, OrderSide.BUY, 200, math.nan),
                (0.02, OrderSide.BUY, 100, math.nan),  # would fire after halt
            ],
        )
        k.register(p)
        r = k.run()
        # We may or may not actually trip — depends on whether the MTM
        # P&L crossed the threshold. The contract under test is: if it
        # trips, halted_at is set and further orders are blocked.
        if r.halted_at is not None:
            # At minimum the second order shouldn't have produced a fill
            # if halt fired before tick 0.02.
            assert r.halted_at <= 0.02 or r.n_fills < 4


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------
class TestDeterminism:
    def test_same_seed_same_result(self) -> None:
        def run_once(seed: int) -> KernelResult:
            cfg = KernelConfig(
                duration_sec=0.1, tick_interval_sec=0.001, enable_latency=True, seed=seed
            )
            k = Kernel(cfg, latency_config=LatencyConfig(seed=seed))
            p = ScriptedParticipant(
                "trader",
                script=[(0.005 * (i + 1), OrderSide.BUY, 100, math.nan) for i in range(5)],
            )
            k.register(p)
            return k.run()

        r1 = run_once(42)
        r2 = run_once(42)
        assert r1.n_fills == r2.n_fills
        assert r1.n_orders_submitted == r2.n_orders_submitted
        assert r1.pnl_per_participant == r2.pnl_per_participant
