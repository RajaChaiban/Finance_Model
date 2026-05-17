"""Mean reverter — fades extreme deviations from the rolling mean.

The counterpart to :class:`~src.esmm.sim.participants.momentum.MomentumTaker`.
This participant maintains a rolling window of recent mids, computes a
z-score of the current mid versus that window, and aggresses when the
z-score crosses a threshold — buying dips, selling rips.

It models the **liquidity-seeking** flow a maker actually *wants* to
trade against: orders that arrive when the price has overshot and are
willing to pay the spread to fade it.

Design notes:

* **Rolling window in (ts, mid) tuples**, trimmed by ``window_sec``
  every tick. Not a fixed-N circular buffer, because under historical
  replay the tick rate varies and a fixed-N would give us inconsistent
  effective horizons across regimes.
* **Stdev floor.** A perfectly flat window gives stdev=0 and would
  trigger a div-by-zero. We treat that case as "no signal" — there's
  nothing to fade if nothing's moving.
* **Minimum sample count.** With < 5 points the stdev is noisy enough
  to fire spurious signals during the warm-up. We sit out until the
  window is properly populated.
* **Optional pin mode.** ``pin_strike`` injects an additional pull
  toward a specific level — useful for opex / max-pain scenarios where
  market structure is dragging the price toward a strike. The pin
  contribution is scaled by ``pin_strength_bps`` so it's a *soft* nudge,
  not a hard magnet: a 5 bps pin on a 100-dollar stock barely moves the
  effective z-score until the price is materially away from the strike.
* **Cooldown.** Same rationale as the momentum taker — without it we'd
  fire on every tick while the band is breached.
"""

from __future__ import annotations

import math
import random
import statistics
from collections import deque
from typing import Optional

from src.esmm.schemas import Fill, OrderBookSnapshot
from src.esmm.sim.lob import Order, OrderSide, OrderType


_MIN_SAMPLES = 5


class MeanReverter:
    """Z-score-based mean-reversion taker, with optional pin mode.

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
        window_sec: float = 60.0,
        zscore_threshold: float = 2.0,
        lot: int = 200,
        cooldown_sec: float = 5.0,
        pin_strike: Optional[float] = None,
        pin_strength_bps: float = 5.0,
        seed: Optional[int] = None,
    ) -> None:
        if window_sec <= 0:
            raise ValueError(f"window_sec must be > 0; got {window_sec}")
        if zscore_threshold <= 0:
            raise ValueError(f"zscore_threshold must be > 0; got {zscore_threshold}")
        if lot <= 0:
            raise ValueError(f"lot must be > 0; got {lot}")
        if cooldown_sec < 0:
            raise ValueError(f"cooldown_sec must be >= 0; got {cooldown_sec}")
        if pin_strike is not None and pin_strike <= 0:
            raise ValueError(
                f"pin_strike must be > 0 when set; got {pin_strike}"
            )
        if pin_strength_bps < 0:
            raise ValueError(
                f"pin_strength_bps must be >= 0; got {pin_strength_bps}"
            )

        self.participant_id = participant_id
        self.symbol = symbol
        self.window_sec = float(window_sec)
        self.zscore_threshold = float(zscore_threshold)
        self.lot = int(lot)
        self.cooldown_sec = float(cooldown_sec)
        self.pin_strike = float(pin_strike) if pin_strike is not None else None
        self.pin_strength_bps = float(pin_strength_bps)

        self._rng = random.Random(seed)
        self._window: deque[tuple[float, float]] = deque()
        self._last_mid: Optional[float] = None
        self._last_order_ts: Optional[float] = None

    # ------------------------------------------------------------------
    # Participant protocol
    # ------------------------------------------------------------------
    def on_book(self, snapshot: OrderBookSnapshot) -> None:
        """Append (ts, mid) to the window and trim by ``window_sec``."""
        if not snapshot.bids or not snapshot.asks:
            return
        mid = 0.5 * (snapshot.best_bid + snapshot.best_ask)
        if not math.isfinite(mid):
            return
        ts = snapshot.ts
        self._window.append((ts, mid))
        self._last_mid = mid
        self._trim_window(ts)

    def on_fill(self, fill: Fill) -> None:  # noqa: ARG002
        """Mean reverter is stateless w.r.t. its own fills."""
        return

    def decide(self, now: float) -> list[Order]:
        """Emit a MARKET order when the effective z-score clears threshold."""
        # Trim again — the window may have aged out since the last book.
        self._trim_window(now)

        if self._last_mid is None:
            return []
        if len(self._window) < _MIN_SAMPLES:
            return []

        if self._last_order_ts is not None:
            if now - self._last_order_ts < self.cooldown_sec:
                return []

        mids = [m for _, m in self._window]
        mean = statistics.fmean(mids)
        try:
            stdev = statistics.stdev(mids)
        except statistics.StatisticsError:
            # Only fires if len < 2; we've already guarded that, but be safe.
            return []
        if stdev <= 0 or not math.isfinite(stdev):
            # Perfectly flat window → nothing to fade.
            return []

        current_mid = self._last_mid
        z = (current_mid - mean) / stdev

        # Optional pin-strike pull. Re-expressing the pin term in
        # z-score units lets us combine it linearly with the rolling z
        # without unit gymnastics. The weight is bps → fraction (/1e4).
        if self.pin_strike is not None and mean > 0:
            pin_delta = (current_mid - self.pin_strike) / mean
            z_pin = pin_delta * (self.pin_strength_bps / 10_000.0) / (stdev / mean)
            # Equivalent to ((current_mid - pin) / stdev) * (bps / 1e4) —
            # but we keep the explicit / mean form to mirror the spec's
            # "weighted by pin_strength_bps / 10000" wording.
            effective_z = z + z_pin
        else:
            effective_z = z

        if effective_z > self.zscore_threshold:
            side = OrderSide.SELL  # price too high → fade by selling
        elif effective_z < -self.zscore_threshold:
            side = OrderSide.BUY  # price too low → buy the dip
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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _trim_window(self, now: float) -> None:
        """Drop (ts, mid) entries older than ``window_sec`` from the front."""
        cutoff = now - self.window_sec
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()


__all__ = ["MeanReverter"]
