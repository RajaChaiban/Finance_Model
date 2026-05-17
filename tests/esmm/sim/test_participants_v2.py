"""Tests for the phase-4 participant archetypes.

Covers :class:`~src.esmm.sim.participants.momentum.MomentumTaker`,
:class:`~src.esmm.sim.participants.mean_reverter.MeanReverter`, and
:class:`~src.esmm.sim.participants.news_shock.NewsShock`.

As with the phase-2 participant tests, these objects are pure — no
kernel wiring — so we feed hand-built snapshots and timestamps and
assert on the orders that come back. Stochastic checks use fixed seeds
and bound tolerances generously to keep CI stable.
"""

from __future__ import annotations

import math

import pytest

from src.esmm.schemas import OrderBookLevel, OrderBookSnapshot
from src.esmm.sim.lob import Order, OrderSide, OrderType
from src.esmm.sim.participants.base import Participant
from src.esmm.sim.participants.mean_reverter import MeanReverter
from src.esmm.sim.participants.momentum import MomentumTaker
from src.esmm.sim.participants.news_shock import NewsShock


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
    return OrderBookSnapshot(
        ts=ts,
        symbol=symbol,
        bids=[OrderBookLevel(price=bid, size=bid_size)],
        asks=[OrderBookLevel(price=ask, size=ask_size)],
    )


def _snap_mid(ts: float, mid: float, *, symbol: str = "SPY", half_spread: float = 0.05) -> OrderBookSnapshot:
    return _snap(
        ts=ts, symbol=symbol, bid=mid - half_spread, ask=mid + half_spread
    )


def _half_snap(*, ts: float = 0.0, symbol: str = "SPY") -> OrderBookSnapshot:
    return OrderBookSnapshot.model_construct(
        ts=ts,
        symbol=symbol,
        bids=[OrderBookLevel(price=99.95, size=100.0)],
        asks=[],
    )


# =====================================================================
# Protocol conformance
# =====================================================================
def test_momentum_taker_is_a_participant() -> None:
    mt = MomentumTaker("m1", "SPY", seed=0)
    assert isinstance(mt, Participant)


def test_mean_reverter_is_a_participant() -> None:
    mr = MeanReverter("mr1", "SPY", seed=0)
    assert isinstance(mr, Participant)


def test_news_shock_is_a_participant() -> None:
    ns = NewsShock("ns1", "SPY", events=[])
    assert isinstance(ns, Participant)


# =====================================================================
# MomentumTaker
# =====================================================================
def test_momentum_taker_ema_converges_to_steady_mid() -> None:
    """Feeding a constant mid should drive both EMAs to that mid."""
    mt = MomentumTaker(
        "m",
        "SPY",
        ema_short_sec=1.0,
        ema_long_sec=5.0,
        threshold_pct=0.001,
        cooldown_sec=0.0,
    )
    # Many ticks at a constant mid of 100.
    for i in range(500):
        mt.on_book(_snap_mid(ts=i * 0.1, mid=100.0))
    # No signal should fire (gap is zero).
    assert mt.decide(50.0) == []
    # Internal EMAs should be essentially equal to the steady mid.
    assert mt._ema_short == pytest.approx(100.0, abs=1e-6)
    assert mt._ema_long == pytest.approx(100.0, abs=1e-6)


def test_momentum_taker_buys_on_uptrend() -> None:
    """A sustained uptrend should drive fast > slow > threshold → BUY."""
    mt = MomentumTaker(
        "m",
        "SPY",
        ema_short_sec=1.0,
        ema_long_sec=10.0,
        threshold_pct=0.005,  # 0.5%
        lot=300,
        cooldown_sec=0.0,
    )
    # 200 ticks rising from 100 to 110 (~10% rise).
    out: list[Order] = []
    for i in range(200):
        ts = i * 0.1
        mid = 100.0 + 0.05 * i
        mt.on_book(_snap_mid(ts=ts, mid=mid))
        out.extend(mt.decide(ts))
    assert out, "expected at least one BUY during the uptrend"
    assert all(o.side == OrderSide.BUY for o in out)
    assert all(o.order_type == OrderType.MARKET for o in out)
    assert all(o.size == 300.0 for o in out)
    assert all(o.owner_id == "m" for o in out)


def test_momentum_taker_sells_on_downtrend() -> None:
    """A sustained downtrend should fire SELL orders."""
    mt = MomentumTaker(
        "m",
        "SPY",
        ema_short_sec=1.0,
        ema_long_sec=10.0,
        threshold_pct=0.005,
        cooldown_sec=0.0,
    )
    out: list[Order] = []
    for i in range(200):
        ts = i * 0.1
        mid = 100.0 - 0.05 * i
        mt.on_book(_snap_mid(ts=ts, mid=mid))
        out.extend(mt.decide(ts))
    assert out, "expected at least one SELL during the downtrend"
    assert all(o.side == OrderSide.SELL for o in out)


def test_momentum_taker_silent_in_flat_market() -> None:
    """No trend → no orders, even with permissive threshold."""
    mt = MomentumTaker(
        "m",
        "SPY",
        ema_short_sec=1.0,
        ema_long_sec=5.0,
        threshold_pct=0.001,
        cooldown_sec=0.0,
    )
    out: list[Order] = []
    for i in range(500):
        ts = i * 0.1
        mt.on_book(_snap_mid(ts=ts, mid=100.0))
        out.extend(mt.decide(ts))
    assert out == []


def test_momentum_taker_cooldown_enforced() -> None:
    """After firing, no further orders until cooldown elapses."""
    mt = MomentumTaker(
        "m",
        "SPY",
        ema_short_sec=1.0,
        ema_long_sec=10.0,
        threshold_pct=0.001,  # very low → trips fast
        cooldown_sec=2.0,
    )
    out: list[Order] = []
    # Build a strong uptrend that will keep firing without the cooldown.
    for i in range(100):
        ts = i * 0.1  # 0.0 .. 9.9
        mid = 100.0 + 0.1 * i
        mt.on_book(_snap_mid(ts=ts, mid=mid))
        out.extend(mt.decide(ts))
    assert out, "needed at least one fire to exercise the cooldown"
    # Between any two consecutive fires the gap must be >= cooldown.
    timestamps = [o.ts for o in out]
    gaps = [b - a for a, b in zip(timestamps, timestamps[1:])]
    assert all(g >= 2.0 - 1e-9 for g in gaps), (
        f"cooldown violated; gaps={gaps}"
    )


def test_momentum_taker_determinism_under_seed() -> None:
    """Same seed + same inputs → identical orders."""
    def drive(seed: int) -> list[Order]:
        mt = MomentumTaker(
            "m",
            "SPY",
            ema_short_sec=1.0,
            ema_long_sec=10.0,
            threshold_pct=0.002,
            cooldown_sec=0.5,
            seed=seed,
        )
        collected: list[Order] = []
        for i in range(150):
            ts = i * 0.1
            mid = 100.0 + 0.03 * i
            mt.on_book(_snap_mid(ts=ts, mid=mid))
            collected.extend(mt.decide(ts))
        return collected

    a = drive(seed=7)
    b = drive(seed=7)
    assert len(a) == len(b)
    for oa, ob in zip(a, b):
        assert oa.side == ob.side
        assert oa.size == ob.size
        assert oa.ts == ob.ts


def test_momentum_taker_no_orders_on_half_book() -> None:
    """Half-empty book → EMAs frozen → no fire even after later cross."""
    mt = MomentumTaker(
        "m",
        "SPY",
        ema_short_sec=1.0,
        ema_long_sec=5.0,
        threshold_pct=0.001,
        cooldown_sec=0.0,
    )
    for i in range(100):
        mt.on_book(_half_snap(ts=i * 0.1))
    assert mt.decide(10.0) == []


def test_momentum_taker_rejects_bad_config() -> None:
    with pytest.raises(ValueError):
        MomentumTaker("x", "SPY", ema_short_sec=0.0)
    with pytest.raises(ValueError):
        MomentumTaker("x", "SPY", ema_long_sec=-1.0)
    with pytest.raises(ValueError):
        MomentumTaker("x", "SPY", ema_short_sec=10.0, ema_long_sec=5.0)
    with pytest.raises(ValueError):
        MomentumTaker("x", "SPY", threshold_pct=-0.01)
    with pytest.raises(ValueError):
        MomentumTaker("x", "SPY", lot=0)
    with pytest.raises(ValueError):
        MomentumTaker("x", "SPY", cooldown_sec=-1.0)


# =====================================================================
# MeanReverter
# =====================================================================
def test_mean_reverter_silent_with_insufficient_samples() -> None:
    """< 5 samples → never trades."""
    mr = MeanReverter(
        "mr",
        "SPY",
        window_sec=60.0,
        zscore_threshold=1.0,
        cooldown_sec=0.0,
    )
    # Feed only 3 samples, with a wild outlier at the end.
    mr.on_book(_snap_mid(ts=0.0, mid=100.0))
    mr.on_book(_snap_mid(ts=1.0, mid=100.0))
    mr.on_book(_snap_mid(ts=2.0, mid=200.0))
    assert mr.decide(2.0) == []


def test_mean_reverter_sells_on_high_z() -> None:
    """Mid far above the rolling mean → SELL."""
    mr = MeanReverter(
        "mr",
        "SPY",
        window_sec=30.0,
        zscore_threshold=2.0,
        cooldown_sec=0.0,
    )
    # Steady mids around 100.0 with tiny jitter, then a spike up.
    jitter = [99.98, 100.02, 100.00, 99.99, 100.01, 100.03, 99.97, 100.02]
    for i, m in enumerate(jitter):
        mr.on_book(_snap_mid(ts=i * 1.0, mid=m))
    # Spike well above the mean (3+ stdevs out).
    mr.on_book(_snap_mid(ts=10.0, mid=101.0))
    orders = mr.decide(10.0)
    assert len(orders) == 1
    o = orders[0]
    assert o.side == OrderSide.SELL
    assert o.order_type == OrderType.MARKET
    assert math.isnan(o.price)


def test_mean_reverter_buys_on_low_z() -> None:
    """Mid far below the rolling mean → BUY."""
    mr = MeanReverter(
        "mr",
        "SPY",
        window_sec=30.0,
        zscore_threshold=2.0,
        cooldown_sec=0.0,
    )
    jitter = [99.98, 100.02, 100.00, 99.99, 100.01, 100.03, 99.97, 100.02]
    for i, m in enumerate(jitter):
        mr.on_book(_snap_mid(ts=i * 1.0, mid=m))
    mr.on_book(_snap_mid(ts=10.0, mid=99.0))
    orders = mr.decide(10.0)
    assert len(orders) == 1
    assert orders[0].side == OrderSide.BUY


def test_mean_reverter_silent_within_band() -> None:
    """In-band z → no order."""
    mr = MeanReverter(
        "mr",
        "SPY",
        window_sec=30.0,
        zscore_threshold=3.0,
        cooldown_sec=0.0,
    )
    jitter = [99.98, 100.02, 100.00, 99.99, 100.01, 100.03, 99.97, 100.02]
    for i, m in enumerate(jitter):
        mr.on_book(_snap_mid(ts=i * 1.0, mid=m))
    # Mild deviation — z should be < 3.
    mr.on_book(_snap_mid(ts=10.0, mid=100.04))
    assert mr.decide(10.0) == []


def test_mean_reverter_pin_mode_pulls_toward_strike() -> None:
    """With pin set well above the realised mean, even a 'mildly above mean'
    mid should look 'far below the pin', so the participant buys."""
    # Build a baseline mean-reverter at the realised mean (~100) — without
    # a pin and with the current mid only mildly above the mean, no trade.
    baseline = MeanReverter(
        "mr_base",
        "SPY",
        window_sec=30.0,
        zscore_threshold=2.0,
        cooldown_sec=0.0,
    )
    jitter = [99.98, 100.02, 100.00, 99.99, 100.01, 100.03, 99.97, 100.02]
    for i, m in enumerate(jitter):
        baseline.on_book(_snap_mid(ts=i * 1.0, mid=m))
    baseline.on_book(_snap_mid(ts=10.0, mid=100.04))
    assert baseline.decide(10.0) == []

    # Same data but with a pin *below* the realised mean. The pin term
    # adds a positive component to the effective z (current_mid - pin > 0),
    # pushing the participant to SELL toward the pin.
    pinned = MeanReverter(
        "mr_pin",
        "SPY",
        window_sec=30.0,
        zscore_threshold=2.0,
        cooldown_sec=0.0,
        pin_strike=95.0,        # well below the realised mean
        pin_strength_bps=10000, # very strong pin so the effect dominates
    )
    for i, m in enumerate(jitter):
        pinned.on_book(_snap_mid(ts=i * 1.0, mid=m))
    pinned.on_book(_snap_mid(ts=10.0, mid=100.04))
    orders = pinned.decide(10.0)
    assert len(orders) == 1
    assert orders[0].side == OrderSide.SELL


def test_mean_reverter_cooldown_enforced() -> None:
    """Two consecutive triggers respect ``cooldown_sec``."""
    mr = MeanReverter(
        "mr",
        "SPY",
        window_sec=30.0,
        zscore_threshold=1.5,
        cooldown_sec=5.0,
    )
    jitter = [99.98, 100.02, 100.00, 99.99, 100.01, 100.03, 99.97, 100.02]
    for i, m in enumerate(jitter):
        mr.on_book(_snap_mid(ts=i * 1.0, mid=m))
    # Spike → fires.
    mr.on_book(_snap_mid(ts=10.0, mid=101.5))
    fires_1 = mr.decide(10.0)
    assert len(fires_1) == 1
    # Spike again 1 sec later → blocked by cooldown.
    mr.on_book(_snap_mid(ts=11.0, mid=101.6))
    assert mr.decide(11.0) == []
    # After the cooldown window passes → allowed again.
    mr.on_book(_snap_mid(ts=20.0, mid=101.6))
    fires_2 = mr.decide(20.0)
    assert len(fires_2) == 1


def test_mean_reverter_window_trims_old_samples() -> None:
    """Samples outside ``window_sec`` should drop out of the calc."""
    mr = MeanReverter(
        "mr",
        "SPY",
        window_sec=5.0,  # tiny window
        zscore_threshold=2.0,
        cooldown_sec=0.0,
    )
    # Feed lots of samples; only the last few inside the window count.
    for i in range(50):
        mr.on_book(_snap_mid(ts=i * 1.0, mid=100.0 + 0.001 * i))
    # Internal window should now hold roughly <= 6 entries (5s window
    # at 1s spacing plus the boundary sample).
    assert len(mr._window) <= 7


def test_mean_reverter_rejects_bad_config() -> None:
    with pytest.raises(ValueError):
        MeanReverter("x", "SPY", window_sec=0.0)
    with pytest.raises(ValueError):
        MeanReverter("x", "SPY", zscore_threshold=0.0)
    with pytest.raises(ValueError):
        MeanReverter("x", "SPY", lot=0)
    with pytest.raises(ValueError):
        MeanReverter("x", "SPY", cooldown_sec=-1.0)
    with pytest.raises(ValueError):
        MeanReverter("x", "SPY", pin_strike=-1.0)
    with pytest.raises(ValueError):
        MeanReverter("x", "SPY", pin_strength_bps=-1.0)


# =====================================================================
# NewsShock
# =====================================================================
def test_news_shock_emits_orders_during_gap_window() -> None:
    """A scheduled gap should fire ~10 aggressive MARKET sells."""
    ns = NewsShock(
        "ns",
        "SPY",
        events=[
            {
                "ts_offset_sec": 5.0,
                "kind": "gap",
                "params": {"pct_move": -0.06, "jump_sec": 5.0},
            }
        ],
    )
    # Prime a mid so flow sizing works.
    ns.on_book(_snap_mid(ts=0.0, mid=100.0))
    # Before the window: nothing.
    assert ns.decide(4.99) == []
    # Walk the window in 0.5s steps until everything fires.
    collected: list[Order] = []
    for i in range(20):
        ts = 5.0 + i * 0.5
        ns.on_book(_snap_mid(ts=ts, mid=100.0))
        collected.extend(ns.decide(ts))
    assert len(collected) == 10  # ~10 sub-orders per the spec
    assert all(o.side == OrderSide.SELL for o in collected)  # negative pct_move
    assert all(o.order_type == OrderType.MARKET for o in collected)
    assert all(math.isnan(o.price) for o in collected)
    assert all(o.size > 0 for o in collected)
    assert all(o.owner_id == "ns" for o in collected)


def test_news_shock_silent_outside_window() -> None:
    """Before and well-after a scheduled event we emit nothing new."""
    ns = NewsShock(
        "ns",
        "SPY",
        events=[
            {
                "ts_offset_sec": 5.0,
                "kind": "news_print",
                "params": {"pct_move": -0.015, "jump_sec": 2.0},
            }
        ],
    )
    ns.on_book(_snap_mid(ts=0.0, mid=100.0))
    # Long before:
    assert ns.decide(1.0) == []
    # Drain the event:
    drained: list[Order] = []
    for i in range(40):
        ts = 5.0 + i * 0.1
        ns.on_book(_snap_mid(ts=ts, mid=100.0))
        drained.extend(ns.decide(ts))
    assert drained, "news_print should have emitted at least one sub-order"
    # Well after — every event has fully fired; subsequent decides are silent.
    for ts in (20.0, 50.0, 100.0):
        ns.on_book(_snap_mid(ts=ts, mid=100.0))
        assert ns.decide(ts) == []


def test_news_shock_halt_emits_nothing() -> None:
    """Halt is documented as a no-flow placeholder in v1."""
    ns = NewsShock(
        "ns",
        "SPY",
        events=[
            {
                "ts_offset_sec": 1.0,
                "kind": "halt",
                "params": {"duration_sec": 10.0},
            }
        ],
    )
    ns.on_book(_snap_mid(ts=0.0, mid=100.0))
    for i in range(50):
        ts = i * 0.5
        ns.on_book(_snap_mid(ts=ts, mid=100.0))
        assert ns.decide(ts) == []


def test_news_shock_vol_spike_emits_nothing() -> None:
    """Vol-spike + spread-widen are documented metadata-only events."""
    ns = NewsShock(
        "ns",
        "SPY",
        events=[
            {
                "ts_offset_sec": 1.0,
                "kind": "vol_spike",
                "params": {},
            },
            {
                "ts_offset_sec": 2.0,
                "kind": "spread_widen",
                "params": {},
            },
        ],
    )
    ns.on_book(_snap_mid(ts=0.0, mid=100.0))
    for i in range(20):
        ts = i * 0.5
        ns.on_book(_snap_mid(ts=ts, mid=100.0))
        assert ns.decide(ts) == []


def test_news_shock_positive_gap_emits_buys() -> None:
    """Positive pct_move → BUY orders (lift offers)."""
    ns = NewsShock(
        "ns",
        "SPY",
        events=[
            {
                "ts_offset_sec": 0.0,
                "kind": "gap",
                "params": {"pct_move": 0.04, "jump_sec": 2.0},
            }
        ],
    )
    ns.on_book(_snap_mid(ts=0.0, mid=100.0))
    collected: list[Order] = []
    for i in range(15):
        ts = i * 0.2
        ns.on_book(_snap_mid(ts=ts, mid=100.0))
        collected.extend(ns.decide(ts))
    assert collected
    assert all(o.side == OrderSide.BUY for o in collected)


def test_news_shock_rejects_bad_config() -> None:
    """Bad event dicts fail at construction time."""
    with pytest.raises(ValueError, match="kind"):
        NewsShock("x", "SPY", events=[{"ts_offset_sec": 1.0, "kind": "boom"}])
    with pytest.raises(ValueError, match="ts_offset_sec"):
        NewsShock(
            "x",
            "SPY",
            events=[{"ts_offset_sec": -1.0, "kind": "gap", "params": {"pct_move": -0.01, "jump_sec": 1.0}}],
        )
    with pytest.raises(ValueError, match="jump_sec"):
        NewsShock(
            "x",
            "SPY",
            events=[{"ts_offset_sec": 0.0, "kind": "gap", "params": {"pct_move": -0.01, "jump_sec": 0.0}}],
        )
    with pytest.raises(ValueError, match="must be a dict"):
        NewsShock("x", "SPY", events=["not a dict"])
    with pytest.raises(ValueError, match="missing required key"):
        NewsShock("x", "SPY", events=[{"kind": "gap"}])
