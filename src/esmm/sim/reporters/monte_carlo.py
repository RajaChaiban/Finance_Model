"""Monte Carlo seeded-variation evaluator.

Runs the same scenario *N times* under different seeds and reports
confidence bands on the result distribution. Critical for honest
strategy evaluation: a single seed can flatter or punish a strategy
arbitrarily; the question is "across plausible flow paths, where does
this strategy land?"

Outputs:
  * P&L percentiles (5/25/50/75/95)
  * Bootstrap CI on the mean (default 95%)
  * VAR(95%) — empirical 5th-percentile P&L loss
  * Tail P&L (worst 5%)
  * Inventory-at-end distribution (mean / std / 95th)
  * Hit rate across runs
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, field
from typing import Any, Callable

from src.esmm.sim.kernel import Kernel, KernelConfig
from src.esmm.sim.latency import LatencyConfig
from src.esmm.sim.participants.base import Participant


@dataclass
class MonteCarloConfig:
    n_runs: int = 100
    base_seed: int = 0
    confidence: float = 0.95  # CI width

    def __post_init__(self) -> None:
        if self.n_runs <= 0:
            raise ValueError("n_runs must be > 0")
        if not (0 < self.confidence < 1):
            raise ValueError("confidence must be in (0, 1)")


@dataclass
class MonteCarloReport:
    n_runs: int
    pnl_mean: float
    pnl_stdev: float
    pnl_percentiles: dict[str, float]  # "p5", "p25", "p50", "p75", "p95"
    pnl_ci_low: float
    pnl_ci_high: float
    var_95: float  # 5th percentile P&L (a loss number)
    tail_mean_5pct: float  # mean of the worst 5% runs
    hit_rate: float  # fraction of runs with pnl > 0
    inv_end_mean: float
    inv_end_stdev: float
    halted_runs: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_runs": self.n_runs,
            "pnl_mean": self.pnl_mean,
            "pnl_stdev": self.pnl_stdev,
            "pnl_percentiles": dict(self.pnl_percentiles),
            "pnl_ci_low": self.pnl_ci_low,
            "pnl_ci_high": self.pnl_ci_high,
            "var_95": self.var_95,
            "tail_mean_5pct": self.tail_mean_5pct,
            "hit_rate": self.hit_rate,
            "inv_end_mean": self.inv_end_mean,
            "inv_end_stdev": self.inv_end_stdev,
            "halted_runs": self.halted_runs,
        }


def run_monte_carlo(
    *,
    config: MonteCarloConfig,
    kernel_config: KernelConfig,
    strategy_factory: Callable[[KernelConfig], Participant],
    flow_factory: Callable[[KernelConfig], list[Participant]] | None = None,
    latency_config: LatencyConfig | None = None,
) -> MonteCarloReport:
    """Run ``n_runs`` seeded variations and aggregate.

    Each run uses seed ``config.base_seed + i * 7919`` (large prime keeps
    streams independent). Per-run state is fully isolated: strategy and
    flow factories are called fresh for each run.
    """
    pnls: list[float] = []
    invs: list[float] = []
    halted = 0

    for i in range(config.n_runs):
        run_seed = (config.base_seed + i * 7919) & 0x7FFFFFFF

        kc = KernelConfig(
            **{**kernel_config.__dict__, "seed": run_seed}
        )
        kernel = Kernel(
            kc,
            latency_config=latency_config or LatencyConfig(seed=run_seed),
        )
        if flow_factory is not None:
            for p in flow_factory(kc):
                kernel.register(p)
        strategy = strategy_factory(kc)
        kernel.register(strategy)

        result = kernel.run()
        sid = strategy.participant_id
        pnls.append(result.pnl_per_participant.get(sid, 0.0))
        invs.append(result.inventory_per_participant.get(sid, 0.0))
        if result.halted_at is not None:
            halted += 1

    return _aggregate(pnls, invs, halted, config.confidence)


def _aggregate(
    pnls: list[float], invs: list[float], halted: int, confidence: float
) -> MonteCarloReport:
    n = len(pnls)
    if n == 0:
        return MonteCarloReport(
            n_runs=0,
            pnl_mean=0.0,
            pnl_stdev=0.0,
            pnl_percentiles={k: 0.0 for k in ("p5", "p25", "p50", "p75", "p95")},
            pnl_ci_low=0.0,
            pnl_ci_high=0.0,
            var_95=0.0,
            tail_mean_5pct=0.0,
            hit_rate=0.0,
            inv_end_mean=0.0,
            inv_end_stdev=0.0,
            halted_runs=0,
        )

    pnl_sorted = sorted(pnls)
    mean = statistics.mean(pnls)
    std = statistics.pstdev(pnls) if n > 1 else 0.0

    pct = {
        "p5": _percentile(pnl_sorted, 0.05),
        "p25": _percentile(pnl_sorted, 0.25),
        "p50": _percentile(pnl_sorted, 0.50),
        "p75": _percentile(pnl_sorted, 0.75),
        "p95": _percentile(pnl_sorted, 0.95),
    }

    # Bootstrap CI on the mean — 2000 resamples is plenty for typical N.
    ci_low, ci_high = _bootstrap_ci_mean(pnls, confidence)

    var_95 = pct["p5"]
    tail_count = max(1, int(round(0.05 * n)))
    tail_mean = statistics.mean(pnl_sorted[:tail_count])

    hit_rate = sum(1 for p in pnls if p > 0) / n

    return MonteCarloReport(
        n_runs=n,
        pnl_mean=mean,
        pnl_stdev=std,
        pnl_percentiles=pct,
        pnl_ci_low=ci_low,
        pnl_ci_high=ci_high,
        var_95=var_95,
        tail_mean_5pct=tail_mean,
        hit_rate=hit_rate,
        inv_end_mean=statistics.mean(invs),
        inv_end_stdev=statistics.pstdev(invs) if n > 1 else 0.0,
        halted_runs=halted,
    )


def _percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolated empirical percentile.

    ``sorted_values`` must be ascending. ``q`` in [0, 1].
    """
    if not sorted_values:
        return 0.0
    if q <= 0:
        return sorted_values[0]
    if q >= 1:
        return sorted_values[-1]
    pos = q * (len(sorted_values) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_values[lo]
    frac = pos - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def _bootstrap_ci_mean(
    values: list[float], confidence: float, n_resamples: int = 2000, seed: int = 0
) -> tuple[float, float]:
    """Bootstrap confidence interval for the sample mean."""
    if len(values) <= 1:
        v = values[0] if values else 0.0
        return v, v
    rng = random.Random(seed)
    means: list[float] = []
    n = len(values)
    for _ in range(n_resamples):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    alpha = 1.0 - confidence
    lo = _percentile(means, alpha / 2)
    hi = _percentile(means, 1.0 - alpha / 2)
    return lo, hi


__all__ = [
    "MonteCarloConfig",
    "MonteCarloReport",
    "run_monte_carlo",
]
