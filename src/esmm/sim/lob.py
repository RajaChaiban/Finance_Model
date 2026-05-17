"""Price-time-priority limit order book.

The LOB is the load-bearing foundation of the simulator. It maintains
bid/ask ladders, supports ``add`` / ``cancel`` / ``modify``, and tracks
each order's queue position so the match engine can resolve fills with
realistic FIFO semantics.

Phase 1 implementation. Internal layout:

* ``_bids`` / ``_asks`` — ``dict[price, list[Order]]``. Lists are FIFO
  queues of resting orders at that price level.
* ``_bid_prices`` / ``_ask_prices`` — sorted lists of price levels used
  for O(log n) best-price access. Bids are kept in **descending** order
  (best bid is index 0); asks in **ascending** order (best ask is index
  0). We use ``bisect.insort`` against the *negated* price for bids to
  reuse the stdlib.
* ``_orders_by_id`` — map for O(1) cancel / modify lookup.

Queue position semantics:

* ``add`` returns the 1-indexed queue position of the newly inserted
  order *within its price level*.
* ``cancel`` decrements the queue position of orders behind the
  cancelled one (they move up).
* ``modify`` with ``new_size < remaining`` keeps queue position (size
  reduction only). With ``new_size > remaining`` we treat it as a
  cancel + re-add — the order loses queue position and goes to the back
  of its price level. This mirrors how every major exchange behaves.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from src.esmm.schemas import OrderBookLevel, OrderBookSnapshot


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


class CrossedBookError(RuntimeError):
    """Raised when ``add`` would result in best_bid >= best_ask.

    Callers (the match engine) are expected to run matching first so any
    crossing order is consumed before the residual is inserted. Reaching
    this means an upstream invariant was violated.
    """


class LimitOrderBook:
    """Price-time-priority LOB.

    Public API:

    * ``add(order) -> int`` — insert a limit order, return its 1-indexed
      queue position within its price level.
    * ``cancel(order_id) -> bool`` — remove an order, returns True if it
      existed and was live.
    * ``modify(order_id, new_size) -> int`` — see docstring on queue
      semantics.
    * ``snapshot(ts) -> OrderBookSnapshot`` — emit a Pydantic snapshot
      aggregated by price level.
    * ``best_bid()`` / ``best_ask()`` / ``mid()`` — top-of-book.
    """

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        # price → FIFO list of live (non-cancelled, remaining > 0) orders.
        self._bids: dict[float, list[Order]] = {}
        self._asks: dict[float, list[Order]] = {}
        # Sorted price ladders. Bids kept as the *negated* price so the
        # smallest negation = largest bid sits at index 0 once we reverse.
        # We store them directly as a sorted list and rely on convention:
        #   _bid_prices is sorted DESCENDING (best first).
        #   _ask_prices is sorted ASCENDING (best first).
        self._bid_prices: list[float] = []  # descending
        self._ask_prices: list[float] = []  # ascending
        self._orders_by_id: dict[int, Order] = {}
        self._next_id: int = 0

    # ------------------------------------------------------------------
    # ID + helpers
    # ------------------------------------------------------------------
    def next_order_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _side_books(
        self, side: OrderSide
    ) -> tuple[dict[float, list[Order]], list[float], bool]:
        """Return (price→queue map, sorted prices, is_bid)."""
        if side is OrderSide.BUY:
            return self._bids, self._bid_prices, True
        return self._asks, self._ask_prices, False

    def _insert_price(self, sorted_prices: list[float], price: float, is_bid: bool) -> None:
        """Insert ``price`` into ``sorted_prices`` preserving order.

        ``is_bid`` selects descending vs ascending order.
        """
        if is_bid:
            # Descending: insert at the position where everything before
            # is >= price and everything after is < price.
            # bisect operates on ascending lists; emulate with negation.
            # For small typical depths (~100) the linear approach is
            # plenty; we use bisect for correctness on larger books.
            neg = [-p for p in sorted_prices]
            idx = bisect.bisect_left(neg, -price)
            sorted_prices.insert(idx, price)
        else:
            idx = bisect.bisect_left(sorted_prices, price)
            sorted_prices.insert(idx, price)

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------
    def add(self, order: Order) -> int:
        """Insert a LIMIT order. Returns 1-indexed queue position.

        Raises :class:`CrossedBookError` if the insertion would result in
        a crossed book — callers must match first.
        """
        if order.order_type is not OrderType.LIMIT:
            raise ValueError(
                f"LimitOrderBook.add only accepts LIMIT orders; got {order.order_type}"
            )
        if not math.isfinite(order.price):
            raise ValueError(f"LIMIT order must have a finite price; got {order.price}")
        if order.remaining <= 0:
            raise ValueError(f"Cannot add order with remaining={order.remaining}")
        if order.order_id in self._orders_by_id:
            raise ValueError(f"Order id {order.order_id} already in book")

        side_book, sorted_prices, is_bid = self._side_books(order.side)
        if order.price not in side_book:
            side_book[order.price] = []
            self._insert_price(sorted_prices, order.price, is_bid)
        queue = side_book[order.price]
        queue.append(order)
        self._orders_by_id[order.order_id] = order

        # Crossed-book guard. Run *after* insertion so we report the
        # actual state but raise before any caller acts on it.
        if self._bid_prices and self._ask_prices:
            if self._bid_prices[0] >= self._ask_prices[0]:
                # Roll back the insert so the book stays consistent.
                queue.pop()
                if not queue:
                    del side_book[order.price]
                    sorted_prices.remove(order.price)
                del self._orders_by_id[order.order_id]
                raise CrossedBookError(
                    f"add would cross book: best_bid={self._bid_prices[0]} "
                    f">= best_ask={self._ask_prices[0]}"
                )

        return len(queue)  # 1-indexed = len after append

    def cancel(self, order_id: int) -> bool:
        """Remove ``order_id`` from the book. Returns True if cancelled.

        Orders behind the cancelled order shift forward (their queue
        position decrements). Returns False if the id is unknown or
        already cancelled / fully filled.
        """
        order = self._orders_by_id.get(order_id)
        if order is None or order.cancelled or order.remaining <= 0:
            return False
        order.cancelled = True
        side_book, sorted_prices, _ = self._side_books(order.side)
        queue = side_book.get(order.price)
        if queue is None:
            return False
        try:
            queue.remove(order)
        except ValueError:
            return False
        if not queue:
            del side_book[order.price]
            sorted_prices.remove(order.price)
        # Keep _orders_by_id entry so later modify/cancel calls return
        # False rather than KeyError — pop to avoid memory growth in
        # long-running sims.
        del self._orders_by_id[order_id]
        return True

    def modify(self, order_id: int, new_size: float) -> int:
        """Resize a resting order.

        Semantics (matches major exchanges):

        * ``new_size <= 0`` — treated as a cancel, returns 0.
        * ``new_size < remaining`` — size-only decrease, queue position
          is **preserved**. Returns the (unchanged) 1-indexed position.
        * ``new_size == remaining`` — no-op, returns current position.
        * ``new_size > remaining`` — order loses time priority: it is
          cancelled and re-added at the back of its price level. The
          new ``size`` becomes ``new_size``.

        Raises ``KeyError`` if the order id is unknown.
        """
        order = self._orders_by_id.get(order_id)
        if order is None:
            raise KeyError(f"Unknown order id {order_id}")
        if order.cancelled or order.remaining <= 0:
            raise KeyError(f"Order {order_id} is not live")

        if new_size <= 0:
            self.cancel(order_id)
            return 0

        side_book, _, _ = self._side_books(order.side)
        queue = side_book[order.price]
        if new_size < order.remaining:
            # Shrink in place. Queue position unchanged.
            order.remaining = new_size
            order.size = new_size
            return queue.index(order) + 1
        if new_size == order.remaining:
            return queue.index(order) + 1
        # Grow → lose priority. Cancel + re-add.
        side = order.side
        price = order.price
        symbol = order.symbol
        ts = order.ts
        owner_id = order.owner_id
        self.cancel(order_id)
        new_order = Order(
            order_id=order_id,
            symbol=symbol,
            side=side,
            price=price,
            size=new_size,
            ts=ts,
            owner_id=owner_id,
            order_type=OrderType.LIMIT,
        )
        return self.add(new_order)

    # ------------------------------------------------------------------
    # Top-of-book + snapshot
    # ------------------------------------------------------------------
    def best_bid(self) -> Optional[float]:
        return self._bid_prices[0] if self._bid_prices else None

    def best_ask(self) -> Optional[float]:
        return self._ask_prices[0] if self._ask_prices else None

    def mid(self) -> float:
        """Mid price. Returns NaN when either side is empty."""
        bb = self.best_bid()
        ba = self.best_ask()
        if bb is None or ba is None:
            return float("nan")
        return 0.5 * (bb + ba)

    def best_bid_size(self) -> float:
        bb = self.best_bid()
        if bb is None:
            return 0.0
        return sum(o.remaining for o in self._bids[bb])

    def best_ask_size(self) -> float:
        ba = self.best_ask()
        if ba is None:
            return 0.0
        return sum(o.remaining for o in self._asks[ba])

    def queue_position(self, order_id: int) -> Optional[int]:
        """1-indexed queue position of ``order_id`` at its price level.

        Returns ``None`` if the order is not in the book.
        """
        order = self._orders_by_id.get(order_id)
        if order is None or order.cancelled or order.remaining <= 0:
            return None
        side_book, _, _ = self._side_books(order.side)
        queue = side_book.get(order.price)
        if queue is None:
            return None
        try:
            return queue.index(order) + 1
        except ValueError:
            return None

    def snapshot(self, ts: float) -> OrderBookSnapshot:
        """Emit a Pydantic snapshot aggregated by price level.

        Snapshot construction obeys the invariants in
        :class:`src.esmm.schemas.OrderBookSnapshot` — bids descending,
        asks ascending, no empty side. If the book lacks a side, we
        return the snapshot via ``model_construct`` to bypass the
        validator, because that case is meaningful for a real sim
        (uncrossed-but-half-empty book) and the schema docstring says
        defensive code must be able to handle malformed inputs.

        For the typical case (both sides populated), the snapshot passes
        full validation.
        """
        bid_levels = [
            OrderBookLevel(price=p, size=sum(o.remaining for o in self._bids[p]))
            for p in self._bid_prices
        ]
        ask_levels = [
            OrderBookLevel(price=p, size=sum(o.remaining for o in self._asks[p]))
            for p in self._ask_prices
        ]

        if not bid_levels or not ask_levels:
            # Schema demands ≥1 level on each side; bypass with
            # model_construct for the partial-book case rather than
            # invent fake prices.
            return OrderBookSnapshot.model_construct(
                ts=ts,
                symbol=self.symbol,
                bids=bid_levels,
                asks=ask_levels,
            )
        return OrderBookSnapshot(
            ts=ts,
            symbol=self.symbol,
            bids=bid_levels,
            asks=ask_levels,
        )

    # ------------------------------------------------------------------
    # Internal mutators used by MatchEngine
    # ------------------------------------------------------------------
    def _consume_top_of_queue(self, side: OrderSide, price: float, size: float) -> None:
        """Decrement ``size`` from the head of the queue at ``price``.

        If the head's ``remaining`` hits 0 it is removed and the next
        order moves up. Used exclusively by the match engine.
        """
        side_book, sorted_prices, _ = self._side_books(side)
        queue = side_book.get(price)
        if not queue:
            raise KeyError(f"No queue at {price} for side {side}")
        head = queue[0]
        if size > head.remaining + 1e-12:
            raise ValueError(
                f"Cannot consume {size} from head with remaining={head.remaining}"
            )
        head.remaining -= size
        if head.remaining <= 1e-12:
            head.remaining = 0.0
            queue.pop(0)
            self._orders_by_id.pop(head.order_id, None)
            if not queue:
                del side_book[price]
                sorted_prices.remove(price)

    def _remove_resting(self, order: Order) -> None:
        """Remove ``order`` from the book (used by self-trade prevention)."""
        order.cancelled = True
        side_book, sorted_prices, _ = self._side_books(order.side)
        queue = side_book.get(order.price)
        if queue is None:
            return
        try:
            queue.remove(order)
        except ValueError:
            return
        if not queue:
            del side_book[order.price]
            sorted_prices.remove(order.price)
        self._orders_by_id.pop(order.order_id, None)


__all__ = [
    "Order",
    "OrderSide",
    "OrderType",
    "LimitOrderBook",
    "CrossedBookError",
]
