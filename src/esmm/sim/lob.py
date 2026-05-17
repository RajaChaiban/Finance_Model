"""Price-time-priority limit order book.

The LOB is the load-bearing foundation of the simulator. It maintains
bid/ask ladders, supports ``add`` / ``cancel`` / ``modify``, and tracks
each order's queue position so the match engine can resolve fills with
realistic FIFO semantics.

Implementation lands in Phase 1. This module currently exposes the type
contracts the kernel and match engine will depend on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class OrderType(str, Enum):
    LIMIT = "limit"
    MARKET = "market"
    CANCEL = "cancel"
    MODIFY = "modify"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class Order:
    """A single resting or in-flight order."""

    order_id: int
    symbol: str
    side: OrderSide
    price: float  # NaN for market orders
    size: float
    ts: float
    owner_id: str  # which participant submitted it (for fill routing)
    order_type: OrderType = OrderType.LIMIT
    # Mutable state — match engine updates these as fills/cancels occur
    remaining: float = field(init=False)
    cancelled: bool = False

    def __post_init__(self) -> None:
        self.remaining = self.size


class LimitOrderBook:
    """Price-time-priority LOB.

    Phase-1 implementation TODO:
      * bid/ask ladders as sorted dicts (price → FIFO deque of Order)
      * add(order): insert into ladder, return book-side queue position
      * cancel(order_id): mark cancelled, remove from queue
      * modify(order_id, new_size): if smaller, keep queue position;
        if larger, treat as cancel + add (loses queue position)
      * snapshot(): emit :class:`src.esmm.schemas.OrderBookSnapshot`
      * crossed_book guard
    """

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        # Phase 1 will populate these
        self._bids: dict[float, list[Order]] = {}
        self._asks: dict[float, list[Order]] = {}
        self._orders_by_id: dict[int, Order] = {}
        self._next_id: int = 0

    def next_order_id(self) -> int:
        self._next_id += 1
        return self._next_id


__all__ = ["Order", "OrderSide", "OrderType", "LimitOrderBook"]
