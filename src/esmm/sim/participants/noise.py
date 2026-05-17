"""Noise trader — Poisson-arriving uninformed flow.

The retail / round-lot trader who has no edge. Models random order flow
that arrives at a fixed average rate. Each order is independently:

* a 50/50 BUY or SELL,
* either a MARKET sweep (with probability ``aggressive_pct``) or a LIMIT
  posted at ``mid ± limit_price_offset_bps``,
* sized uniformly in ``[lot_min, lot_max]``.

This is the load-bearing "background liquidity" for the simulator — it's
what makes the LOB look alive while the maker is being graded.

Design choices worth knowing:

* Arrival is **Bernoulli-per-tick** (``rate * dt`` per call to
  :meth:`decide`), not draw-an-exponential. With tick dt ≪ 1/rate this
  matches a Poisson process to first order and is dramatically simpler
  (no fractional-event bookkeeping between ticks). It also degrades
  gracefully if the kernel tick rate falls: at most one order per tick.
* If ``rate * dt`` ever exceeds 1.0 we clip to 1.0 (effectively
  saturating at "one order every tick"). The test that exercises the
  rate stays well below that regime.
* The participant **needs a mid** to act — when only one side of the
  book is populated, it emits nothing rather than guess. This matches
  how real retail flow behaves around a halted book.
"""

from __future__ import annotations

import random
from typing import Optional

from src.esmm.schemas import Fill, OrderBookSnapshot
from src.esmm.sim.lob import Order, OrderSide, OrderType


class NoiseTrader:
    """Poisson-arriving uninformed order flow.

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
        *,
        arrival_rate_hz: float = 2.0,
        lot_min: int = 100,
        lot_max: int = 500,
        aggressive_pct: float = 0.5,
        limit_price_offset_bps: float = 1.0,
        seed: Optional[int] = None,
    ) -> None:
        if arrival_rate_hz < 0:
            raise ValueError(f"arrival_rate_hz must be >= 0; got {arrival_rate_hz}")
        if lot_min <= 0 or lot_max <= 0:
            raise ValueError(f"lot bounds must be > 0; got [{lot_min}, {lot_max}]")
        if lot_min > lot_max:
            raise ValueError(f"lot_min ({lot_min}) > lot_max ({lot_max})")
        if not 0.0 <= aggressive_pct <= 1.0:
            raise ValueError(f"aggressive_pct must be in [0,1]; got {aggressive_pct}")
        if limit_price_offset_bps < 0:
            raise ValueError(
                f"limit_price_offset_bps must be >= 0; got {limit_price_offset_bps}"
            )

        self.participant_id = participant_id
        self.symbol = symbol
        self.arrival_rate_hz = float(arrival_rate_hz)
        self.lot_min = int(lot_min)
        self.lot_max = int(lot_max)
        self.aggressive_pct = float(aggressive_pct)
        self.limit_price_offset_bps = float(limit_price_offset_bps)

        # Use a private Random so we don't perturb the global RNG and so
        # multiple NoiseTraders in one sim can have independent streams.
        self._rng = random.Random(seed)
        self._last_mid: Optional[float] = None
        self._last_decide_ts: Optional[float] = None

    # ------------------------------------------------------------------
    # Participant protocol
    # ------------------------------------------------------------------
    def on_book(self, snapshot: OrderBookSnapshot) -> None:
        """Cache the latest mid. Skip when the book is half-empty."""
        if not snapshot.bids or not snapshot.asks:
            self._last_mid = None
            return
        self._last_mid = 0.5 * (snapshot.best_bid + snapshot.best_ask)

    def on_fill(self, fill: Fill) -> None:
        """Uninformed flow keeps no inventory state."""
        # Intentionally a no-op — the noise trader doesn't react.
        return

    def decide(self, now: float) -> list[Order]:
        """Bernoulli-per-tick arrival; emits at most one order."""
        if self._last_mid is None:
            # No usable book → no opinion.
            self._last_decide_ts = now
            return []

        # Time since last tick. First call: assume one canonical tick at
        # the rate's natural cadence — but we don't know it, so fall back
        # to "treat the first call as having dt = 1 / rate" so the first
        # tick is shaped consistently with steady state.
        if self._last_decide_ts is None:
            # First call: no orders. We need a baseline dt; emitting on
            # the very first tick would let the user's seed force a
            # deterministic outcome regardless of the rate. Skipping the
            # first tick gives us a clean rate measurement.
            self._last_decide_ts = now
            return []

        dt = now - self._last_decide_ts
        self._last_decide_ts = now
        if dt <= 0:
            return []

        prob = self.arrival_rate_hz * dt
        if prob >= 1.0:
            prob = 1.0  # saturate; see module docstring
        if self._rng.random() >= prob:
            return []

        # We're arriving — build the order.
        side = OrderSide.BUY if self._rng.random() < 0.5 else OrderSide.SELL
        size = float(self._rng.randint(self.lot_min, self.lot_max))
        is_aggressive = self._rng.random() < self.aggressive_pct

        if is_aggressive:
            order = Order(
                order_id=0,  # placeholder — kernel assigns the real id
                symbol=self.symbol,
                side=side,
                price=float("nan"),  # market orders don't carry a price
                size=size,
                ts=now,
                owner_id=self.participant_id,
                order_type=OrderType.MARKET,
            )
        else:
            mid = self._last_mid
            offset = mid * (self.limit_price_offset_bps / 10_000.0)
            # BUY limit sits below mid; SELL limit sits above. This keeps
            # the order from crossing the book on entry.
            price = mid - offset if side is OrderSide.BUY else mid + offset
            order = Order(
                order_id=0,
                symbol=self.symbol,
                side=side,
                price=price,
                size=size,
                ts=now,
                owner_id=self.participant_id,
                order_type=OrderType.LIMIT,
            )
        return [order]


__all__ = ["NoiseTrader"]
