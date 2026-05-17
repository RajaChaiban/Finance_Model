"""Tests for the Phase-5 walk-forward + Monte Carlo reporters.

Use mock participants — no dependency on agent-built archetypes.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

import pytest

from src.esmm.schemas import Fill, OrderBookSnapshot
from src.esmm.sim.kernel import KernelConfig
from src.esmm.sim.lob import Order, OrderSide, OrderType
from src.esmm.sim.reporters.monte_carlo import (
    MonteCarloConfig,
    run_monte_carlo,
    _bootstrap_ci_mean,
    _percentile,
)
from src.esmm.sim.reporters.walk_forward import (
    WalkForwardConfig,
    run_walk_forward,
)


# ---------------------------------------------------------------------------
# Mock strategy that scales its size by seed → variation across runs
# ---------------------------------------------------------------------------
@dataclass
class SeedSensitiveBuyer:
    participant_id: str
    seed_hash: int  # used to pick a size deterministically
    fired: bool = False

    def on_book(self, snap: OrderBookSnapshot) -> None:
        pass

    def on_fill(self, fill: Fill) -> None:
        pass

    def decide(self, now: float):
        if self.fired or now < 0.005:
            return []
        self.fired = True
        size = 50 + (self.seed_hash % 7) * 25
        return [
            Order(
                order_id=0,
                symbol="SPY",
                side=OrderSide.BUY,
                price=math.nan,
                size=size,
                ts=now,
                owner_id=self.participant_id,
                order_type=OrderType.MARKET,
            )
        ]


@dataclass
class IdleBuyer:
    participant_id: str = "idle"

    def on_book(self, snap: OrderBookSnapshot) -> None:
        pass

    def on_fill(self, fill: Fill) -> None:
        pass

    def decide(self, now: float):
        return []


# ---------------------------------------------------------------------------
# Walk-forward
# ---------------------------------------------------------------------------
class TestWalkForwardConfig:
    def test_defaults(self) -> None:
        c = WalkForwardConfig()
        assert c.train_sec > 0 and c.test_sec > 0
        assert c.n_windows > 0

    @pytest.mark.parametrize("field", ["train_sec", "test_sec", "step_sec"])
    def test_non_positive_window_rejected(self, field) -> None:
        with pytest.raises(ValueError):
            WalkForwardConfig(**{field: 0})

    def test_n_windows_positive(self) -> None:
        with pytest.raises(ValueError):
            WalkForwardConfig(n_windows=0)


class TestWalkForwardRun:
    def test_basic_n_windows(self) -> None:
        cfg = WalkForwardConfig(
            train_sec=1.0, test_sec=0.02, step_sec=0.02, n_windows=3, seed=1
        )
        report = run_walk_forward(
            config=cfg,
            kernel_config_factory=lambda d: KernelConfig(
                duration_sec=d,
                tick_interval_sec=0.001,
                snapshot_interval_sec=0.01,
                enable_latency=False,
            ),
            strategy_factory=lambda cfg: IdleBuyer(),
        )
        assert report.n_windows == 3
        assert len(report.windows) == 3
        # IdleBuyer produces no orders → no fills, P&L should be 0.
        assert all(w.pnl == 0.0 for w in report.windows)
        assert report.pnl_stdev == 0.0
        assert report.hit_rate == 0.0  # no wins

    def test_strategy_factory_called_per_window(self) -> None:
        call_count = {"n": 0}

        def factory(cfg):
            call_count["n"] += 1
            return IdleBuyer(participant_id=f"idle_{call_count['n']}")

        cfg = WalkForwardConfig(
            train_sec=1.0, test_sec=0.02, step_sec=0.02, n_windows=4, seed=2
        )
        run_walk_forward(
            config=cfg,
            kernel_config_factory=lambda d: KernelConfig(
                duration_sec=d, tick_interval_sec=0.001, enable_latency=False
            ),
            strategy_factory=factory,
        )
        assert call_count["n"] == 4

    def test_report_to_dict(self) -> None:
        cfg = WalkForwardConfig(test_sec=0.02, step_sec=0.02, n_windows=2, seed=3)
        report = run_walk_forward(
            config=cfg,
            kernel_config_factory=lambda d: KernelConfig(
                duration_sec=d, tick_interval_sec=0.001, enable_latency=False
            ),
            strategy_factory=lambda cfg: IdleBuyer(),
        )
        d = report.to_dict()
        assert "n_windows" in d and "pnl_sharpe" in d and "hit_rate" in d


# ---------------------------------------------------------------------------
# Monte Carlo
# ---------------------------------------------------------------------------
class TestMonteCarloConfig:
    def test_defaults(self) -> None:
        c = MonteCarloConfig()
        assert c.n_runs > 0
        assert 0 < c.confidence < 1

    def test_n_runs_positive(self) -> None:
        with pytest.raises(ValueError):
            MonteCarloConfig(n_runs=0)

    def test_confidence_bounded(self) -> None:
        with pytest.raises(ValueError):
            MonteCarloConfig(confidence=0)
        with pytest.raises(ValueError):
            MonteCarloConfig(confidence=1)


class TestPercentile:
    def test_endpoints(self) -> None:
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert _percentile(vals, 0.0) == 1.0
        assert _percentile(vals, 1.0) == 5.0

    def test_median(self) -> None:
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert _percentile(vals, 0.5) == 3.0

    def test_empty(self) -> None:
        assert _percentile([], 0.5) == 0.0


class TestBootstrapCI:
    def test_constant_values_zero_width(self) -> None:
        lo, hi = _bootstrap_ci_mean([10.0] * 50, 0.95)
        assert math.isclose(lo, 10.0)
        assert math.isclose(hi, 10.0)

    def test_single_value(self) -> None:
        lo, hi = _bootstrap_ci_mean([7.0], 0.95)
        assert lo == 7.0 and hi == 7.0

    def test_centred_around_true_mean(self) -> None:
        # Symmetric data → CI brackets the mean.
        data = list(range(1, 101))  # mean = 50.5
        lo, hi = _bootstrap_ci_mean(data, 0.95)
        assert lo < 50.5 < hi


class TestMonteCarloRun:
    def test_idle_strategy_zero_pnl(self) -> None:
        cfg = MonteCarloConfig(n_runs=10, base_seed=0)
        kc = KernelConfig(
            duration_sec=0.02, tick_interval_sec=0.001, enable_latency=False
        )
        report = run_monte_carlo(
            config=cfg,
            kernel_config=kc,
            strategy_factory=lambda c: IdleBuyer(),
        )
        assert report.n_runs == 10
        assert report.pnl_mean == 0.0
        assert report.hit_rate == 0.0

    def test_variation_across_seeds(self) -> None:
        cfg = MonteCarloConfig(n_runs=20, base_seed=42)
        kc = KernelConfig(
            duration_sec=0.02,
            tick_interval_sec=0.001,
            enable_latency=True,  # latency stream varies by seed
        )
        report = run_monte_carlo(
            config=cfg,
            kernel_config=kc,
            strategy_factory=lambda c: SeedSensitiveBuyer(
                participant_id="strat", seed_hash=c.seed or 0
            ),
        )
        # 7 distinct sizes possible (size % 7 buckets) → multiple distinct PnLs
        unique_pnls = {round(p, 6) for p in [w for w in [report.pnl_mean]] if p}
        # At minimum we should see variation
        assert report.pnl_stdev > 0 or report.n_runs == 1

    def test_percentiles_ordered(self) -> None:
        cfg = MonteCarloConfig(n_runs=20, base_seed=7)
        kc = KernelConfig(
            duration_sec=0.02, tick_interval_sec=0.001, enable_latency=False
        )
        report = run_monte_carlo(
            config=cfg,
            kernel_config=kc,
            strategy_factory=lambda c: SeedSensitiveBuyer(
                participant_id="s", seed_hash=c.seed or 0
            ),
        )
        p = report.pnl_percentiles
        assert p["p5"] <= p["p25"] <= p["p50"] <= p["p75"] <= p["p95"]

    def test_report_to_dict(self) -> None:
        cfg = MonteCarloConfig(n_runs=5, base_seed=1)
        kc = KernelConfig(duration_sec=0.02, tick_interval_sec=0.001, enable_latency=False)
        report = run_monte_carlo(
            config=cfg,
            kernel_config=kc,
            strategy_factory=lambda c: IdleBuyer(),
        )
        d = report.to_dict()
        for key in (
            "n_runs",
            "pnl_mean",
            "pnl_stdev",
            "pnl_percentiles",
            "pnl_ci_low",
            "pnl_ci_high",
            "var_95",
            "tail_mean_5pct",
            "hit_rate",
        ):
            assert key in d
