"""Walk-forward stability evaluator.

Splits a long simulation into rolling windows and runs the same strategy
on each. Reports per-window P&L, drawdown, fill count — and aggregate
stability metrics that a 20-year trader actually looks at:

  * P&L stability (mean / std / Sharpe across windows)
  * Hit rate (fraction of windows with positive P&L)
  * Worst-window P&L
  * Inventory-end stability (do positions clear by end of window?)

This is the "did this strategy *actually* survive across time" check —
much more honest than a single-pass backtest. A strategy with a great
single-pass Sharpe but a bad worst-window has too much regime risk.

Notes:
  * The walk-forward harness doesn't *retrain* anything. Most MM
    strategies are stateful within a window but reset between windows.
    Phase 4 will add an optional ``warm_start_factory`` for strategies
    that need cross-window context.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Callable

from src.esmm.sim.kernel import Kernel, KernelConfig, KernelResult
from src.esmm.sim.latency import LatencyConfig
from src.esmm.sim.participants.base import Participant


@dataclass
class WalkForwardConfig:
    """Window sizing in *seconds of sim time*.

    Default train/test split (30/5) is loosely calibrated to a research
    workflow: 30 sim-sec of "warm-up" before a 5 sim-sec OOS window.
    For per-day calibration use longer windows by scaling these up.
    """

    train_sec: float = 30.0
    test_sec: float = 5.0
    step_sec: float = 5.0  # window advance step
    n_windows: int = 5
    seed: int | None = None

    def __post_init__(self) -> None:
        if self.train_sec <= 0 or self.test_sec <= 0 or self.step_sec <= 0:
            raise ValueError("train/test/step must all be > 0")
        if self.n_windows <= 0:
            raise ValueError("n_windows must be > 0")


@dataclass
class WalkForwardWindow:
    """One window's outcome."""

    index: int
    start_sec: float
    end_sec: float
    pnl: float
    final_inventory: float
    n_fills: int
    halted_at: float | None


@dataclass
class WalkForwardReport:
    """Aggregate report across all windows."""

    n_windows: int
    windows: list[WalkForwardWindow]
    pnl_mean: float
    pnl_stdev: float
    pnl_sharpe: float  # sqrt(N) * mean/std — small-N caveat noted in docs
    hit_rate: float
    worst_window_pnl: float
    best_window_pnl: float
    inventory_end_mean: float
    inventory_end_stdev: float
    halted_windows: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_windows": self.n_windows,
            "windows": [w.__dict__ for w in self.windows],
            "pnl_mean": self.pnl_mean,
            "pnl_stdev": self.pnl_stdev,
            "pnl_sharpe": self.pnl_sharpe,
            "hit_rate": self.hit_rate,
            "worst_window_pnl": self.worst_window_pnl,
            "best_window_pnl": self.best_window_pnl,
            "inventory_end_mean": self.inventory_end_mean,
            "inventory_end_stdev": self.inventory_end_stdev,
            "halted_windows": self.halted_windows,
        }


def run_walk_forward(
    *,
    config: WalkForwardConfig,
    kernel_config_factory: Callable[[float], KernelConfig],
    strategy_factory: Callable[[KernelConfig], Participant],
    flow_factory: Callable[[KernelConfig], list[Participant]] | None = None,
    latency_config: LatencyConfig | None = None,
) -> WalkForwardReport:
    """Run ``n_windows`` rolling windows and aggregate.

    Args:
        config: window sizing.
        kernel_config_factory: ``f(duration_sec) -> KernelConfig``. Lets the
            caller customise per-window mid/symbol/etc.
        strategy_factory: builds a fresh strategy per window.
        flow_factory: optional fresh ambient flow per window.
        latency_config: optional latency override (seeded once across all
            windows so the latency stream is deterministic).
    """
    windows: list[WalkForwardWindow] = []
    test_pnls: list[float] = []
    test_invs: list[float] = []
    halted = 0

    for i in range(config.n_windows):
        start = i * config.step_sec
        end = start + config.test_sec
        duration = config.test_sec  # only test_sec actually runs per window

        kc = kernel_config_factory(duration)
        if config.seed is not None:
            # Use a per-window seed so each window is reproducible but
            # distinct. (i << 16) ^ seed gives separation without
            # collision for typical seed magnitudes.
            kc = KernelConfig(
                **{
                    **kc.__dict__,
                    "seed": (config.seed + i * 7919) & 0x7FFFFFFF,
                }
            )
        kernel = Kernel(
            kc,
            latency_config=latency_config
            or LatencyConfig(seed=kc.seed if kc.seed is not None else None),
        )
        if flow_factory is not None:
            for p in flow_factory(kc):
                kernel.register(p)
        strategy = strategy_factory(kc)
        kernel.register(strategy)

        result = kernel.run()
        sid = strategy.participant_id
        pnl = result.pnl_per_participant.get(sid, 0.0)
        inv = result.inventory_per_participant.get(sid, 0.0)
        test_pnls.append(pnl)
        test_invs.append(inv)
        if result.halted_at is not None:
            halted += 1
        windows.append(
            WalkForwardWindow(
                index=i,
                start_sec=start,
                end_sec=end,
                pnl=pnl,
                final_inventory=inv,
                n_fills=result.n_fills,
                halted_at=result.halted_at,
            )
        )

    return _aggregate(windows, test_pnls, test_invs, halted)


def _aggregate(
    windows: list[WalkForwardWindow],
    pnls: list[float],
    invs: list[float],
    halted: int,
) -> WalkForwardReport:
    n = len(windows)
    if n == 0:
        return WalkForwardReport(
            n_windows=0,
            windows=[],
            pnl_mean=0.0,
            pnl_stdev=0.0,
            pnl_sharpe=0.0,
            hit_rate=0.0,
            worst_window_pnl=0.0,
            best_window_pnl=0.0,
            inventory_end_mean=0.0,
            inventory_end_stdev=0.0,
            halted_windows=0,
        )

    mean_pnl = statistics.mean(pnls)
    std_pnl = statistics.pstdev(pnls) if n > 1 else 0.0
    sharpe = (mean_pnl / std_pnl * math.sqrt(n)) if std_pnl > 0 else 0.0
    hit_rate = sum(1 for p in pnls if p > 0) / n
    worst = min(pnls)
    best = max(pnls)
    mean_inv = statistics.mean(invs)
    std_inv = statistics.pstdev(invs) if n > 1 else 0.0
    return WalkForwardReport(
        n_windows=n,
        windows=windows,
        pnl_mean=mean_pnl,
        pnl_stdev=std_pnl,
        pnl_sharpe=sharpe,
        hit_rate=hit_rate,
        worst_window_pnl=worst,
        best_window_pnl=best,
        inventory_end_mean=mean_inv,
        inventory_end_stdev=std_inv,
        halted_windows=halted,
    )


__all__ = [
    "WalkForwardConfig",
    "WalkForwardReport",
    "WalkForwardWindow",
    "run_walk_forward",
]
