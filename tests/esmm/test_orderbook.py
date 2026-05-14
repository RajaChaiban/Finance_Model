"""Tests for orderbook primitives."""

from __future__ import annotations

import math

import pytest

from src.esmm.orderbook import (
    book_depth,
    is_crossed,
    log_return,
    micro_price,
    mid_price,
    order_book_imbalance,
    spread,
    spread_bps,
    weighted_mid_price,
)
from src.esmm.schemas import OrderBookLevel, OrderBookSnapshot


def _make_book(bid: float, ask: float, bid_size: float = 100.0, ask_size: float = 100.0) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        ts=0.0,
        symbol="TEST",
        bids=[OrderBookLevel(price=bid, size=bid_size)],
        asks=[OrderBookLevel(price=ask, size=ask_size)],
    )


def test_mid_price_is_arithmetic_average():
    snap = _make_book(99.5, 100.5)
    assert mid_price(snap) == 100.0


def test_spread_and_spread_bps():
    snap = _make_book(99.5, 100.5)
    assert spread(snap) == pytest.approx(1.0)
    assert spread_bps(snap) == pytest.approx(100.0)  # 1.0 / 100.0 * 1e4


def test_micro_price_equals_mid_when_balanced():
    snap = _make_book(99.5, 100.5, bid_size=100, ask_size=100)
    assert micro_price(snap) == pytest.approx(mid_price(snap))


def test_micro_price_skews_toward_thin_side():
    # Heavy bid (1000) vs thin ask (10) → micro lifts toward ask (next trade likely up).
    snap = _make_book(99.5, 100.5, bid_size=1000, ask_size=10)
    m = mid_price(snap)
    mp = micro_price(snap)
    assert mp > m, f"micro_price {mp} should be above mid {m} when bid is heavier"


def test_obi_bounded_in_minus_one_to_plus_one():
    snap = _make_book(99.5, 100.5, bid_size=300, ask_size=100)
    obi = order_book_imbalance(snap)
    assert -1.0 <= obi <= 1.0
    assert obi == pytest.approx((300 - 100) / 400)


def test_obi_zero_when_perfectly_balanced():
    snap = _make_book(99.5, 100.5, bid_size=100, ask_size=100)
    assert order_book_imbalance(snap) == pytest.approx(0.0)


def test_weighted_mid_with_multiple_levels():
    snap = OrderBookSnapshot(
        ts=0.0,
        symbol="TEST",
        bids=[
            OrderBookLevel(price=99.5, size=100),
            OrderBookLevel(price=99.4, size=200),
        ],
        asks=[
            OrderBookLevel(price=100.5, size=100),
            OrderBookLevel(price=100.6, size=200),
        ],
    )
    wm = weighted_mid_price(snap, depth=2)
    # Symmetric book → weighted mid equals mid
    assert wm == pytest.approx(mid_price(snap))


def test_book_depth_sums_top_n_levels():
    snap = OrderBookSnapshot(
        ts=0.0,
        symbol="TEST",
        bids=[OrderBookLevel(price=99.5, size=100), OrderBookLevel(price=99.4, size=50)],
        asks=[OrderBookLevel(price=100.5, size=80)],
    )
    assert book_depth(snap, "bid", depth=2) == 150
    assert book_depth(snap, "ask", depth=5) == 80


def test_is_crossed_detects_anomaly():
    crossed = _make_book(100.5, 100.0)  # bid > ask
    assert is_crossed(crossed)
    healthy = _make_book(99.5, 100.5)
    assert not is_crossed(healthy)


def test_log_return_handles_invalid_inputs():
    assert log_return(0, 100) == 0.0
    assert log_return(100, 0) == 0.0
    assert log_return(100, 100) == pytest.approx(0.0)
    assert log_return(100, 110) == pytest.approx(math.log(1.1))
