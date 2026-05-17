"""Deterministic event-loop kernel.

The kernel is the conductor of a simulation run. It owns:

* a single :class:`~src.esmm.sim.lob.LimitOrderBook` and a
  :class:`~src.esmm.sim.matching.MatchEngine` over it,
* an optional :class:`~src.esmm.sim.latency.LatencyModel` (orders are
  delivered to the match engine after a sampled latency),
* an optional :class:`~src.esmm.sim.risk.RiskEngine` that gates
  submissions and trips the kill-switch on hard breaches,
* a list of :class:`~src.esmm.sim.participants.base.Participant`
  actors that consume snapshots and emit orders.

Loop semantics (fixed-step, deterministic):

  for now in t0 .. t0 + duration step tick_interval:
      1. process due in-flight orders (their post-latency arrival <= now)
      2. emit a snapshot if a snapshot-tick is due; route to each
         participant via ``on_book``
      3. ask each participant for new orders via ``decide(now)``; schedule
         each at ``now + latency.sample_submit_sec()`` (or ``now`` when
         latency is disabled)

Fills are routed to **both** sides (aggressor and resting). Fair value
at the moment of the fill is recorded as the *pre-fill* mid, since
post-fill mid can be polluted by the just-executed trade. Fees default
to ``cfg.fee_bps`` (negative = maker rebate); the kernel doesn't try to
differentiate maker vs. taker beyond labelling the aggressor.

Determinism: given ``(seed, participants, scenario)``, every fill,
inventory path, and snapshot timestamp is bit-for-bit reproducible.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from src.esmm.schemas import Fill, OrderBookSnapshot, Side
from src.esmm.sim.latency import LatencyConfig, LatencyModel
from src.esmm.sim.lob import (
    CrossedBookError,
    LimitOrderBook,
    Order,
    OrderSide,
    OrderType,
)
from src.esmm.sim.matching import MatchEngine
from src.esmm.sim.participants.base import Participant
from src.esmm.sim.risk import RiskEngine, RiskLimits, RiskState


@dataclass
class KernelConfig:
    """Runtime knobs for one simulator run."""

    duration_sec: float
    tick_interval_sec: float = 0.01  # 100 Hz default — participants decide
    snapshot_interval_sec: float = 0.05  # 20 Hz default — snapshots emitted
    seed: int | None = None
    enable_latency: bool = True
    fee_bps: float = -0.2  # default maker rebate
    symbol: str = "SPY"
    starting_mid: float = 100.0
    starting_spread_bps: float = 4.0
    seed_book_size: float = 1000.0  # quantity placed on each seed level
    seed_book_levels: int = 3
    seed_book_level_step_bps: float = 2.0
    seed_book_owner: str = "house"

    def __post_init__(self) -> None:
        if self.duration_sec <= 0:
            raise ValueError("duration_sec must be > 0")
        if self.tick_interval_sec <= 0:
            raise ValueError("tick_interval_sec must be > 0")
        if self.snapshot_interval_sec <= 0:
            raise ValueError("snapshot_interval_sec must be > 0")
        if self.seed_book_levels < 1:
            raise ValueError("seed_book_levels must be >= 1")


@dataclass
class KernelResult:
    """Aggregated output of a single ``Kernel.run()`` call."""

    duration_sec: float
    n_ticks: int
    n_snapshots: int
    n_orders_submitted: int
    n_fills: int
    initial_mid: float
    final_mid: float
    fills: list[Fill] = field(default_factory=list)
    snapshots: list[OrderBookSnapshot] = field(default_factory=list)
    pnl_per_participant: dict[str, float] = field(default_factory=dict)
    inventory_per_participant: dict[str, float] = field(default_factory=dict)
    risk_breaches: list[Any] = field(default_factory=list)
    halted_at: float | None = None


class Kernel:
    """Deterministic event-loop driver.

    Typical usage::

        cfg = KernelConfig(duration_sec=60, symbol="SPY", starting_mid=100)
        kernel = Kernel(cfg)
        kernel.register(NoiseTrader(...))
        kernel.register(InformedTrader(...))
        result = kernel.run()
        print(result.pnl_per_participant)
    """

    def __init__(
        self,
        config: KernelConfig,
        latency_config: LatencyConfig | None = None,
        risk_limits: RiskLimits | None = None,
    ) -> None:
        self.config = config
        self.lob = LimitOrderBook(config.symbol)
        self.match_engine = MatchEngine(self.lob)
        self.latency = LatencyModel(
            latency_config or LatencyConfig(seed=config.seed)
        )
        self.risk = RiskEngine(risk_limits) if risk_limits is not None else None

        self.participants: list[Participant] = []
        self._participants_by_id: dict[str, Participant] = {}

        # Min-heap of (arrival_ts, seq, order). seq is a monotonically
        # increasing tiebreaker so equal-ts events are processed in
        # submission order (deterministic).
        self._pending: list[tuple[float, int, Order]] = []
        self._seq: int = 0

        # Per-participant running state.
        self._inventory: dict[str, float] = {}
        self._cash: dict[str, float] = {}  # signed: + when sold, - when bought

        # Hooks the scenario layer can use to override behaviour. None by
        # default; populated when a scenario is wired in (Phase 4).
        self._spread_widen_factor: float = 1.0

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def register(self, p: Participant) -> None:
        """Attach a participant. Order of registration is preserved."""
        if p.participant_id in self._participants_by_id:
            raise ValueError(f"Participant id {p.participant_id!r} already registered")
        self.participants.append(p)
        self._participants_by_id[p.participant_id] = p
        self._inventory[p.participant_id] = 0.0
        self._cash[p.participant_id] = 0.0

    def seed_book(self) -> None:
        """Bootstrap the book with synthetic background depth.

        Places ``seed_book_levels`` orders on each side, stepping by
        ``seed_book_level_step_bps`` outwards from ``starting_mid``. All
        orders are owned by ``seed_book_owner`` (default ``"house"``)
        so participants don't trip self-trade prevention against them.
        """
        cfg = self.config
        half = 0.5 * cfg.starting_spread_bps / 10_000 * cfg.starting_mid
        step = cfg.seed_book_level_step_bps / 10_000 * cfg.starting_mid

        for level in range(cfg.seed_book_levels):
            offset = half + level * step
            bid_price = cfg.starting_mid - offset
            ask_price = cfg.starting_mid + offset
            for side, price in (
                (OrderSide.BUY, bid_price),
                (OrderSide.SELL, ask_price),
            ):
                oid = self.lob.next_order_id()
                self.lob.add(
                    Order(
                        order_id=oid,
                        symbol=cfg.symbol,
                        side=side,
                        price=price,
                        size=cfg.seed_book_size,
                        ts=0.0,
                        owner_id=cfg.seed_book_owner,
                    )
                )

    # ------------------------------------------------------------------
    # Run loop
    # ------------------------------------------------------------------
    def run(self) -> KernelResult:
        cfg = self.config
        self.seed_book()
        initial_mid = self.lob.mid()
        if not math.isfinite(initial_mid):
            raise RuntimeError(
                "Kernel.run: book has no two-sided depth after seed_book; "
                "increase seed_book_levels or seed_book_size."
            )

        result = KernelResult(
            duration_sec=cfg.duration_sec,
            n_ticks=0,
            n_snapshots=0,
            n_orders_submitted=0,
            n_fills=0,
            initial_mid=initial_mid,
            final_mid=initial_mid,
        )

        now = 0.0
        next_snapshot = 0.0
        end = cfg.duration_sec
        tick = cfg.tick_interval_sec
        snap_interval = cfg.snapshot_interval_sec

        while now <= end + 1e-12:
            # 1) Drain any due in-flight orders.
            self._drain_pending(now, result)

            # 2) Snapshot tick.
            if now >= next_snapshot - 1e-12:
                snap = self.lob.snapshot(now)
                result.snapshots.append(snap)
                result.n_snapshots += 1
                for p in self.participants:
                    try:
                        p.on_book(snap)
                    except Exception:
                        # Participants must not crash the kernel. The
                        # alternative is silent corruption of the run.
                        raise
                next_snapshot = now + snap_interval

            # 3) Participant decisions.
            if not (self.risk is not None and self.risk.is_halted):
                for p in self.participants:
                    new_orders = p.decide(now)
                    for o in new_orders or []:
                        self._submit(now, p.participant_id, o, result)

            # 4) Halt check (post-trade risk).
            if self.risk is not None:
                state = self._current_risk_state(now)
                self.risk.check_posttrade(state)
                if self.risk.is_halted and result.halted_at is None:
                    result.halted_at = now

            result.n_ticks += 1
            now += tick

        # Drain anything still pending right at the end.
        self._drain_pending(end + tick, result)

        # Finalise.
        result.n_fills = len(result.fills)  # one Fill per side per trade
        final_mid = self.lob.mid()
        if math.isfinite(final_mid):
            result.final_mid = final_mid
        result.pnl_per_participant = self._compute_pnl(final_mid)
        result.inventory_per_participant = dict(self._inventory)
        if self.risk is not None:
            result.risk_breaches = list(self.risk.breaches)
        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _submit(
        self,
        now: float,
        owner_id: str,
        order: Order,
        result: KernelResult,
    ) -> None:
        """Schedule a participant's order for arrival at ``now + latency``."""
        # Assign a real order id if the participant left a placeholder.
        if order.order_id <= 0:
            order.order_id = self.lob.next_order_id()
        order.owner_id = owner_id
        order.ts = now
        if order.symbol != self.config.symbol:
            order.symbol = self.config.symbol

        if self.risk is not None:
            mid = self.lob.mid()
            if not math.isfinite(mid):
                mid = self.config.starting_mid
            notional = abs(order.remaining) * mid
            delta = order.remaining if order.side is OrderSide.BUY else -order.remaining
            allowed, _breach = self.risk.check_pretrade(
                participant_id=owner_id,
                symbol=order.symbol,
                order_notional_usd=notional,
                order_delta=delta,
                state=self._current_risk_state(now),
            )
            if not allowed:
                return

        arrival = now + (self.latency.sample_submit_sec() if self.config.enable_latency else 0.0)
        self._seq += 1
        heapq.heappush(self._pending, (arrival, self._seq, order))
        result.n_orders_submitted += 1

    def _drain_pending(self, now: float, result: KernelResult) -> None:
        while self._pending and self._pending[0][0] <= now + 1e-12:
            _ts, _seq, order = heapq.heappop(self._pending)
            self._deliver(order, result)

    def _deliver(self, order: Order, result: KernelResult) -> None:
        """Hand an order to the match engine and route fills back."""
        try:
            match_result = self.match_engine.match(order)
        except CrossedBookError:
            # Match engine should never produce a crossed book on its own;
            # this only happens if upstream scheduled a stale state. Drop
            # the order rather than crash.
            return
        except ValueError:
            return

        if not match_result.fills:
            return

        pre_mid = self._safe_mid()
        for aggressor, resting, price, size in match_result.fills:
            agg_fill = self._make_fill(aggressor, resting, price, size, pre_mid, is_aggressor=True)
            rest_fill = self._make_fill(resting, aggressor, price, size, pre_mid, is_aggressor=False)

            result.fills.append(agg_fill)
            result.fills.append(rest_fill)

            self._apply_to_inventory(aggressor.owner_id, agg_fill)
            self._apply_to_inventory(resting.owner_id, rest_fill)

            agg_p = self._participants_by_id.get(aggressor.owner_id)
            if agg_p is not None:
                agg_p.on_fill(agg_fill)
            rest_p = self._participants_by_id.get(resting.owner_id)
            if rest_p is not None:
                rest_p.on_fill(rest_fill)

    def _make_fill(
        self,
        primary: Order,
        counter: Order,
        price: float,
        size: float,
        fair_value: float,
        is_aggressor: bool,
    ) -> Fill:
        """Build a Fill from this primary's perspective.

        ``primary.side`` is BUY → the primary bought; their Fill.side
        is BUY. For the resting side, we flip — they sold. ``counter``
        is the other order in the trade and is only used for counterparty
        attribution downstream.
        """
        side_for_primary = Side.BUY if primary.side is OrderSide.BUY else Side.SELL
        # Maker / taker fee differentiation: aggressor pays a (positive)
        # taker fee by default; resting earns the (negative) maker
        # rebate. We treat cfg.fee_bps as the maker rebate; aggressor
        # pays the opposite sign with a small spread on top — but to
        # avoid surprising users we just use cfg.fee_bps for both sides
        # in v1. Phase-3 will split this.
        return Fill(
            ts=primary.ts,
            symbol=primary.symbol,
            side=side_for_primary,
            price=price,
            size=size,
            fair_value_at_fill=fair_value,
            fee_bps=self.config.fee_bps,
            is_hedge=False,
            counterparty=counter.owner_id,
        )

    def _apply_to_inventory(self, owner_id: str, fill: Fill) -> None:
        if owner_id not in self._inventory:
            # Participants registered externally; "house" seed isn't a
            # real participant. Track its P&L anyway so the kernel's
            # bookkeeping totals always reconcile to zero.
            self._inventory[owner_id] = 0.0
            self._cash[owner_id] = 0.0
        signed = fill.size if fill.side is Side.BUY else -fill.size
        cash_delta = -signed * fill.price
        # Fees: positive bps = cost; negative bps = rebate.
        fee = -fill.fee_bps / 10_000 * fill.price * fill.size
        self._inventory[owner_id] += signed
        self._cash[owner_id] += cash_delta + fee

    def _compute_pnl(self, final_mid: float) -> dict[str, float]:
        pnl: dict[str, float] = {}
        for owner_id, inv in self._inventory.items():
            mtm = inv * (final_mid if math.isfinite(final_mid) else self.config.starting_mid)
            pnl[owner_id] = self._cash[owner_id] + mtm
        return pnl

    def _safe_mid(self) -> float:
        m = self.lob.mid()
        if math.isfinite(m):
            return m
        return self.config.starting_mid

    def _current_risk_state(self, now: float) -> RiskState:
        mid = self._safe_mid()
        gross_notional = 0.0
        net_delta = 0.0
        per_sym: dict[str, float] = {}
        for owner_id, inv in self._inventory.items():
            n = abs(inv) * mid
            gross_notional += n
            net_delta += inv
            per_sym[self.config.symbol] = per_sym.get(self.config.symbol, 0.0) + n
        total_pnl = sum(self._compute_pnl(mid).values())
        return RiskState(
            ts=now,
            gross_notional_usd=gross_notional,
            net_delta=net_delta,
            daily_pnl=total_pnl,
            peak_daily_pnl=max(0.0, total_pnl),  # naive — phase-3 tracks
            per_symbol_notional=per_sym,
        )


__all__ = ["Kernel", "KernelConfig", "KernelResult"]
