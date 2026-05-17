"""Pre-trade and post-trade risk enforcement.

A real desk dies from skipping these checks. The sim defaults to the
discipline a regulated MM would impose.

Pre-trade (per order):
  * ``max_notional_usd`` — gross open notional
  * ``max_net_delta`` — directional exposure
  * ``max_gross_gamma`` — convexity exposure (phase 4 / options)
  * ``concentration_pct`` — single-symbol cap
  * ``max_orders_per_sec`` — rate limit (mirrors exchange throttles)

Post-trade (continuous):
  * ``daily_loss_kill_switch_usd`` — flat the book, stop quoting
  * ``max_drawdown_pct`` — soft warn at 50%, halt at 75%
  * ``inventory_age_sec`` — alert on stale positions

Phase-3 implementation. Stub today.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RiskLimits:
    max_notional_usd: float = 1_000_000.0
    max_net_delta: float = 10_000.0
    max_gross_gamma: float = 0.0  # 0 disables (phase 4)
    concentration_pct: float = 0.4
    max_orders_per_sec: int = 50
    daily_loss_kill_switch_usd: float = 50_000.0
    max_drawdown_pct: float = 0.75
    inventory_age_sec: float = 3600.0


@dataclass
class RiskBreach:
    ts: float
    limit_name: str
    actual_value: float
    threshold: float


class RiskEngine:
    """Phase-3 implementation lands here."""

    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits
        self.breaches: list[RiskBreach] = []
        self.halted: bool = False

    def check_pretrade(self, *args, **kwargs) -> bool:
        """Phase 3."""
        raise NotImplementedError("Phase 3")

    def check_posttrade(self, *args, **kwargs) -> bool:
        """Phase 3."""
        raise NotImplementedError("Phase 3")


__all__ = ["RiskLimits", "RiskBreach", "RiskEngine"]
