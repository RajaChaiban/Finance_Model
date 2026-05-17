"""Hypothesis property tests for the LOB.

Invariants we exercise:

1. ``bids_sorted_descending`` — after any sequence of add/cancel, the
   bid ladder is strictly descending.
2. ``asks_sorted_ascending`` — symmetric, ascending.
3. ``no_crossed_book_when_matching_first`` — random aggressive orders
   that go through ``MatchEngine.match`` never leave the book crossed.
4. ``conservation_of_size`` — sum of all sizes ever added equals fills
   + cancels + currently-resting.
5. ``queue_position_monotonic_under_back_cancels`` — cancelling orders
   behind a given order never makes its queue position worse.

We use the legacy ``hypothesis.strategies.composite`` style so the
strategies stay readable. Random seeds are picked by Hypothesis.
"""

from __future__ import annotations

from typing import Optional

from hypothesis import HealthCheck, given, settings, strategies as st

from src.esmm.sim.lob import LimitOrderBook, Order, OrderSide, OrderType
from src.esmm.sim.matching import MatchEngine

# ----------------------------------------------------------------------
# Strategy helpers
# ----------------------------------------------------------------------
prices_bid = st.sampled_from([95.0, 96.0, 97.0, 98.0, 99.0, 100.0])
prices_ask = st.sampled_from([101.0, 102.0, 103.0, 104.0, 105.0, 106.0])
sizes = st.sampled_from([1.0, 2.0, 5.0, 10.0])
owners = st.sampled_from(["alice", "bob", "carol"])


def _fresh_bid(book: LimitOrderBook, price: float, size: float, owner: str) -> Order:
    return Order(
        order_id=book.next_order_id(),
        symbol=book.symbol,
        side=OrderSide.BUY,
        price=price,
        size=size,
        ts=0.0,
        owner_id=owner,
        order_type=OrderType.LIMIT,
    )


def _fresh_ask(book: LimitOrderBook, price: float, size: float, owner: str) -> Order:
    return Order(
        order_id=book.next_order_id(),
        symbol=book.symbol,
        side=OrderSide.SELL,
        price=price,
        size=size,
        ts=0.0,
        owner_id=owner,
        order_type=OrderType.LIMIT,
    )


# ----------------------------------------------------------------------
# Property 1 — bids stay descending under random add/cancel.
# ----------------------------------------------------------------------
@settings(max_examples=60, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.sampled_from(["add_bid", "cancel"]),
            prices_bid,
            sizes,
            owners,
        ),
        min_size=0,
        max_size=40,
    )
)
def test_bids_sorted_descending(ops) -> None:
    book = LimitOrderBook("SPY")
    live_ids: list[int] = []
    for kind, p_bid, size, owner in ops:
        if kind == "add_bid":
            o = _fresh_bid(book, p_bid, size, owner)
            book.add(o)
            live_ids.append(o.order_id)
        elif kind == "cancel" and live_ids:
            target = live_ids.pop(0)
            book.cancel(target)
        bp = book._bid_prices
        assert bp == sorted(bp, reverse=True)


# ----------------------------------------------------------------------
# Property 2 — asks stay ascending under random add/cancel.
# ----------------------------------------------------------------------
@settings(max_examples=60, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.sampled_from(["add_ask", "cancel"]),
            prices_ask,
            sizes,
            owners,
        ),
        min_size=0,
        max_size=40,
    )
)
def test_asks_sorted_ascending(ops) -> None:
    book = LimitOrderBook("SPY")
    live_ids: list[int] = []
    for kind, p_ask, size, owner in ops:
        if kind == "add_ask":
            o = _fresh_ask(book, p_ask, size, owner)
            book.add(o)
            live_ids.append(o.order_id)
        elif kind == "cancel" and live_ids:
            target = live_ids.pop(0)
            book.cancel(target)
        ap = book._ask_prices
        assert ap == sorted(ap)


# ----------------------------------------------------------------------
# Property 3 — random aggressive flow through MatchEngine never leaves
# the book crossed.
# ----------------------------------------------------------------------
@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    st.lists(
        st.tuples(
            st.sampled_from(["maker_bid", "maker_ask", "taker_buy", "taker_sell"]),
            prices_bid,
            prices_ask,
            sizes,
            owners,
        ),
        min_size=0,
        max_size=40,
    )
)
def test_no_crossed_book_when_matching_first(ops) -> None:
    book = LimitOrderBook("SPY")
    eng = MatchEngine(book, self_trade_prevention=True)
    for kind, p_bid, p_ask, size, owner in ops:
        if kind == "maker_bid":
            # Run incoming through matcher: if it crosses the ask, it
            # gets filled; otherwise it rests.
            eng.match(_fresh_bid(book, p_bid, size, owner))
        elif kind == "maker_ask":
            eng.match(_fresh_ask(book, p_ask, size, owner))
        elif kind == "taker_buy":
            # Aggressive buy: limit at top of ask range so it crosses.
            o = _fresh_bid(book, 106.0, size, owner)
            eng.match(o)
        elif kind == "taker_sell":
            o = _fresh_ask(book, 95.0, size, owner)
            eng.match(o)
        bb = book.best_bid()
        ba = book.best_ask()
        if bb is not None and ba is not None:
            assert bb < ba, f"crossed book after {kind}: bid={bb} ask={ba}"


# ----------------------------------------------------------------------
# Property 4 — conservation of size.
# ----------------------------------------------------------------------
@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    st.lists(
        st.tuples(
            st.sampled_from(["maker_bid", "maker_ask", "taker_buy", "taker_sell", "cancel"]),
            prices_bid,
            prices_ask,
            sizes,
            owners,
        ),
        min_size=0,
        max_size=40,
    )
)
def test_conservation_of_size(ops) -> None:
    """sum_added - sum_filled - sum_cancelled == sum_in_book."""
    book = LimitOrderBook("SPY")
    eng = MatchEngine(book, self_trade_prevention=False)

    sum_added = 0.0
    sum_filled = 0.0
    sum_cancelled = 0.0
    resting_ids: list[int] = []

    for kind, p_bid, p_ask, size, owner in ops:
        if kind == "maker_bid":
            o = _fresh_bid(book, p_bid, size, owner)
            sum_added += o.size
            res = eng.match(o)
            sum_filled += sum(f[3] for f in res.fills)
            # The "remainder" of a LIMIT was placed in book — track id.
            if res.remainder > 0:
                resting_ids.append(o.order_id)
        elif kind == "maker_ask":
            o = _fresh_ask(book, p_ask, size, owner)
            sum_added += o.size
            res = eng.match(o)
            sum_filled += sum(f[3] for f in res.fills)
            if res.remainder > 0:
                resting_ids.append(o.order_id)
        elif kind == "taker_buy":
            o = _fresh_bid(book, 106.0, size, owner)
            sum_added += o.size
            res = eng.match(o)
            sum_filled += sum(f[3] for f in res.fills)
            if res.remainder > 0:
                resting_ids.append(o.order_id)
        elif kind == "taker_sell":
            o = _fresh_ask(book, 95.0, size, owner)
            sum_added += o.size
            res = eng.match(o)
            sum_filled += sum(f[3] for f in res.fills)
            if res.remainder > 0:
                resting_ids.append(o.order_id)
        elif kind == "cancel" and resting_ids:
            target = resting_ids.pop(0)
            order = book._orders_by_id.get(target)
            # Fills are double-counted on both sides; we only count
            # aggressor adds and cancel the remaining residual.
            if order is not None and not order.cancelled and order.remaining > 0:
                sum_cancelled += order.remaining
                book.cancel(target)

    # Whatever is left in the book.
    in_book = 0.0
    for queue in list(book._bids.values()) + list(book._asks.values()):
        in_book += sum(o.remaining for o in queue)

    # Each fill is between an aggressor (already counted in sum_added)
    # and a resting maker (also counted, since the maker was added in a
    # prior iteration). So the "consumed" total is 2*sum_filled.
    consumed = 2 * sum_filled
    assert abs(sum_added - consumed - sum_cancelled - in_book) < 1e-9, (
        f"conservation violated: added={sum_added} consumed={consumed} "
        f"cancelled={sum_cancelled} in_book={in_book}"
    )


# ----------------------------------------------------------------------
# Property 5 — queue position monotonic under back cancels.
# ----------------------------------------------------------------------
@settings(max_examples=50, deadline=None)
@given(
    st.lists(sizes, min_size=2, max_size=10),
    st.integers(min_value=0, max_value=9),
)
def test_queue_position_monotonic_under_back_cancels(queue_sizes, target_idx) -> None:
    """Cancelling orders BEHIND yours never increases your position."""
    book = LimitOrderBook("SPY")
    ids: list[int] = []
    for s in queue_sizes:
        o = _fresh_bid(book, 100.0, s, "alice")
        book.add(o)
        ids.append(o.order_id)

    target_idx = min(target_idx, len(ids) - 1)
    target_id = ids[target_idx]
    initial_pos = book.queue_position(target_id)
    assert initial_pos == target_idx + 1

    # Cancel all orders BEHIND the target (higher index).
    for behind_id in ids[target_idx + 1 :]:
        book.cancel(behind_id)
        new_pos = book.queue_position(target_id)
        assert new_pos is not None
        # Cancels behind us never push us back.
        assert new_pos <= initial_pos
        # In fact they shouldn't change our position at all.
        assert new_pos == initial_pos
