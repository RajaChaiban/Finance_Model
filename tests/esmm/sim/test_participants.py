"""Tests for the three phase-2 participant archetypes.

Covers :class:`~src.esmm.sim.participants.noise.NoiseTrader`,
:class:`~src.esmm.sim.participants.informed.InformedTrader`, and
:class:`~src.esmm.sim.participants.replay_taker.ReplayTaker`.

These participants are pure objects — no kernel wiring — so the tests
exercise them by hand-feeding snapshots and timestamps. Stochastic
checks bound their tolerances generously (and assert determinism under
a fixed seed) so the suite stays stable under CI.
"""

from __future__ import annotations

import math
from typing import Callable

import pytest

from src.esmm.schemas import OrderBookLevel, OrderBookSnapshot
from src.esmm.sim.lob import Order, OrderSide, OrderType
from src.esmm.sim.participants.base import Participant
from src.esmm.sim.participants.informed import InformedTrader
from src.esmm.sim.participants.noise import NoiseTrader
from src.esmm.sim.participants.replay_taker import ReplayTaker


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _snap(
    *,
    ts: float = 0.0,
    symbol: str = "SPY",
    bid: float = 99.95,
    ask: float = 100.05,
    bid_size: float = 1000.0,
    ask_size: float = 1000.0,
) -> OrderBookSnapshot:
    """Two-sided snapshot helper."""
    return OrderBookSnapshot(
        ts=ts,
        symbol=symbol,
        bids=[OrderBookLevel(price=bid, size=bid_size)],
        asks=[OrderBookLevel(price=ask, size=ask_size)],
    )


def _half_snap(
    *, ts: float = 0.0, symbol: str = "SPY", side: str = "bid"
) -> OrderBookSnapshot:
    """One-sided snapshot — only valid via model_construct."""
    if side == "bid":
        return OrderBookSnapshot.model_construct(
            ts=ts,
            symbol=symbol,
            bids=[OrderBookLevel(price=99.95, size=100.0)],
            asks=[],
        )
    return OrderBookSnapshot.model_construct(
        ts=ts,
        symbol=symbol,
        bids=[],
        asks=[OrderBookLevel(price=100.05, size=100.0)],
    )


# =====================================================================
# Protocol conformance
# =====================================================================
def test_noise_trader_is_a_participant() -> None:
    nt = NoiseTrader("noise1", "SPY", seed=0)
    assert isinstance(nt, Participant)


def test_informed_trader_is_a_participant() -> None:
    it = InformedTrader("inf1", "SPY", future_mid_provider=lambda t: 100.0, seed=0)
    assert isinstance(it, Participant)


def test_replay_taker_is_a_participant() -> None:
    rt = ReplayTaker("rep1", "SPY", events=[])
    assert isinstance(rt, Participant)


# =====================================================================
# NoiseTrader
# =====================================================================
def _drive_noise(nt: NoiseTrader, *, n_ticks: int, dt: float) -> list[Order]:
    """Feed a steady stream of snapshots/ticks and collect emitted orders."""
    nt.on_book(_snap(ts=0.0))
    out: list[Order] = []
    for i in range(n_ticks):
        now = (i + 1) * dt
        nt.on_book(_snap(ts=now))
        out.extend(nt.decide(now))
    return out


def test_noise_trader_determinism_under_seed() -> None:
    """Same seed → identical order stream."""
    a = NoiseTrader("a", "SPY", arrival_rate_hz=5.0, seed=42)
    b = NoiseTrader("b", "SPY", arrival_rate_hz=5.0, seed=42)
    out_a = _drive_noise(a, n_ticks=200, dt=0.01)
    out_b = _drive_noise(b, n_ticks=200, dt=0.01)
    assert len(out_a) == len(out_b)
    for oa, ob in zip(out_a, out_b):
        assert oa.side == ob.side
        assert oa.size == ob.size
        assert oa.order_type == ob.order_type
        if oa.order_type == OrderType.MARKET:
            assert math.isnan(oa.price) and math.isnan(ob.price)
        else:
            assert oa.price == pytest.approx(ob.price)


def test_noise_trader_arrival_rate_approximately_correct() -> None:
    """Bernoulli-per-tick arrival should land within ±30% of target."""
    rate = 5.0  # Hz
    dt = 0.01  # 100 Hz ticks → rate*dt = 0.05 « 1 (safe regime)
    duration = 10.0
    n_ticks = int(duration / dt)
    nt = NoiseTrader("nt", "SPY", arrival_rate_hz=rate, seed=7)
    orders = _drive_noise(nt, n_ticks=n_ticks, dt=dt)
    expected = rate * duration  # ≈ 50
    assert 0.7 * expected <= len(orders) <= 1.3 * expected, (
        f"expected ≈{expected} orders, got {len(orders)}"
    )


def test_noise_trader_no_orders_when_mid_is_none() -> None:
    """Half-empty book → no orders, ever."""
    nt = NoiseTrader("nt", "SPY", arrival_rate_hz=100.0, seed=1)
    nt.on_book(_half_snap(side="bid"))
    out: list[Order] = []
    for i in range(200):
        now = (i + 1) * 0.01
        nt.on_book(_half_snap(ts=now, side="bid"))
        out.extend(nt.decide(now))
    assert out == []


def test_noise_trader_aggressive_pct_distribution() -> None:
    """Empirical aggressive fraction should track aggressive_pct."""
    nt = NoiseTrader(
        "nt", "SPY", arrival_rate_hz=50.0, aggressive_pct=0.7, seed=3
    )
    out = _drive_noise(nt, n_ticks=2000, dt=0.01)
    assert len(out) > 100
    market = sum(1 for o in out if o.order_type == OrderType.MARKET)
    frac = market / len(out)
    assert 0.55 <= frac <= 0.85, f"expected ~0.7 aggressive, got {frac:.3f}"


def test_noise_trader_lot_size_within_bounds() -> None:
    """Every emitted size is in [lot_min, lot_max]."""
    nt = NoiseTrader(
        "nt", "SPY", arrival_rate_hz=20.0, lot_min=100, lot_max=500, seed=5
    )
    out = _drive_noise(nt, n_ticks=2000, dt=0.01)
    assert out
    for o in out:
        assert 100 <= o.size <= 500


def test_noise_trader_side_balance_approximately_5050() -> None:
    """Side fraction should be ~0.5 over a large sample."""
    nt = NoiseTrader("nt", "SPY", arrival_rate_hz=50.0, seed=11)
    out = _drive_noise(nt, n_ticks=3000, dt=0.01)
    assert len(out) >= 500
    buys = sum(1 for o in out if o.side == OrderSide.BUY)
    frac = buys / len(out)
    assert 0.4 <= frac <= 0.6, f"expected ~0.5 buys, got {frac:.3f}"


def test_noise_trader_limit_price_respects_offset_and_side() -> None:
    """Limit BUY < mid, limit SELL > mid, by the configured offset (bps)."""
    nt = NoiseTrader(
        "nt",
        "SPY",
        arrival_rate_hz=50.0,
        aggressive_pct=0.0,
        limit_price_offset_bps=2.0,
        seed=13,
    )
    nt.on_book(_snap(ts=0.0, bid=99.95, ask=100.05))  # mid = 100.0
    out: list[Order] = []
    for i in range(200):
        now = (i + 1) * 0.01
        nt.on_book(_snap(ts=now, bid=99.95, ask=100.05))
        out.extend(nt.decide(now))
    assert out
    for o in out:
        assert o.order_type == OrderType.LIMIT
        if o.side == OrderSide.BUY:
            assert o.price == pytest.approx(100.0 * (1 - 2e-4), abs=1e-9)
        else:
            assert o.price == pytest.approx(100.0 * (1 + 2e-4), abs=1e-9)


def test_noise_trader_rejects_bad_config() -> None:
    """Bad inputs raise at construction time."""
    with pytest.raises(ValueError):
        NoiseTrader("x", "SPY", arrival_rate_hz=-1.0)
    with pytest.raises(ValueError):
        NoiseTrader("x", "SPY", lot_min=0)
    with pytest.raises(ValueError):
        NoiseTrader("x", "SPY", lot_min=500, lot_max=100)
    with pytest.raises(ValueError):
        NoiseTrader("x", "SPY", aggressive_pct=1.5)
    with pytest.raises(ValueError):
        NoiseTrader("x", "SPY", limit_price_offset_bps=-1.0)


def test_noise_trader_orders_are_well_formed() -> None:
    """Emitted orders carry the right owner_id, symbol, and placeholder id."""
    nt = NoiseTrader("noise-X", "AAPL", arrival_rate_hz=50.0, seed=21)
    nt.on_book(_snap(ts=0.0, symbol="AAPL"))
    nt.decide(0.01)  # prime
    out: list[Order] = []
    for i in range(200):
        now = (i + 2) * 0.01
        nt.on_book(_snap(ts=now, symbol="AAPL"))
        out.extend(nt.decide(now))
    assert out
    for o in out:
        assert o.owner_id == "noise-X"
        assert o.symbol == "AAPL"
        assert o.order_id == 0
        assert o.order_type in (OrderType.MARKET, OrderType.LIMIT)
        if o.order_type == OrderType.MARKET:
            assert math.isnan(o.price)
        else:
            assert math.isfinite(o.price)


# =====================================================================
# InformedTrader
# =====================================================================
def _const_future(price: float) -> Callable[[float], float]:
    return lambda _t: price


def test_informed_trader_buys_when_future_above_current() -> None:
    """Large positive edge → MARKET BUY of size ``lot``."""
    it = InformedTrader(
        "inf",
        "SPY",
        future_mid_provider=_const_future(101.0),  # +100 bps vs 100.0
        edge_threshold_bps=5.0,
        lot=500,
        signal_noise_bps=0.0,
        seed=1,
    )
    it.on_book(_snap(ts=0.0, bid=99.95, ask=100.05))
    orders = it.decide(0.1)
    assert len(orders) == 1
    o = orders[0]
    assert o.side == OrderSide.BUY
    assert o.order_type == OrderType.MARKET
    assert o.size == 500
    assert math.isnan(o.price)
    assert o.owner_id == "inf"
    assert o.order_id == 0


def test_informed_trader_sells_when_future_below_current() -> None:
    """Large negative edge → MARKET SELL."""
    it = InformedTrader(
        "inf",
        "SPY",
        future_mid_provider=_const_future(99.0),  # -100 bps vs 100.0
        edge_threshold_bps=5.0,
        lot=500,
        signal_noise_bps=0.0,
    )
    it.on_book(_snap(ts=0.0, bid=99.95, ask=100.05))
    orders = it.decide(0.1)
    assert len(orders) == 1
    assert orders[0].side == OrderSide.SELL
    assert orders[0].order_type == OrderType.MARKET


def test_informed_trader_silent_when_edge_below_threshold() -> None:
    """Within threshold → no order."""
    # mid=100.0, future=100.02 → edge=2bps; threshold=5bps → silent.
    it = InformedTrader(
        "inf",
        "SPY",
        future_mid_provider=_const_future(100.02),
        edge_threshold_bps=5.0,
        signal_noise_bps=0.0,
    )
    it.on_book(_snap(ts=0.0, bid=99.95, ask=100.05))
    assert it.decide(0.1) == []


def test_informed_trader_perfect_predictor_with_zero_noise() -> None:
    """signal_noise=0 → exact sign of edge drives the decision."""
    it_buy = InformedTrader(
        "i1",
        "SPY",
        future_mid_provider=_const_future(100.0 * (1 + 5.1e-4)),
        edge_threshold_bps=5.0,
        signal_noise_bps=0.0,
    )
    it_buy.on_book(_snap(ts=0.0, bid=99.95, ask=100.05))
    assert len(it_buy.decide(0.1)) == 1

    it_silent = InformedTrader(
        "i2",
        "SPY",
        future_mid_provider=_const_future(100.0 * (1 + 4.9e-4)),
        edge_threshold_bps=5.0,
        signal_noise_bps=0.0,
    )
    it_silent.on_book(_snap(ts=0.0, bid=99.95, ask=100.05))
    assert it_silent.decide(0.1) == []


def test_informed_trader_no_orders_when_mid_is_none() -> None:
    """Half-empty book → no orders."""
    it = InformedTrader(
        "inf",
        "SPY",
        future_mid_provider=_const_future(120.0),  # huge edge if it had a mid
        edge_threshold_bps=5.0,
        signal_noise_bps=0.0,
    )
    it.on_book(_half_snap(side="bid"))
    assert it.decide(0.1) == []
    it.on_book(_half_snap(side="ask"))
    assert it.decide(0.2) == []


def test_informed_trader_determinism_under_seed() -> None:
    """Same seed + same signal stream → identical orders."""
    def fut(_t: float) -> float:
        return 100.05  # 5 bps edge: right at threshold → noise decides

    a = InformedTrader(
        "a",
        "SPY",
        future_mid_provider=fut,
        edge_threshold_bps=5.0,
        signal_noise_bps=3.0,
        seed=99,
    )
    b = InformedTrader(
        "b",
        "SPY",
        future_mid_provider=fut,
        edge_threshold_bps=5.0,
        signal_noise_bps=3.0,
        seed=99,
    )
    out_a: list[Order] = []
    out_b: list[Order] = []
    for i in range(50):
        ts = (i + 1) * 0.1
        a.on_book(_snap(ts=ts, bid=99.95, ask=100.05))
        b.on_book(_snap(ts=ts, bid=99.95, ask=100.05))
        out_a.extend(a.decide(ts))
        out_b.extend(b.decide(ts))
    assert len(out_a) == len(out_b)
    for oa, ob in zip(out_a, out_b):
        assert oa.side == ob.side
        assert oa.size == ob.size


def test_informed_trader_rejects_bad_config() -> None:
    with pytest.raises(ValueError):
        InformedTrader("x", "SPY", future_mid_provider=lambda t: 100.0, lookahead_sec=-1)
    with pytest.raises(ValueError):
        InformedTrader(
            "x", "SPY", future_mid_provider=lambda t: 100.0, edge_threshold_bps=-1
        )
    with pytest.raises(ValueError):
        InformedTrader("x", "SPY", future_mid_provider=lambda t: 100.0, lot=0)
    with pytest.raises(ValueError):
        InformedTrader(
            "x", "SPY", future_mid_provider=lambda t: 100.0, signal_noise_bps=-1.0
        )


# =====================================================================
# ReplayTaker
# =====================================================================
def test_replay_taker_emits_events_in_order() -> None:
    """Walking time forward one event at a time emits each in turn."""
    events = [(0.1, "buy", 100.0), (0.2, "sell", 200.0), (0.3, "buy", 50.0)]
    rt = ReplayTaker("rep", "SPY", events=events)

    out = rt.decide(0.05)
    assert out == []  # nothing has triggered yet

    out = rt.decide(0.1)
    assert len(out) == 1
    assert out[0].side == OrderSide.BUY
    assert out[0].size == 100.0
    assert out[0].order_type == OrderType.MARKET
    assert math.isnan(out[0].price)
    assert out[0].owner_id == "rep"
    assert out[0].symbol == "SPY"
    assert out[0].order_id == 0
    assert out[0].ts == 0.1

    out = rt.decide(0.2)
    assert len(out) == 1
    assert out[0].side == OrderSide.SELL
    assert out[0].size == 200.0

    out = rt.decide(0.3)
    assert len(out) == 1
    assert out[0].side == OrderSide.BUY
    assert out[0].size == 50.0

    # Nothing left.
    assert rt.decide(1.0) == []


def test_replay_taker_flushes_multiple_events_on_jump() -> None:
    """A big time-jump emits every event whose ts <= now in one call."""
    events = [
        (0.1, "buy", 10.0),
        (0.2, "sell", 20.0),
        (0.3, "buy", 30.0),
        (0.4, "sell", 40.0),
    ]
    rt = ReplayTaker("rep", "SPY", events=events)
    out = rt.decide(0.35)
    assert [o.size for o in out] == [10.0, 20.0, 30.0]
    assert [o.side for o in out] == [
        OrderSide.BUY,
        OrderSide.SELL,
        OrderSide.BUY,
    ]
    # The last event still pending.
    remaining = rt.decide(0.5)
    assert len(remaining) == 1
    assert remaining[0].size == 40.0


def test_replay_taker_never_reemits() -> None:
    """Calling decide multiple times at the same ts emits each event exactly once."""
    events = [(0.1, "buy", 10.0), (0.2, "sell", 20.0)]
    rt = ReplayTaker("rep", "SPY", events=events)
    first = rt.decide(1.0)
    assert len(first) == 2
    # Subsequent calls — even further in the future — emit nothing.
    assert rt.decide(2.0) == []
    assert rt.decide(10.0) == []


def test_replay_taker_unsorted_raises() -> None:
    """Constructor refuses out-of-order tapes."""
    with pytest.raises(ValueError, match="sorted"):
        ReplayTaker(
            "rep",
            "SPY",
            events=[(0.1, "buy", 10.0), (0.05, "sell", 5.0)],
        )


def test_replay_taker_rejects_bad_side() -> None:
    with pytest.raises(ValueError, match="buy"):
        ReplayTaker("rep", "SPY", events=[(0.1, "hodl", 10.0)])


def test_replay_taker_rejects_bad_size() -> None:
    with pytest.raises(ValueError, match="size"):
        ReplayTaker("rep", "SPY", events=[(0.1, "buy", 0.0)])
    with pytest.raises(ValueError, match="size"):
        ReplayTaker("rep", "SPY", events=[(0.1, "buy", -1.0)])


def test_replay_taker_handles_empty_tape() -> None:
    """No events → decide always returns []."""
    rt = ReplayTaker("rep", "SPY", events=[])
    assert rt.decide(0.0) == []
    assert rt.decide(100.0) == []


def test_replay_taker_on_book_and_on_fill_are_noops() -> None:
    """These methods exist (protocol) but don't change behaviour."""
    from src.esmm.schemas import Fill, Side

    rt = ReplayTaker("rep", "SPY", events=[(0.1, "buy", 10.0)])
    rt.on_book(_snap(ts=0.05))
    rt.on_fill(
        Fill(
            ts=0.05,
            symbol="SPY",
            side=Side.BUY,
            price=100.0,
            size=10.0,
            fair_value_at_fill=100.0,
        )
    )
    out = rt.decide(0.1)
    assert len(out) == 1
