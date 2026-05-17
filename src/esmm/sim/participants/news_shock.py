"""News-shock — scripted regime events as flow.

In an ideal world we'd inject regime events (gap, halt, vol spike,
spread widen) directly into the kernel as metadata. Phase-4 v1 of the
simulator doesn't yet expose that injection surface, so we model news
as **aggressive market-order flow** wrapped in a participant. That
captures the gap / news-print events realistically (they really do show
up as bursts of market orders) and lets the more abstract events
(halt / vol_spike / spread_widen) sit as silent placeholders we can
upgrade once the kernel grows the right hook.

Event grammar — each event is a dict with three keys:

* ``ts_offset_sec`` (float): when the event fires, in seconds relative
  to the start of the run (whatever ``t=0`` means to the kernel).
* ``kind`` (str): one of the supported kinds below.
* ``params`` (dict): kind-specific parameters.

Supported kinds:

* ``"gap"``  — params: ``{"pct_move": float, "jump_sec": float}``.
  Over a ``jump_sec`` window starting at ``ts_offset_sec`` we emit ~10
  evenly-spaced MARKET orders in the direction of ``pct_move`` (BUY for
  positive, SELL for negative). Sizing heuristic: each sub-order is
  ``abs(pct_move) * mid * 100`` shares — large enough to walk the book
  several levels but not absurd. There is no market-impact model in
  Phase-4 v1, so we calibrated by sweeping the heuristic against the
  matching engine until a -6% gap actually walked the mid ~3-5% with a
  populated noise book; the exact number is not load-bearing because
  the maker reacts to the *direction* of the shock, not the magnitude.
* ``"news_print"`` — same shape as ``"gap"``, smaller in magnitude
  and faster in duration. Models a single-headline burst.
* ``"halt"`` — params: ``{"duration_sec": float}``. We emit nothing
  for the duration. The LOB is not actually halted (that would require
  kernel cooperation) — other participants continue. Treat this as a
  best-effort marker until the kernel grows a real halt hook.
* ``"vol_spike"`` — no orders emitted. Documented limitation: in v1 we
  can't change other participants' volatility from a single participant,
  so this is a metadata-only event. Kept in the schema so scenario
  configs are forward-compatible.
* ``"spread_widen"`` — same as ``"vol_spike"``: no orders emitted.

Internal cursor semantics mirror :class:`ReplayTaker`: every (event,
sub-order) is fired at most once; we track ``_emitted_count`` per event
so re-entering ``decide`` during the same window doesn't double-fire.
"""

from __future__ import annotations

import math
from typing import Optional

from src.esmm.schemas import Fill, OrderBookSnapshot
from src.esmm.sim.lob import Order, OrderSide, OrderType


_FLOW_KINDS = frozenset({"gap", "news_print"})
_SILENT_KINDS = frozenset({"halt", "vol_spike", "spread_widen"})
_SUPPORTED_KINDS = _FLOW_KINDS | _SILENT_KINDS


class _ScheduledEvent:
    """Internal mutable wrapper around a user-supplied event dict.

    We unpack + validate up-front so the hot path of ``decide`` is just
    arithmetic and comparisons.
    """

    __slots__ = (
        "ts_offset_sec",
        "kind",
        "pct_move",
        "jump_sec",
        "duration_sec",
        "n_sub_orders",
        "emitted_count",
    )

    def __init__(
        self,
        *,
        ts_offset_sec: float,
        kind: str,
        pct_move: float,
        jump_sec: float,
        duration_sec: float,
        n_sub_orders: int,
    ) -> None:
        self.ts_offset_sec = ts_offset_sec
        self.kind = kind
        self.pct_move = pct_move
        self.jump_sec = jump_sec
        self.duration_sec = duration_sec
        self.n_sub_orders = n_sub_orders
        self.emitted_count = 0


class NewsShock:
    """Scripted news / shock event generator.

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
        events: list[dict],
    ) -> None:
        self.participant_id = participant_id
        self.symbol = symbol

        # Validate + normalise everything at construction time. A bad
        # scenario file should fail loudly *before* the simulator starts
        # consuming wall-clock time.
        validated: list[_ScheduledEvent] = []
        for i, ev in enumerate(events):
            if not isinstance(ev, dict):
                raise ValueError(f"events[{i}] must be a dict; got {type(ev).__name__}")
            try:
                ts_offset_sec = float(ev["ts_offset_sec"])
                kind = ev["kind"]
            except KeyError as exc:
                raise ValueError(
                    f"events[{i}] missing required key {exc.args[0]!r}"
                ) from exc
            if not isinstance(kind, str) or kind not in _SUPPORTED_KINDS:
                raise ValueError(
                    f"events[{i}].kind must be one of {sorted(_SUPPORTED_KINDS)}; "
                    f"got {kind!r}"
                )
            if ts_offset_sec < 0:
                raise ValueError(
                    f"events[{i}].ts_offset_sec must be >= 0; got {ts_offset_sec}"
                )
            params = ev.get("params", {}) or {}
            if not isinstance(params, dict):
                raise ValueError(
                    f"events[{i}].params must be a dict; got {type(params).__name__}"
                )

            pct_move = 0.0
            jump_sec = 0.0
            duration_sec = 0.0
            n_sub_orders = 0

            if kind in _FLOW_KINDS:
                pct_move = float(params.get("pct_move", 0.0))
                jump_sec = float(params.get("jump_sec", 1.0))
                if jump_sec <= 0:
                    raise ValueError(
                        f"events[{i}].params.jump_sec must be > 0; got {jump_sec}"
                    )
                # ~10 sub-orders evenly spaced — but never fewer than 1
                # (sub-second jumps would otherwise emit nothing).
                n_sub_orders = max(1, 10)
            elif kind == "halt":
                duration_sec = float(params.get("duration_sec", 0.0))
                if duration_sec < 0:
                    raise ValueError(
                        f"events[{i}].params.duration_sec must be >= 0; "
                        f"got {duration_sec}"
                    )
            # vol_spike / spread_widen: nothing to unpack (no flow emitted).

            validated.append(
                _ScheduledEvent(
                    ts_offset_sec=ts_offset_sec,
                    kind=kind,
                    pct_move=pct_move,
                    jump_sec=jump_sec,
                    duration_sec=duration_sec,
                    n_sub_orders=n_sub_orders,
                )
            )

        self._events = validated
        self._last_mid: Optional[float] = None

    # ------------------------------------------------------------------
    # Participant protocol
    # ------------------------------------------------------------------
    def on_book(self, snapshot: OrderBookSnapshot) -> None:
        """Cache the latest mid for sub-order sizing."""
        if not snapshot.bids or not snapshot.asks:
            return
        mid = 0.5 * (snapshot.best_bid + snapshot.best_ask)
        if not math.isfinite(mid):
            return
        self._last_mid = mid

    def on_fill(self, fill: Fill) -> None:  # noqa: ARG002
        """News shock is fire-and-forget; fills don't matter."""
        return

    def decide(self, now: float) -> list[Order]:
        """Emit any scheduled orders whose sub-window has been crossed."""
        if self._last_mid is None:
            # No mid yet → can't size flow orders. Silent kinds also
            # short-circuit since they emit nothing anyway.
            return []

        out: list[Order] = []
        for ev in self._events:
            if ev.kind not in _FLOW_KINDS:
                # halt / vol_spike / spread_widen: emit nothing in v1.
                continue
            if now < ev.ts_offset_sec:
                continue
            if ev.emitted_count >= ev.n_sub_orders:
                continue

            # How many sub-orders should have fired by ``now``?
            elapsed = now - ev.ts_offset_sec
            interval = ev.jump_sec / ev.n_sub_orders
            # +1 because the first sub-order fires *at* ts_offset_sec
            # (elapsed = 0), not after one interval.
            target = min(ev.n_sub_orders, 1 + int(elapsed / interval) if interval > 0 else ev.n_sub_orders)
            target = max(target, 0)

            while ev.emitted_count < target:
                side = OrderSide.BUY if ev.pct_move > 0 else OrderSide.SELL
                # Heuristic size — see module docstring for calibration
                # rationale. Clamp to a sensible minimum so a tiny
                # pct_move still produces a non-zero order.
                size = max(1.0, abs(ev.pct_move) * self._last_mid * 100.0)
                out.append(
                    Order(
                        order_id=0,  # placeholder
                        symbol=self.symbol,
                        side=side,
                        price=float("nan"),  # MARKET
                        size=size,
                        ts=now,
                        owner_id=self.participant_id,
                        order_type=OrderType.MARKET,
                    )
                )
                ev.emitted_count += 1
        return out


__all__ = ["NewsShock"]
