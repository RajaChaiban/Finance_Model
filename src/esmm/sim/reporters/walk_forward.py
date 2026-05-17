"""Rolling-window walk-forward evaluator.

Train on [t0,t1], test on [t1,t2], advance, repeat. Outputs out-of-sample
Sharpe + drawdown + edge-over-passive per window. Default window:
30 days train, 5 days test, 1 day step (sec 10 of design spec).

Phase-5 implementation. Stub today.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WalkForwardConfig:
    train_days: int = 30
    test_days: int = 5
    step_days: int = 1


def run_walk_forward(*args, **kwargs):
    """Phase 5."""
    raise NotImplementedError("Phase 5")


__all__ = ["WalkForwardConfig", "run_walk_forward"]
