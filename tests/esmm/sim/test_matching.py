"""Example-based tests for ``src.esmm.sim.matching.MatchEngine``."""

from __future__ import annotations

import math

import pytest

from src.esmm.sim.lob import LimitOrderBook, Order, OrderSide, OrderType
from src.esmm.sim.matching import MatchEngine, MatchResult


def _mk_limit(
    book: LimitOrderBook,
    side: OrderSide,
    price: float,
    size: float,
    *,
    owner: str = "alice",
    ts: float = 0.0,
) -> Order:
    return Order(
        order_id=book.next_order_id(),
        symbol=book.symbol,
        side=side,
        price=price,
        size=size,
        ts=ts,
        owner_id=owner,
        order_type=OrderType.LIMIT,
    )


def _mk_market(
    book: LimitOrderBook,
    side: OrderSide,
    size: float,
    *,
    owner: str = "alice",
    ts: float = 0.0,
) -> Order:
    return Order(
        order_id=book.next_order_id(),
        symbol=book.symbol,
        side=side,
        price=float("nan"),
        size=size,
        ts=ts,
        owner_id=owner,
        order_type=OrderType.MARKET,
    )


# ----------------------------------------------------------------------
# Non-crossing limits → rest on book
# ----------------------------------------------------------------------
def test_limit_no_cross_rests_on_book() -> None:
    book = LimitOrderBook("SPY")
    book.add(_mk_limit(book, OrderSide.SELL, 101.0, 10.0, owner="maker"))
    eng = MatchEngine(book)
    incoming = _mk_limit(book, OrderSide.BUY, 99.0, 5.0, owner="taker")
    res = eng.match(incoming)
    assert res.fills == []
    assert res.remainder == 5.0
    assert book.best_bid() == 99.0


def test_limit_no_cross_rests_when_book_empty() -> None:
    book = LimitOrderBook("SPY")
    eng = MatchEngine(book)
    incoming = _mk_limit(book, OrderSide.BUY, 99.0, 5.0)
    res = eng.match(incoming)
    assert res.fills == []
    assert res.remainder == 5.0
    assert book.best_bid() == 99.0


# ----------------------------------------------------------------------
# Single-level crossings
# ----------------------------------------------------------------------
def test_limit_crosses_single_resting_full_fill_at_resting_price() -> None:
    book = LimitOrderBook("SPY")
    resting = _mk_limit(book, OrderSide.SELL, 101.0, 10.0, owner="maker")
    book.add(resting)
    eng = MatchEngine(book)
    incoming = _mk_limit(book, OrderSide.BUY, 102.0, 10.0, owner="taker")
    res = eng.match(incoming)
    assert len(res.fills) == 1
    _agg, rest, price, size = res.fills[0]
    assert price == 101.0  # resting price wins
    assert size == 10.0
    assert rest is resting
    assert res.remainder == 0.0
    assert book.best_ask() is None  # consumed


def test_limit_crosses_partial_resting_remainder_in_book() -> None:
    book = LimitOrderBook("SPY")
    resting = _mk_limit(book, OrderSide.SELL, 101.0, 10.0, owner="maker")
    book.add(resting)
    eng = MatchEngine(book)
    incoming = _mk_limit(book, OrderSide.BUY, 102.0, 4.0, owner="taker")
    res = eng.match(incoming)
    assert len(res.fills) == 1
    assert res.fills[0][3] == 4.0
    assert res.remainder == 0.0
    # 6 units of the resting ask remain
    assert book.best_ask() == 101.0
    assert book.best_ask_size() == 6.0


def test_aggressor_size_exceeds_single_resting_rest_added_to_book() -> None:
    book = LimitOrderBook("SPY")
    resting = _mk_limit(book, OrderSide.SELL, 101.0, 3.0, owner="maker")
    book.add(resting)
    eng = MatchEngine(book)
    incoming = _mk_limit(book, OrderSide.BUY, 101.0, 10.0, owner="taker")
    res = eng.match(incoming)
    assert len(res.fills) == 1
    assert res.fills[0][3] == 3.0
    # Aggressor leftover (7) rests as a bid at 101.0
    assert math.isclose(book.best_bid(), 101.0)
    assert book.best_bid_size() == 7.0


# ----------------------------------------------------------------------
# Walking multiple levels
# ----------------------------------------------------------------------
def test_limit_walks_three_price_levels() -> None:
    book = LimitOrderBook("SPY")
    book.add(_mk_limit(book, OrderSide.SELL, 101.0, 5.0, owner="m1"))
    book.add(_mk_limit(book, OrderSide.SELL, 102.0, 5.0, owner="m2"))
    book.add(_mk_limit(book, OrderSide.SELL, 103.0, 5.0, owner="m3"))
    eng = MatchEngine(book)
    incoming = _mk_limit(book, OrderSide.BUY, 103.0, 12.0, owner="taker")
    res = eng.match(incoming)
    assert [f[2] for f in res.fills] == [101.0, 102.0, 103.0]
    assert [f[3] for f in res.fills] == [5.0, 5.0, 2.0]
    assert res.remainder == 0.0
    # Some of the 103 ask survives (5 - 2 = 3)
    assert book.best_ask() == 103.0
    assert book.best_ask_size() == 3.0


def test_limit_stops_walking_when_price_exhausted() -> None:
    """Limit at 102 should not eat the 103 level even with size left."""
    book = LimitOrderBook("SPY")
    book.add(_mk_limit(book, OrderSide.SELL, 101.0, 3.0, owner="m1"))
    book.add(_mk_limit(book, OrderSide.SELL, 103.0, 5.0, owner="m2"))
    eng = MatchEngine(book)
    incoming = _mk_limit(book, OrderSide.BUY, 102.0, 10.0, owner="taker")
    res = eng.match(incoming)
    assert len(res.fills) == 1
    assert res.fills[0][2] == 101.0
    # Leftover 7 rests at 102
    assert book.best_bid() == 102.0
    assert book.best_bid_size() == 7.0
    assert book.best_ask() == 103.0


# ----------------------------------------------------------------------
# Market orders
# ----------------------------------------------------------------------
def test_market_buy_on_empty_book_returns_remainder() -> None:
    book = LimitOrderBook("SPY")
    eng = MatchEngine(book)
    res = eng.match(_mk_market(book, OrderSide.BUY, 5.0))
    assert res.fills == []
    assert res.remainder == 5.0
    # MARKET orders never rest
    assert book.best_bid() is None


def test_market_buy_walks_book_until_empty() -> None:
    book = LimitOrderBook("SPY")
    book.add(_mk_limit(book, OrderSide.SELL, 101.0, 3.0, owner="m1"))
    book.add(_mk_limit(book, OrderSide.SELL, 102.0, 4.0, owner="m2"))
    eng = MatchEngine(book)
    res = eng.match(_mk_market(book, OrderSide.BUY, 100.0, owner="taker"))
    # Consumes everything (7), 93 unfilled
    assert [f[3] for f in res.fills] == [3.0, 4.0]
    assert res.remainder == 93.0
    assert book.best_ask() is None


def test_market_sell_consumes_bids_in_order() -> None:
    book = LimitOrderBook("SPY")
    book.add(_mk_limit(book, OrderSide.BUY, 100.0, 5.0, owner="m1"))
    book.add(_mk_limit(book, OrderSide.BUY, 99.0, 5.0, owner="m2"))
    eng = MatchEngine(book)
    res = eng.match(_mk_market(book, OrderSide.SELL, 7.0, owner="taker"))
    assert [f[2] for f in res.fills] == [100.0, 99.0]
    assert [f[3] for f in res.fills] == [5.0, 2.0]
    assert res.remainder == 0.0
    assert book.best_bid() == 99.0
    assert book.best_bid_size() == 3.0


# ----------------------------------------------------------------------
# Self-trade prevention
# ----------------------------------------------------------------------
def test_self_trade_prevention_enabled_skips_resting() -> None:
    book = LimitOrderBook("SPY")
    own_resting = _mk_limit(book, OrderSide.SELL, 101.0, 5.0, owner="alice")
    other_resting = _mk_limit(book, OrderSide.SELL, 102.0, 5.0, owner="bob")
    book.add(own_resting)
    book.add(other_resting)
    eng = MatchEngine(book, self_trade_prevention=True)
    incoming = _mk_limit(book, OrderSide.BUY, 102.0, 5.0, owner="alice")
    res = eng.match(incoming)
    # Resting alice order skipped (cancelled); aggressor fills bob.
    assert len(res.fills) == 1
    assert res.fills[0][1] is other_resting
    assert res.fills[0][2] == 102.0
    # alice's resting order is no longer in book
    assert own_resting.cancelled is True
    assert own_resting.order_id not in book._orders_by_id


def test_self_trade_prevention_disabled_fills_self() -> None:
    book = LimitOrderBook("SPY")
    own_resting = _mk_limit(book, OrderSide.SELL, 101.0, 5.0, owner="alice")
    book.add(own_resting)
    eng = MatchEngine(book, self_trade_prevention=False)
    incoming = _mk_limit(book, OrderSide.BUY, 101.0, 5.0, owner="alice")
    res = eng.match(incoming)
    assert len(res.fills) == 1
    assert res.fills[0][1] is own_resting
    assert res.remainder == 0.0


# ----------------------------------------------------------------------
# Price-time priority
# ----------------------------------------------------------------------
def test_price_time_priority_at_same_price_earlier_fills_first() -> None:
    book = LimitOrderBook("SPY")
    first = _mk_limit(book, OrderSide.SELL, 101.0, 5.0, owner="m1", ts=0.0)
    second = _mk_limit(book, OrderSide.SELL, 101.0, 5.0, owner="m2", ts=1.0)
    book.add(first)
    book.add(second)
    eng = MatchEngine(book)
    res = eng.match(_mk_limit(book, OrderSide.BUY, 101.0, 7.0, owner="taker"))
    # first must fill in full before second is touched
    assert res.fills[0][1] is first
    assert res.fills[0][3] == 5.0
    assert res.fills[1][1] is second
    assert res.fills[1][3] == 2.0


# ----------------------------------------------------------------------
# Invariants after match
# ----------------------------------------------------------------------
def test_no_crossed_book_after_match() -> None:
    book = LimitOrderBook("SPY")
    book.add(_mk_limit(book, OrderSide.BUY, 100.0, 10.0, owner="m1"))
    book.add(_mk_limit(book, OrderSide.SELL, 101.0, 10.0, owner="m2"))
    eng = MatchEngine(book)
    # Aggressive bid that partially fills then rests.
    res = eng.match(_mk_limit(book, OrderSide.BUY, 101.5, 4.0, owner="taker"))
    assert len(res.fills) == 1
    bb = book.best_bid()
    ba = book.best_ask()
    assert bb is None or ba is None or bb < ba


def test_match_does_not_rest_market_remainder() -> None:
    book = LimitOrderBook("SPY")
    book.add(_mk_limit(book, OrderSide.SELL, 101.0, 5.0, owner="m1"))
    eng = MatchEngine(book)
    res = eng.match(_mk_market(book, OrderSide.BUY, 20.0, owner="taker"))
    assert res.remainder == 15.0
    assert book.best_bid() is None  # never rests


def test_match_empty_order_is_noop() -> None:
    book = LimitOrderBook("SPY")
    eng = MatchEngine(book)
    # Construct an order with remaining=0
    o = _mk_limit(book, OrderSide.BUY, 100.0, 0.0001, owner="x")
    o.remaining = 0.0
    res = eng.match(o)
    assert res.fills == []
    assert res.remainder == 0.0


def test_aggressor_remainder_after_walk_rests_at_limit_price() -> None:
    book = LimitOrderBook("SPY")
    book.add(_mk_limit(book, OrderSide.SELL, 101.0, 3.0, owner="m1"))
    eng = MatchEngine(book)
    # Buy 10 @ 102: fills 3 @ 101, leftover 7 should rest at 102 not 101.
    res = eng.match(_mk_limit(book, OrderSide.BUY, 102.0, 10.0, owner="taker"))
    assert len(res.fills) == 1
    assert book.best_bid() == 102.0
    assert book.best_bid_size() == 7.0
