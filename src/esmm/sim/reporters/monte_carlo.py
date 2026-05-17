"""Monte Carlo scenario variation.

Run N seeded variations of a scenario, then report confidence bands on
P&L / inventory / hedge cost / drawdown. Default: 95% bootstrap CI.

Phase-5 implementation. Stub today.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MonteCarloConfig:
    n_runs: int = 100
    base_seed: int = 0
    confidence: float = 0.95


def run_monte_carlo(*args, **kwargs):
    """Phase 5."""
    raise NotImplementedError("Phase 5")


__all__ = ["MonteCarloConfig", "run_monte_carlo"]
