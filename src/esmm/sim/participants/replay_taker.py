"""Replay-taker — emits a pre-recorded aggressor tape as MARKET orders.

Used by Track D (historical replay) of the simulator: feed in a sorted
list of ``(ts, side, size)`` tuples extracted from a real trades file
and the participant will inject them at the right wall-clock times.

Internal cursor advances monotonically — an event is never re-emitted.
If the kernel ticks faster than the event spacing, no harm done; if it
ticks slower (or skips forward, e.g. after a halt), all events whose
timestamps have passed are flushed in one ``decide`` call. This matches
how a real exchange would have queued the orders during the gap.

Sorting is enforced at construction time — relying on the kernel to
hand us events in order would let a buggy adapter silently drop trades,
which is exactly the kind of bug that ruins a backtest result.
"""

from __future__ import annotations

from src.esmm.schemas import Fill, OrderBookSnapshot
from src.esmm.sim.lob import Order, OrderSide, OrderType


_SIDE_MAP: dict[str, OrderSide] = {
    "buy": OrderSide.BUY,
    "sell": OrderSide.SELL,
}


class ReplayTaker:
    """Deterministic replay of an aggressor tape.

    Parameters
    ----------
    events
        List of ``(ts, side_str, size)`` tuples. ``side_str`` must be
        ``"buy"`` or ``"sell"``; ``size`` must be > 0; the list must be
        sorted ascending by ts. ``ValueError`` otherwise.
    """

    participant_id: str

    def __init__(
        self,
        participant_id: str,
        symbol: str,
        events: list[tuple[float, str, float]],
    ) -> None:
        self.participant_id = participant_id
        self.symbol = symbol

        # Validate up-front so a malformed tape never silently produces
        # the wrong simulation. Keep an internal list of typed tuples.
        validated: list[tuple[float, OrderSide, float]] = []
        last_ts: float | None = None
        for i, ev in enumerate(events):
            if len(ev) != 3:
                raise ValueError(
                    f"events[{i}] must be (ts, side, size); got {ev!r}"
                )
            ts, side_str, size = ev
            if not isinstance(side_str, str) or side_str.lower() not in _SIDE_MAP:
                raise ValueError(
                    f"events[{i}].side must be 'buy' or 'sell'; got {side_str!r}"
                )
            if size <= 0:
                raise ValueError(f"events[{i}].size must be > 0; got {size}")
            if last_ts is not None and ts < last_ts:
                raise ValueError(
                    f"events must be sorted by ts; events[{i}].ts={ts} < "
                    f"previous ts={last_ts}"
                )
            validated.append((float(ts), _SIDE_MAP[side_str.lower()], float(size)))
            last_ts = ts

        self._events = validated
        # Cursor = index of the next event to emit. Monotonic; never resets.
        self._cursor = 0

    # ------------------------------------------------------------------
    # Participant protocol
    # ------------------------------------------------------------------
    def on_book(self, snapshot: OrderBookSnapshot) -> None:  # noqa: ARG002
        """Replay is fully pre-scripted — book state is irrelevant."""
        return

    def on_fill(self, fill: Fill) -> None:  # noqa: ARG002
        """Replay carries no inventory state."""
        return

    def decide(self, now: float) -> list[Order]:
        """Emit every event whose ts has passed, in order."""
        orders: list[Order] = []
        while self._cursor < len(self._events):
            ts, side, size = self._events[self._cursor]
            if ts > now:
                break
            orders.append(
                Order(
                    order_id=0,  # placeholder — kernel assigns the real id
                    symbol=self.symbol,
                    side=side,
                    price=float("nan"),  # MARKET
                    size=size,
                    ts=ts,
                    owner_id=self.participant_id,
                    order_type=OrderType.MARKET,
                )
            )
            self._cursor += 1
        return orders


__all__ = ["ReplayTaker"]
