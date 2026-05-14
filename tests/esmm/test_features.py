"""Tests for the microstructure feature engine."""

from __future__ import annotations

import pytest

from src.esmm.features import FeatureEngine, RollingStats, realized_variance, signed_volume
from src.esmm.schemas import Fill, OrderBookLevel, OrderBookSnapshot, Side


def _snap(ts: float, mid: float, bid_size: float = 100.0, ask_size: float = 100.0) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        ts=ts,
        symbol="TEST",
        bids=[OrderBookLevel(price=mid - 0.05, size=bid_size)],
        asks=[OrderBookLevel(price=mid + 0.05, size=ask_size)],
    )


def test_rolling_stats_window_eviction():
    rs = RollingStats(window=3)
    for v in [1.0, 2.0, 3.0, 4.0]:
        rs.add(v)
    assert list(rs.values) == [2.0, 3.0, 4.0]
    assert rs.mean == pytest.approx(3.0)


def test_rolling_stats_variance_zero_with_one_value():
    rs = RollingStats(window=10)
    rs.add(5.0)
    assert rs.variance == 0.0


def test_feature_engine_first_call_no_returns():
    engine = FeatureEngine()
    feats = engine.update(_snap(0.0, 100.0))
    assert feats["rv_fast"] == 0.0
    assert feats["mid"] == pytest.approx(100.0)


def test_feature_engine_picks_up_movement():
    engine = FeatureEngine(fast_window=5, slow_window=20)
    snaps = [_snap(float(i), 100.0 + i * 0.1) for i in range(10)]
    last_feats = {}
    for s in snaps:
        last_feats = engine.update(s)
    assert last_feats["momentum"] > 0  # consistent upward drift
    assert last_feats["rv_fast"] > 0


def test_micro_minus_mid_positive_when_bid_heavy():
    engine = FeatureEngine()
    feats = engine.update(_snap(0.0, 100.0, bid_size=500, ask_size=50))
    assert feats["micro_minus_mid_bps"] > 0


def test_realized_variance_zero_for_constant_mid():
    snaps = [_snap(float(i), 100.0) for i in range(20)]
    assert realized_variance(snaps) == pytest.approx(0.0)


def test_realized_variance_positive_for_random_walk():
    import random
    rng = random.Random(0)
    mid = 100.0
    snaps = []
    for i in range(50):
        mid *= 1 + rng.gauss(0, 0.001)
        snaps.append(_snap(float(i), mid))
    assert realized_variance(snaps) > 0


def test_signed_volume_buys_minus_sells():
    fills = [
        Fill(ts=0, symbol="X", side=Side.BUY, price=100, size=10, fair_value_at_fill=100),
        Fill(ts=1, symbol="X", side=Side.SELL, price=100, size=4, fair_value_at_fill=100),
        Fill(ts=2, symbol="X", side=Side.BUY, price=100, size=6, fair_value_at_fill=100),
    ]
    assert signed_volume(fills) == 12  # 10 - 4 + 6
