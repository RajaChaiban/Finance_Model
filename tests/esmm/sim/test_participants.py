"""Tests for the phase-2 participant archetypes.

Currently covers :class:`~src.esmm.sim.participants.noise.NoiseTrader`.
Subsequent commits add InformedTrader and ReplayTaker coverage.

These participants are pure objects — no kernel wiring — so the tests
exercise them by hand-feeding snapshots and timestamps. Stochastic
checks bound their tolerances generously (and assert determinism under
a fixed seed) so the suite stays stable under CI.
"""

from __future__ import annotations

import math

import pytest

from src.esmm.schemas import OrderBookLevel, OrderBookSnapshot
from src.esmm.sim.lob import Order, OrderSide, OrderType
from src.esmm.sim.participants.base import Participant
from src.esmm.sim.participants.noise import NoiseTrader


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
