"""Momentum taker — EMA-crossover trend follower.

A taker archetype that maintains two exponentially-weighted moving
averages of the mid (a fast one and a slow one) and aggresses MARKET
orders when their normalised gap clears a configurable threshold. It
generates the **continuation flow** that any market maker has to either
ride or hedge — the kind of flow that piles in after the price has
already started moving.

Design notes:

* **Proper time-weighted EMA.** Because the kernel ticks at irregular
  intervals (especially under historical replay), we can't use the
  fixed-α naive recurrence; that would weight each tick equally
  regardless of dt and produce wildly different EMAs at different tick
  rates. We use ``alpha = 1 - exp(-dt / tau)`` so the effective time
  constant is invariant to the sampling cadence.
* **Threshold gating, not crossover edge.** We check the *current* sign
  of ``(fast - slow) / slow`` against ``threshold_pct``, not "did fast
  cross slow this tick". This is more robust to choppy markets where
  the EMAs whip across each other repeatedly; trading every micro-cross
  burns spread. The threshold turns the taker into a "trade only when
  trend is real" filter.
* **Cooldown.** Once we fire, we sit out ``cooldown_sec`` regardless of
  signal. Without this the participant would machine-gun the LOB the
  instant the threshold is breached.
* **First snapshot seeds both EMAs to the mid.** No "burn-in" period —
  trades the first time the signal clears. Tests rely on this so they
  can construct deterministic uptrend / downtrend snapshots.
* **NaN-safe.** If the snapshot has a half-empty book the EMAs are
  frozen at their last value rather than poisoned with NaN.
"""

from __future__ import annotations

import math
import random
from typing import Optional

from src.esmm.schemas import Fill, OrderBookSnapshot
from src.esmm.sim.lob import Order, OrderSide, OrderType


class MomentumTaker:
    """EMA-crossover momentum taker.

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
        ema_short_sec: float = 10.0,
        ema_long_sec: float = 60.0,
        threshold_pct: float = 0.05,
        lot: int = 500,
        cooldown_sec: float = 5.0,
        seed: Optional[int] = None,
    ) -> None:
        if ema_short_sec <= 0:
            raise ValueError(f"ema_short_sec must be > 0; got {ema_short_sec}")
        if ema_long_sec <= 0:
            raise ValueError(f"ema_long_sec must be > 0; got {ema_long_sec}")
        if ema_short_sec >= ema_long_sec:
            raise ValueError(
                f"ema_short_sec ({ema_short_sec}) must be < ema_long_sec "
                f"({ema_long_sec}) for a meaningful crossover"
            )
        if threshold_pct < 0:
            raise ValueError(f"threshold_pct must be >= 0; got {threshold_pct}")
        if lot <= 0:
            raise ValueError(f"lot must be > 0; got {lot}")
        if cooldown_sec < 0:
            raise ValueError(f"cooldown_sec must be >= 0; got {cooldown_sec}")

        self.participant_id = participant_id
        self.symbol = symbol
        self.ema_short_sec = float(ema_short_sec)
        self.ema_long_sec = float(ema_long_sec)
        self.threshold_pct = float(threshold_pct)
        self.lot = int(lot)
        self.cooldown_sec = float(cooldown_sec)

        # Private RNG — kept even though current decide() is deterministic,
        # so future tie-breaking randomness doesn't perturb the global RNG.
        self._rng = random.Random(seed)

        self._ema_short: Optional[float] = None
        self._ema_long: Optional[float] = None
        self._last_ts: Optional[float] = None
        self._last_order_ts: Optional[float] = None

    # ------------------------------------------------------------------
    # Participant protocol
    # ------------------------------------------------------------------
    def on_book(self, snapshot: OrderBookSnapshot) -> None:
        """Update both EMAs from the snapshot mid.

        Skips when the book is half-empty (mid would be NaN) — we want
        the EMA frozen, not poisoned, across a halt or one-sided book.
        """
        if not snapshot.bids or not snapshot.asks:
            return
        mid = 0.5 * (snapshot.best_bid + snapshot.best_ask)
        if not math.isfinite(mid):
            return

        ts = snapshot.ts
        if self._ema_short is None or self._ema_long is None or self._last_ts is None:
            # First valid snapshot: seed both EMAs to the current mid.
            # This makes the participant immediately responsive to the
            # *next* snapshot rather than needing a burn-in.
            self._ema_short = mid
            self._ema_long = mid
            self._last_ts = ts
            return

        dt = ts - self._last_ts
        if dt <= 0:
            # Same- or earlier-timestamp snapshot: ignore. Replay can
            # produce identical-ts events; we don't want them to wash
            # the EMA toward the new mid with zero weighting.
            return

        alpha_short = 1.0 - math.exp(-dt / self.ema_short_sec)
        alpha_long = 1.0 - math.exp(-dt / self.ema_long_sec)
        self._ema_short = alpha_short * mid + (1.0 - alpha_short) * self._ema_short
        self._ema_long = alpha_long * mid + (1.0 - alpha_long) * self._ema_long
        self._last_ts = ts

    def on_fill(self, fill: Fill) -> None:  # noqa: ARG002
        """Momentum taker carries no inventory state."""
        return

    def decide(self, now: float) -> list[Order]:
        """Emit a MARKET order when the EMA gap clears the threshold."""
        if self._ema_short is None or self._ema_long is None:
            return []
        if self._ema_long <= 0:
            return []

        # Cooldown gate — fires before any signal evaluation so we don't
        # waste CPU on a signal we wouldn't act on anyway.
        if self._last_order_ts is not None:
            if now - self._last_order_ts < self.cooldown_sec:
                return []

        signal = (self._ema_short - self._ema_long) / self._ema_long

        if signal > self.threshold_pct:
            side = OrderSide.BUY
        elif signal < -self.threshold_pct:
            side = OrderSide.SELL
        else:
            return []

        self._last_order_ts = now
        return [
            Order(
                order_id=0,  # placeholder — kernel assigns the real id
                symbol=self.symbol,
                side=side,
                price=float("nan"),  # MARKET
                size=float(self.lot),
                ts=now,
                owner_id=self.participant_id,
                order_type=OrderType.MARKET,
            )
        ]


__all__ = ["MomentumTaker"]
