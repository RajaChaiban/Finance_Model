"""Example-based tests for ``src.esmm.sim.lob.LimitOrderBook``.

Covers the public API: add / cancel / modify / snapshot / best_bid /
best_ask / mid / queue_position, plus crossed-book guard. Property-based
invariants live in ``test_lob_properties.py``.
"""

from __future__ import annotations

import math

import pytest

from src.esmm.schemas import OrderBookSnapshot
from src.esmm.sim.lob import (
    CrossedBookError,
    LimitOrderBook,
    Order,
    OrderSide,
    OrderType,
)


def _mk(
    book: LimitOrderBook,
    side: OrderSide,
    price: float,
    size: float,
    *,
    owner: str = "alice",
    ts: float = 0.0,
) -> Order:
    """Helper: build a LIMIT order with a fresh id from the book."""
    return Order(
        order_id=book.next_order_id(),
        symbol=book.symbol,
        side=side,
        price=price,
        size=size,
        ts=ts,
        owner_id=owner,
    )


# ----------------------------------------------------------------------
# add / basic structure
# ----------------------------------------------------------------------
def test_add_single_bid_returns_position_one() -> None:
    book = LimitOrderBook("SPY")
    pos = book.add(_mk(book, OrderSide.BUY, 100.0, 10.0))
    assert pos == 1
    assert book.best_bid() == 100.0
    assert book.best_ask() is None


def test_add_single_ask_returns_position_one() -> None:
    book = LimitOrderBook("SPY")
    pos = book.add(_mk(book, OrderSide.SELL, 101.0, 5.0))
    assert pos == 1
    assert book.best_ask() == 101.0
    assert book.best_bid() is None


def test_fifo_at_same_price_level() -> None:
    book = LimitOrderBook("SPY")
    o1 = _mk(book, OrderSide.BUY, 100.0, 10.0, owner="a")
    o2 = _mk(book, OrderSide.BUY, 100.0, 20.0, owner="b")
    o3 = _mk(book, OrderSide.BUY, 100.0, 30.0, owner="c")
    assert book.add(o1) == 1
    assert book.add(o2) == 2
    assert book.add(o3) == 3
    assert book.queue_position(o1.order_id) == 1
    assert book.queue_position(o3.order_id) == 3


def test_bids_sorted_descending() -> None:
    book = LimitOrderBook("SPY")
    book.add(_mk(book, OrderSide.BUY, 99.0, 10.0))
    book.add(_mk(book, OrderSide.BUY, 100.0, 10.0))
    book.add(_mk(book, OrderSide.BUY, 98.0, 10.0))
    assert book._bid_prices == [100.0, 99.0, 98.0]
    assert book.best_bid() == 100.0


def test_asks_sorted_ascending() -> None:
    book = LimitOrderBook("SPY")
    book.add(_mk(book, OrderSide.SELL, 102.0, 10.0))
    book.add(_mk(book, OrderSide.SELL, 101.0, 10.0))
    book.add(_mk(book, OrderSide.SELL, 103.0, 10.0))
    assert book._ask_prices == [101.0, 102.0, 103.0]
    assert book.best_ask() == 101.0


def test_add_rejects_market_order() -> None:
    book = LimitOrderBook("SPY")
    o = Order(
        order_id=book.next_order_id(),
        symbol="SPY",
        side=OrderSide.BUY,
        price=float("nan"),
        size=10.0,
        ts=0.0,
        owner_id="x",
        order_type=OrderType.MARKET,
    )
    with pytest.raises(ValueError):
        book.add(o)


def test_add_rejects_duplicate_id() -> None:
    book = LimitOrderBook("SPY")
    o1 = _mk(book, OrderSide.BUY, 100.0, 10.0)
    book.add(o1)
    # Manually construct one with the same id
    o2 = Order(
        order_id=o1.order_id,
        symbol="SPY",
        side=OrderSide.BUY,
        price=99.0,
        size=10.0,
        ts=0.0,
        owner_id="x",
    )
    with pytest.raises(ValueError):
        book.add(o2)


# ----------------------------------------------------------------------
# cancel
# ----------------------------------------------------------------------
def test_cancel_removes_order() -> None:
    book = LimitOrderBook("SPY")
    o = _mk(book, OrderSide.BUY, 100.0, 10.0)
    book.add(o)
    assert book.cancel(o.order_id) is True
    assert book.best_bid() is None
    assert book.queue_position(o.order_id) is None


def test_cancel_unknown_returns_false() -> None:
    book = LimitOrderBook("SPY")
    assert book.cancel(9999) is False


def test_cancel_twice_returns_false_second_time() -> None:
    book = LimitOrderBook("SPY")
    o = _mk(book, OrderSide.BUY, 100.0, 10.0)
    book.add(o)
    assert book.cancel(o.order_id) is True
    assert book.cancel(o.order_id) is False


def test_cancel_updates_queue_positions_behind() -> None:
    book = LimitOrderBook("SPY")
    o1 = _mk(book, OrderSide.BUY, 100.0, 10.0)
    o2 = _mk(book, OrderSide.BUY, 100.0, 20.0)
    o3 = _mk(book, OrderSide.BUY, 100.0, 30.0)
    for o in (o1, o2, o3):
        book.add(o)
    assert book.queue_position(o3.order_id) == 3
    book.cancel(o1.order_id)
    # o2 and o3 shift forward by 1
    assert book.queue_position(o2.order_id) == 1
    assert book.queue_position(o3.order_id) == 2


def test_cancel_last_at_price_removes_level() -> None:
    book = LimitOrderBook("SPY")
    o = _mk(book, OrderSide.BUY, 100.0, 10.0)
    book.add(o)
    book.cancel(o.order_id)
    assert 100.0 not in book._bid_prices
    assert 100.0 not in book._bids


# ----------------------------------------------------------------------
# modify
# ----------------------------------------------------------------------
def test_modify_smaller_keeps_queue_position() -> None:
    book = LimitOrderBook("SPY")
    o1 = _mk(book, OrderSide.BUY, 100.0, 10.0)
    o2 = _mk(book, OrderSide.BUY, 100.0, 20.0)
    book.add(o1)
    book.add(o2)
    new_pos = book.modify(o1.order_id, 5.0)
    assert new_pos == 1
    assert book.queue_position(o1.order_id) == 1
    assert book.queue_position(o2.order_id) == 2
    # remaining shrunk
    assert book._orders_by_id[o1.order_id].remaining == 5.0


def test_modify_larger_loses_queue_position() -> None:
    book = LimitOrderBook("SPY")
    o1 = _mk(book, OrderSide.BUY, 100.0, 10.0)
    o2 = _mk(book, OrderSide.BUY, 100.0, 20.0)
    o3 = _mk(book, OrderSide.BUY, 100.0, 30.0)
    for o in (o1, o2, o3):
        book.add(o)
    # Grow o1 → it goes to the back.
    new_pos = book.modify(o1.order_id, 50.0)
    assert new_pos == 3
    assert book.queue_position(o2.order_id) == 1
    assert book.queue_position(o3.order_id) == 2
    assert book.queue_position(o1.order_id) == 3


def test_modify_equal_size_is_noop() -> None:
    book = LimitOrderBook("SPY")
    o = _mk(book, OrderSide.BUY, 100.0, 10.0)
    book.add(o)
    pos = book.modify(o.order_id, 10.0)
    assert pos == 1
    assert book._orders_by_id[o.order_id].remaining == 10.0


def test_modify_to_zero_cancels() -> None:
    book = LimitOrderBook("SPY")
    o = _mk(book, OrderSide.BUY, 100.0, 10.0)
    book.add(o)
    assert book.modify(o.order_id, 0.0) == 0
    assert book.queue_position(o.order_id) is None


def test_modify_unknown_raises() -> None:
    book = LimitOrderBook("SPY")
    with pytest.raises(KeyError):
        book.modify(9999, 10.0)


# ----------------------------------------------------------------------
# snapshot
# ----------------------------------------------------------------------
def test_snapshot_aggregates_sizes_per_level() -> None:
    book = LimitOrderBook("SPY")
    book.add(_mk(book, OrderSide.BUY, 100.0, 10.0))
    book.add(_mk(book, OrderSide.BUY, 100.0, 20.0))
    book.add(_mk(book, OrderSide.SELL, 101.0, 5.0))
    snap = book.snapshot(ts=1.5)
    assert isinstance(snap, OrderBookSnapshot)
    assert snap.ts == 1.5
    assert snap.symbol == "SPY"
    assert snap.bids[0].price == 100.0
    assert snap.bids[0].size == 30.0
    assert snap.asks[0].price == 101.0
    assert snap.asks[0].size == 5.0


def test_snapshot_omits_cancelled_levels() -> None:
    book = LimitOrderBook("SPY")
    o = _mk(book, OrderSide.BUY, 99.0, 10.0)
    book.add(o)
    book.add(_mk(book, OrderSide.BUY, 100.0, 5.0))
    book.add(_mk(book, OrderSide.SELL, 101.0, 5.0))
    book.cancel(o.order_id)
    snap = book.snapshot(ts=0.0)
    prices = [lvl.price for lvl in snap.bids]
    assert 99.0 not in prices
    assert 100.0 in prices


def test_snapshot_descending_bids_ascending_asks() -> None:
    book = LimitOrderBook("SPY")
    book.add(_mk(book, OrderSide.BUY, 99.0, 1.0))
    book.add(_mk(book, OrderSide.BUY, 100.0, 1.0))
    book.add(_mk(book, OrderSide.BUY, 98.0, 1.0))
    book.add(_mk(book, OrderSide.SELL, 103.0, 1.0))
    book.add(_mk(book, OrderSide.SELL, 101.0, 1.0))
    book.add(_mk(book, OrderSide.SELL, 102.0, 1.0))
    snap = book.snapshot(ts=0.0)
    bid_prices = [lvl.price for lvl in snap.bids]
    ask_prices = [lvl.price for lvl in snap.asks]
    assert bid_prices == sorted(bid_prices, reverse=True)
    assert ask_prices == sorted(ask_prices)


def test_snapshot_one_sided_uses_model_construct() -> None:
    # Bid-only book: schema would normally reject; snapshot bypasses.
    book = LimitOrderBook("SPY")
    book.add(_mk(book, OrderSide.BUY, 100.0, 10.0))
    snap = book.snapshot(ts=0.0)
    assert snap.bids[0].price == 100.0
    assert snap.asks == []


# ----------------------------------------------------------------------
# best_bid / best_ask / mid
# ----------------------------------------------------------------------
def test_mid_with_both_sides() -> None:
    book = LimitOrderBook("SPY")
    book.add(_mk(book, OrderSide.BUY, 100.0, 1.0))
    book.add(_mk(book, OrderSide.SELL, 102.0, 1.0))
    assert book.mid() == 101.0


def test_mid_with_empty_book_returns_nan() -> None:
    book = LimitOrderBook("SPY")
    assert math.isnan(book.mid())


def test_mid_with_one_sided_book_returns_nan() -> None:
    book = LimitOrderBook("SPY")
    book.add(_mk(book, OrderSide.BUY, 100.0, 1.0))
    assert math.isnan(book.mid())


def test_best_sizes_aggregate_queue() -> None:
    book = LimitOrderBook("SPY")
    book.add(_mk(book, OrderSide.BUY, 100.0, 7.0))
    book.add(_mk(book, OrderSide.BUY, 100.0, 3.0))
    book.add(_mk(book, OrderSide.SELL, 101.0, 4.0))
    assert book.best_bid_size() == 10.0
    assert book.best_ask_size() == 4.0


# ----------------------------------------------------------------------
# ids + invariants
# ----------------------------------------------------------------------
def test_order_ids_are_monotonic() -> None:
    book = LimitOrderBook("SPY")
    ids = [book.next_order_id() for _ in range(5)]
    assert ids == sorted(ids)
    assert len(set(ids)) == 5


def test_crossed_book_add_raises() -> None:
    """Inserting a bid above the best ask must raise + leave book clean."""
    book = LimitOrderBook("SPY")
    book.add(_mk(book, OrderSide.BUY, 100.0, 10.0))
    book.add(_mk(book, OrderSide.SELL, 101.0, 10.0))
    bad_bid = _mk(book, OrderSide.BUY, 102.0, 5.0)
    with pytest.raises(CrossedBookError):
        book.add(bad_bid)
    # Book state untouched
    assert book.best_bid() == 100.0
    assert book.best_ask() == 101.0
    assert bad_bid.order_id not in book._orders_by_id


def test_crossed_book_add_raises_sell_side() -> None:
    book = LimitOrderBook("SPY")
    book.add(_mk(book, OrderSide.BUY, 100.0, 10.0))
    book.add(_mk(book, OrderSide.SELL, 101.0, 10.0))
    bad_ask = _mk(book, OrderSide.SELL, 99.0, 5.0)
    with pytest.raises(CrossedBookError):
        book.add(bad_ask)
    assert book.best_ask() == 101.0


def test_queue_position_unknown_returns_none() -> None:
    book = LimitOrderBook("SPY")
    assert book.queue_position(9999) is None
