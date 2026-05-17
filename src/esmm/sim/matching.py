"""Match engine — price-time priority FIFO matching.

Phase-1 responsibilities:

* cross detection — incoming buy at >= best ask (or sell at <= best bid)
* walk the opposite ladder at increasing aggression, generating fills
  at the *resting* order's price (price-time priority convention)
* partial fills against multiple resting orders
* self-trade prevention — if ``self_trade_prevention=True`` and the
  incoming order's ``owner_id`` matches a resting order's, the resting
  order is cancelled and walking continues
* if the incoming order is a LIMIT and remainder > 0 after the walk,
  the remainder is rested on the book
* MARKET orders never rest — leftover is reported as ``remainder``

The match engine mutates the LOB in place. Fills are returned but **not
persisted as Pydantic ``Fill`` objects** — that translation happens at
the kernel layer where fee_bps / fair_value / etc. are known.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.esmm.sim.lob import LimitOrderBook, Order, OrderSide, OrderType


@dataclass
class MatchResult:
    """Result of attempting to match a single incoming order."""

    fills: list[tuple[Order, Order, float, float]]  # (aggressor, resting, price, size)
    remainder: float


class MatchEngine:
    """Match engine — mutates the attached LOB on each ``match`` call."""

    def __init__(self, lob: LimitOrderBook, self_trade_prevention: bool = True) -> None:
        self.lob = lob
        self.self_trade_prevention = self_trade_prevention

    # ------------------------------------------------------------------
    def _opposing_top(self, side: OrderSide) -> tuple[float, list[Order]] | None:
        """Return (best price, FIFO queue) on the side that ``side`` crosses."""
        if side is OrderSide.BUY:
            ba = self.lob.best_ask()
            if ba is None:
                return None
            return ba, self.lob._asks[ba]
        bb = self.lob.best_bid()
        if bb is None:
            return None
        return bb, self.lob._bids[bb]

    @staticmethod
    def _crosses(side: OrderSide, limit_price: float, resting_price: float) -> bool:
        """Does an aggressor on ``side`` at ``limit_price`` cross ``resting_price``?"""
        if side is OrderSide.BUY:
            return limit_price >= resting_price
        return limit_price <= resting_price

    # ------------------------------------------------------------------
    def match(self, incoming: Order) -> MatchResult:
        """Apply ``incoming`` to the LOB and return fills + remainder.

        Behaviour:

        * MARKET orders — walk until ``remaining`` is 0 or the opposing
          side is empty. Whatever's left is reported as ``remainder``;
          MARKET orders never rest.
        * LIMIT orders — walk the book while the aggressor's limit
          crosses the resting price. Any leftover is inserted into the
          book via ``LimitOrderBook.add``.

        Trade price = resting order's price (standard price-time
        priority convention). The aggressor pays the price they crossed
        at (which is at least as good as their limit).
        """
        if incoming.remaining <= 0:
            return MatchResult(fills=[], remainder=0.0)

        fills: list[tuple[Order, Order, float, float]] = []
        is_market = incoming.order_type is OrderType.MARKET
        is_limit = incoming.order_type is OrderType.LIMIT
        if not (is_market or is_limit):
            raise ValueError(
                f"MatchEngine only handles LIMIT or MARKET orders; got {incoming.order_type}"
            )

        # ------------------------------------------------------------------
        # Walking phase
        # ------------------------------------------------------------------
        while incoming.remaining > 1e-12:
            top = self._opposing_top(incoming.side)
            if top is None:
                break
            resting_price, queue = top

            # LIMIT: stop once the price level no longer crosses.
            if is_limit and not self._crosses(incoming.side, incoming.price, resting_price):
                break

            if not queue:
                # Should not happen; defensive.
                break
            resting = queue[0]

            # Self-trade prevention. We cancel the resting order and
            # keep walking — by spec, the aggressor remains active and
            # consumes the next resting order at this (or worse) price.
            if (
                self.self_trade_prevention
                and incoming.owner_id == resting.owner_id
            ):
                self.lob._remove_resting(resting)
                continue

            trade_size = min(incoming.remaining, resting.remaining)
            trade_price = resting_price  # price-time priority

            fills.append((incoming, resting, trade_price, trade_size))

            # Update the resting order via the LOB's internal mutator so
            # the price-level / id maps stay consistent.
            self.lob._consume_top_of_queue(resting.side, resting_price, trade_size)
            incoming.remaining -= trade_size

        # ------------------------------------------------------------------
        # Resting phase
        # ------------------------------------------------------------------
        if is_limit and incoming.remaining > 1e-12:
            # Snap to a clean number to avoid float-creep on the book.
            # We keep incoming.size as recorded; remaining is what rests.
            # The book stores the order object directly, so consumers
            # that look at .remaining get the residual amount.
            self.lob.add(incoming)
        elif is_market and incoming.remaining > 1e-12:
            # MARKET orders never rest.
            pass

        return MatchResult(fills=fills, remainder=max(incoming.remaining, 0.0))


__all__ = ["MatchEngine", "MatchResult"]
