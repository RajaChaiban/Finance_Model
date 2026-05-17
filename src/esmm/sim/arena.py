"""Multi-strategy bake-off harness.

Runs N strategy variants on the **same** scripted/historical flow in
parallel kernels (seeded identically), then produces a side-by-side
report: P&L curve, TCA breakdown, drawdown, adverse-selection cost,
hedge bps.

This is where research value compounds: controlled A/B testing means
exogenous market moves cancel out and only the strategy delta matters.

Phase-3 implementation. Stub today.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ArenaResult:
    """One arena run's output — per-strategy summary + comparison stats."""

    run_id: str
    strategies: list[str]
    per_strategy: list[dict[str, Any]]
    comparison: dict[str, Any]


class Arena:
    """Phase-3 implementation lands here."""

    def __init__(self, scenario_id: str, configs: list[Any]) -> None:
        self.scenario_id = scenario_id
        self.configs = configs

    def run(self) -> ArenaResult:
        """Phase 3."""
        raise NotImplementedError("Phase 3")


__all__ = ["Arena", "ArenaResult"]
