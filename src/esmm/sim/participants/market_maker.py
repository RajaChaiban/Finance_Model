"""MarketMakerParticipant — the core strategy under test in the eSMM sim.

This is the participant that *makes* the market. It reuses the existing
quote engine + inventory book + auto-hedger modules from
:mod:`src.esmm.quote_engine`, :mod:`src.esmm.inventory`, and
:mod:`src.esmm.hedger` — so its behaviour is identical to the standalone
MM lab; the only new thing here is the kernel-facing protocol.

Loop on each :meth:`decide` tick:

1. If no cached snapshot, return [].
2. If we requoted recently (``now - last_requote_ts < requote_interval_sec``),
   return [].
3. Ask :class:`~src.esmm.quote_engine.QuoteEngine` for a fresh
   :class:`~src.esmm.schemas.Quote` given the current inventory.
4. Emit a BID and an ASK LIMIT order at the quoted prices/sizes. If
   either side's size is zero (quote pulled past ``max_inventory``) we
   skip that side.
5. If hedging is enabled, evaluate
   :class:`~src.esmm.hedger.AutoHedger` against the current net delta.
   When it fires we emit a MARKET order matching the hedge fill.
6. Stamp ``last_requote_ts = now``.

Limitation worth flagging: this participant does NOT cancel stale
quotes from a previous tick. Each ``decide`` call simply *adds* a new
pair of limits — the kernel's LOB will continue to hold whichever of
our previous orders haven't been filled or cancelled by external means.
A more polished v2 should track outstanding order ids and emit CANCEL
operations (the kernel currently routes orders by ``OrderType`` so a
proper CANCEL path would also need kernel support). For phase-4 v1
this approximation is good enough to stress-test the quoting/hedging
plumbing inside the kernel.
"""

from __future__ import annotations

from typing import Optional

from src.esmm.hedger import AutoHedger
from src.esmm.inventory import InventoryBook
from src.esmm.quote_engine import QuoteEngine
from src.esmm.schemas import (
    Fill,
    MarketMakingConfig,
    OrderBookSnapshot,
    Quote,
    Side,
)
from src.esmm.sim.lob import Order, OrderSide, OrderType


class MarketMakerParticipant:
    """A market-making participant that quotes both sides + auto-hedges."""

    participant_id: str

    def __init__(
        self,
        participant_id: str,
        config: MarketMakingConfig,
        requote_interval_sec: float = 0.05,
        use_hedger: bool = True,
    ) -> None:
        if requote_interval_sec < 0:
            raise ValueError(
                f"requote_interval_sec must be >= 0; got {requote_interval_sec}"
            )

        self.participant_id = participant_id
        self.config = config
        self.requote_interval_sec = float(requote_interval_sec)
        self.use_hedger = bool(use_hedger)

        # Reuse the standalone MM lab modules verbatim. InventoryBook
        # is multi-symbol; we only care about ``config.symbol``.
        self.inventory = InventoryBook()
        self.quote_engine = QuoteEngine(config)
        self.hedger = AutoHedger(config)

        self.last_snapshot: Optional[OrderBookSnapshot] = None
        self.last_quote: Optional[Quote] = None
        self.last_requote_ts: Optional[float] = None
        self.n_fills: int = 0

    # ------------------------------------------------------------------
    # Participant protocol
    # ------------------------------------------------------------------
    def on_book(self, snapshot: OrderBookSnapshot) -> None:
        """Cache the latest snapshot.

        We only act on snapshots of *our* symbol; other-symbol snapshots
        are ignored so the participant can sit safely inside a multi-
        symbol kernel without picking up unrelated updates.
        """
        if snapshot.symbol != self.config.symbol:
            return
        self.last_snapshot = snapshot

    def on_fill(self, fill: Fill) -> None:
        """Apply the fill to the internal inventory book."""
        self.inventory.apply_fill(fill)
        self.n_fills += 1

    def decide(self, now: float) -> list[Order]:
        """Refresh quotes (and optionally hedge) once per requote window."""
        if self.last_snapshot is None:
            return []

        # Need a usable two-sided book to quote at all.
        if not self.last_snapshot.bids or not self.last_snapshot.asks:
            return []

        # Rate-limit our own quoting.
        if self.last_requote_ts is not None:
            if now - self.last_requote_ts < self.requote_interval_sec:
                return []

        quote = self.quote_engine.quote(self.last_snapshot, self.inventory)
        self.last_quote = quote

        orders: list[Order] = []

        # NOTE: see module docstring — we don't cancel previous quotes.
        # The previous tick's resting orders stay on the book until
        # filled or cancelled externally. v1 limitation.
        if quote.bid_size > 0:
            orders.append(
                Order(
                    order_id=0,  # kernel assigns the real id
                    symbol=self.config.symbol,
                    side=OrderSide.BUY,
                    price=quote.bid_price,
                    size=quote.bid_size,
                    ts=now,
                    owner_id=self.participant_id,
                    order_type=OrderType.LIMIT,
                )
            )
        if quote.ask_size > 0:
            orders.append(
                Order(
                    order_id=0,
                    symbol=self.config.symbol,
                    side=OrderSide.SELL,
                    price=quote.ask_price,
                    size=quote.ask_size,
                    ts=now,
                    owner_id=self.participant_id,
                    order_type=OrderType.LIMIT,
                )
            )

        if self.use_hedger:
            position = self.inventory.get(self.config.symbol)
            mid = 0.5 * (self.last_snapshot.best_bid + self.last_snapshot.best_ask)
            hedge_fill = self.hedger.evaluate(
                ts=now,
                net_delta=position.quantity,
                hedge_price=mid,
            )
            if hedge_fill is not None:
                # The hedger returns a synthetic Fill. In the sim we
                # achieve the same exposure by emitting a MARKET order
                # in the corresponding direction; the kernel's match
                # engine resolves the actual fill price from the LOB.
                hedge_side = (
                    OrderSide.SELL if hedge_fill.side == Side.SELL else OrderSide.BUY
                )
                orders.append(
                    Order(
                        order_id=0,
                        symbol=self.config.symbol,
                        side=hedge_side,
                        price=float("nan"),
                        size=hedge_fill.size,
                        ts=now,
                        owner_id=self.participant_id,
                        order_type=OrderType.MARKET,
                    )
                )

        self.last_requote_ts = now
        return orders


__all__ = ["MarketMakerParticipant"]
