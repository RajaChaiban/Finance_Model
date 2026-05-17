"""Match engine — price-time priority FIFO matching.

Phase-1 responsibilities:
  * cross detection: incoming buy at >= best ask (or sell at <= best bid)
  * walk the opposite ladder at increasing aggression, generating fills
  * partial fills, self-trade prevention (drop the resting order if same owner)
  * emit Fill events for both sides with the correct sign convention
    (consistent with :class:`src.esmm.schemas.Fill`)
"""

from __future__ import annotations

from dataclasses import dataclass

from src.esmm.sim.lob import LimitOrderBook, Order


@dataclass
class MatchResult:
    """Result of attempting to match a single incoming order."""

    fills: list[tuple[Order, Order, float, float]]  # (aggressor, resting, price, size)
    remainder: float


class MatchEngine:
    """Stateless match engine — pure function of LOB state + incoming order."""

    def __init__(self, lob: LimitOrderBook, self_trade_prevention: bool = True) -> None:
        self.lob = lob
        self.self_trade_prevention = self_trade_prevention

    def match(self, incoming: Order) -> MatchResult:
        """Apply ``incoming`` to ``self.lob`` and return fills + remainder.

        Phase 1 implementation.
        """
        raise NotImplementedError("Phase 1")


__all__ = ["MatchEngine", "MatchResult"]
