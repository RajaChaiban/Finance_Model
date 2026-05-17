"""Informed trader — peeks Δt ahead and trades the predicted edge.

Generates the **adverse selection** the maker has to defend against.
Every fill against this participant is, in expectation, a fill that
moves against the maker (because the informed side bought right before
the price went up).

This is the cleanest way to drop a tunable amount of toxic flow into
the lab: dial ``signal_noise_bps`` up to make the informed flow noisier
(closer to noise traders), down to make it more toxic.

Design notes:

* The future-mid provider is **injected** so the participant doesn't
  need to know how the sim materialises the future (replay tape,
  synthetic GBM, look-ahead into the maker's own price path). The sim
  wires this up at construction time.
* The signal is ``future_mid + N(0, signal_noise_bps/10000 * mid)`` —
  noise scales with price so the threshold check stays scale-invariant.
* The threshold is checked against the **expected edge**, not the
  realised one, so the trader is honest about its own uncertainty. With
  ``signal_noise=0`` and the right future_mid, the trader is a perfect
  oracle (useful for upper-bound TCA analysis).
* Orders are always MARKET — informed flow that posts limits would be
  modelling a *patient* informed trader, which is a different archetype.
"""

from __future__ import annotations

import random
from typing import Callable, Optional

from src.esmm.schemas import Fill, OrderBookSnapshot
from src.esmm.sim.lob import Order, OrderSide, OrderType


class InformedTrader:
    """Sees the future mid Δt ahead and trades when expected edge clears
    a threshold.

    Attributes
    ----------
    participant_id
        Unique id used by the kernel to route fills.
    """

    participant_id: str

    def __init__(
        self,
        participant_id: str,
        symbol: str,
        future_mid_provider: Callable[[float], float],
        *,
        lookahead_sec: float = 0.5,
        edge_threshold_bps: float = 5.0,
        lot: int = 500,
        signal_noise_bps: float = 2.0,
        seed: Optional[int] = None,
    ) -> None:
        if lookahead_sec < 0:
            raise ValueError(f"lookahead_sec must be >= 0; got {lookahead_sec}")
        if edge_threshold_bps < 0:
            raise ValueError(
                f"edge_threshold_bps must be >= 0; got {edge_threshold_bps}"
            )
        if lot <= 0:
            raise ValueError(f"lot must be > 0; got {lot}")
        if signal_noise_bps < 0:
            raise ValueError(f"signal_noise_bps must be >= 0; got {signal_noise_bps}")

        self.participant_id = participant_id
        self.symbol = symbol
        self.future_mid_provider = future_mid_provider
        self.lookahead_sec = float(lookahead_sec)
        self.edge_threshold_bps = float(edge_threshold_bps)
        self.lot = int(lot)
        self.signal_noise_bps = float(signal_noise_bps)

        self._rng = random.Random(seed)
        self._last_mid: Optional[float] = None
        self._last_snapshot_ts: Optional[float] = None

    # ------------------------------------------------------------------
    # Participant protocol
    # ------------------------------------------------------------------
    def on_book(self, snapshot: OrderBookSnapshot) -> None:
        """Cache mid + snapshot ts. ``None`` when the book is half-empty."""
        if not snapshot.bids or not snapshot.asks:
            self._last_mid = None
            self._last_snapshot_ts = snapshot.ts
            return
        self._last_mid = 0.5 * (snapshot.best_bid + snapshot.best_ask)
        self._last_snapshot_ts = snapshot.ts

    def on_fill(self, fill: Fill) -> None:
        """Informed flow models its edge purely from the future-mid
        signal; fills don't update its state."""
        return

    def decide(self, now: float) -> list[Order]:
        mid = self._last_mid
        if mid is None or mid <= 0:
            return []

        # Pull the predicted mid. The provider already incorporates
        # ``lookahead_sec`` — it knows what "the future" means; the
        # participant doesn't have to add the offset itself.
        future_mid = float(self.future_mid_provider(now))

        # Additive Gaussian noise scaled to the current mid keeps the
        # bps semantics. We deliberately use a *symmetric* error model
        # so the threshold check stays unbiased.
        if self.signal_noise_bps > 0:
            sigma = self.signal_noise_bps / 10_000.0 * mid
            future_mid += self._rng.gauss(0.0, sigma)

        edge_bps = (future_mid - mid) / mid * 10_000.0

        if edge_bps > self.edge_threshold_bps:
            side = OrderSide.BUY
        elif edge_bps < -self.edge_threshold_bps:
            side = OrderSide.SELL
        else:
            return []

        order = Order(
            order_id=0,  # placeholder — kernel assigns the real id
            symbol=self.symbol,
            side=side,
            price=float("nan"),  # MARKET order
            size=float(self.lot),
            ts=now,
            owner_id=self.participant_id,
            order_type=OrderType.MARKET,
        )
        return [order]


__all__ = ["InformedTrader"]
