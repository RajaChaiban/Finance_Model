"""Order book primitives used by every other eSMM module.

Why these specific functions:
- mid_price: trivial reference, but used everywhere.
- micro_price: size-weighted between bid and ask. Closer to true fair value
  than mid in skewed books — every MM textbook (Stoikov, Cartea, Almgren)
  references it.
- order_book_imbalance (OBI): leading indicator of next price move
  (Cartea & Jaimungal 2015). Single most-cited microstructure feature for MM.
- weighted_mid_price: depth-aware mid using top-N levels.
- spread_bps: width of the touch in bps of mid; quoting decisions key off this.
"""

from __future__ import annotations

import math

from src.esmm.schemas import OrderBookSnapshot


def mid_price(snap: OrderBookSnapshot) -> float:
    return 0.5 * (snap.best_bid + snap.best_ask)


def spread(snap: OrderBookSnapshot) -> float:
    return snap.best_ask - snap.best_bid


def spread_bps(snap: OrderBookSnapshot) -> float:
    m = mid_price(snap)
    if m <= 0:
        return float("nan")
    return 1e4 * spread(snap) / m


def micro_price(snap: OrderBookSnapshot) -> float:
    """Size-weighted price between best bid and best ask.

    micro = (ask_size * bid + bid_size * ask) / (bid_size + ask_size)

    Intuition: if the bid has 10x the size of the ask, the next trade is far
    more likely to lift the offer than hit the bid, so true fair value sits
    closer to the ask than to the mid.
    """
    bs = snap.best_bid_size
    as_ = snap.best_ask_size
    total = bs + as_
    if total <= 0:
        return mid_price(snap)
    return (as_ * snap.best_bid + bs * snap.best_ask) / total


def weighted_mid_price(snap: OrderBookSnapshot, depth: int = 5) -> float:
    """Depth-weighted mid using top-`depth` levels each side.

    Used as a more robust fair-value reference for low-touch instruments.
    """
    bid_levels = snap.bids[:depth]
    ask_levels = snap.asks[:depth]
    bid_notional = sum(l.price * l.size for l in bid_levels)
    bid_size = sum(l.size for l in bid_levels)
    ask_notional = sum(l.price * l.size for l in ask_levels)
    ask_size = sum(l.size for l in ask_levels)
    if bid_size <= 0 or ask_size <= 0:
        return mid_price(snap)
    bid_vwap = bid_notional / bid_size
    ask_vwap = ask_notional / ask_size
    return 0.5 * (bid_vwap + ask_vwap)


def order_book_imbalance(snap: OrderBookSnapshot, depth: int = 1) -> float:
    """OBI on top-`depth` levels. Returns value in [-1, +1].

    Positive = more size on the bid → upward pressure.
    Negative = more size on the offer → downward pressure.
    """
    bid_size = sum(l.size for l in snap.bids[:depth])
    ask_size = sum(l.size for l in snap.asks[:depth])
    total = bid_size + ask_size
    if total <= 0:
        return 0.0
    return (bid_size - ask_size) / total


def book_depth(snap: OrderBookSnapshot, side: str, depth: int = 5) -> float:
    """Total quantity on one side, top-`depth` levels."""
    levels = snap.bids if side == "bid" else snap.asks
    return sum(l.size for l in levels[:depth])


def is_crossed(snap: OrderBookSnapshot) -> bool:
    """A book is crossed when bid >= ask. Should never happen in clean data —
    if it does, the snapshot is stale or corrupted."""
    return snap.best_bid >= snap.best_ask


def log_return(prev_mid: float, curr_mid: float) -> float:
    if prev_mid <= 0 or curr_mid <= 0:
        return 0.0
    return math.log(curr_mid / prev_mid)
