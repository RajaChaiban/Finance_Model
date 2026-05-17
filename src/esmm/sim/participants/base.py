"""Participant protocol — every simulator actor implements this.

A participant is anything that submits orders into the LOB: our MM
strategy, a noise trader, an informed bot, a replay aggressor stream.
The kernel routes book updates and fills to each one and asks for
new orders each tick.

The protocol is deliberately tiny so it's easy to add new archetypes.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.esmm.schemas import Fill, OrderBookSnapshot
from src.esmm.sim.lob import Order


@runtime_checkable
class Participant(Protocol):
    """A simulator actor."""

    participant_id: str

    def on_book(self, snapshot: OrderBookSnapshot) -> None:
        """Called when a new LOB snapshot is published."""
        ...

    def on_fill(self, fill: Fill) -> None:
        """Called when one of this participant's orders is filled."""
        ...

    def decide(self, now: float) -> list[Order]:
        """Asked once per kernel tick — return any new orders/cancels.

        Returning an empty list is fine.
        """
        ...


__all__ = ["Participant"]
