"""Tests for the OrderBookSnapshot sortedness validator.

The validator's job: catch malformed feeds from real-data adapters before
they reach quote/backtest code. Engine-tolerated degenerate inputs (negative
prices, crossed books) must still be constructible via `model_construct`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.esmm.schemas import OrderBookLevel, OrderBookSnapshot


def _level(p: float, s: float = 100.0) -> OrderBookLevel:
    return OrderBookLevel(price=p, size=s)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_valid_book_constructs_successfully():
    snap = OrderBookSnapshot(
        ts=0.0,
        symbol="SPY",
        bids=[_level(100.0), _level(99.5), _level(99.0)],
        asks=[_level(100.5), _level(101.0), _level(101.5)],
    )
    assert snap.best_bid == 100.0
    assert snap.best_ask == 100.5


def test_single_level_book_is_valid():
    snap = OrderBookSnapshot(
        ts=0.0, symbol="X", bids=[_level(99.5)], asks=[_level(100.5)]
    )
    assert snap.best_bid == 99.5
    assert snap.best_ask == 100.5


# ---------------------------------------------------------------------------
# Sort-order enforcement — the critical safety net
# ---------------------------------------------------------------------------


def test_rejects_bids_not_descending():
    with pytest.raises(ValidationError, match="bids must be strictly descending"):
        OrderBookSnapshot(
            ts=0.0, symbol="X",
            bids=[_level(99.0), _level(100.0)],  # ascending, wrong
            asks=[_level(101.0)],
        )


def test_rejects_asks_not_ascending():
    with pytest.raises(ValidationError, match="asks must be strictly ascending"):
        OrderBookSnapshot(
            ts=0.0, symbol="X",
            bids=[_level(99.0)],
            asks=[_level(101.0), _level(100.5)],  # descending, wrong
        )


def test_rejects_duplicate_bid_prices():
    """Strictly descending — equality not allowed."""
    with pytest.raises(ValidationError):
        OrderBookSnapshot(
            ts=0.0, symbol="X",
            bids=[_level(99.5), _level(99.5)],
            asks=[_level(100.5)],
        )


def test_rejects_duplicate_ask_prices():
    with pytest.raises(ValidationError):
        OrderBookSnapshot(
            ts=0.0, symbol="X",
            bids=[_level(99.5)],
            asks=[_level(100.5), _level(100.5)],
        )


# ---------------------------------------------------------------------------
# Empty sides
# ---------------------------------------------------------------------------


def test_rejects_empty_bids():
    with pytest.raises(ValidationError, match="at least one bid"):
        OrderBookSnapshot(ts=0.0, symbol="X", bids=[], asks=[_level(100.5)])


def test_rejects_empty_asks():
    with pytest.raises(ValidationError, match="at least one"):
        OrderBookSnapshot(ts=0.0, symbol="X", bids=[_level(99.5)], asks=[])


# ---------------------------------------------------------------------------
# Engine-tolerated degenerate cases — must still construct via model_construct
# ---------------------------------------------------------------------------


def test_model_construct_bypasses_validator_for_negative_prices():
    """Defensive engine code is unit-tested against malformed inputs;
    `model_construct` is the escape hatch the engine tests use."""
    snap = OrderBookSnapshot.model_construct(
        ts=0.0, symbol="X",
        bids=[_level(-1.0)], asks=[_level(1.0)],
    )
    assert snap.best_bid == -1.0


def test_model_construct_bypasses_validator_for_crossed_book():
    snap = OrderBookSnapshot.model_construct(
        ts=0.0, symbol="X",
        bids=[_level(101.0)], asks=[_level(99.0)],
    )
    assert snap.best_bid > snap.best_ask  # crossed


# ---------------------------------------------------------------------------
# Synthetic generator output is always validator-clean
# ---------------------------------------------------------------------------


def test_synthetic_generator_passes_validator_for_many_seeds():
    """The synthetic GBM book must never produce out-of-order depth — if it
    does we want this test to fail loudly so we don't quietly ship bad data."""
    from src.esmm.synthetic import generate_order_book_path

    for seed in [1, 7, 42, 99, 12345]:
        snaps = generate_order_book_path(n_snaps=50, levels=5, seed=seed)
        # Re-validate by round-tripping each snapshot through the constructor
        for s in snaps:
            re_validated = OrderBookSnapshot(
                ts=s.ts, symbol=s.symbol, bids=s.bids, asks=s.asks
            )
            assert re_validated.best_bid < re_validated.best_ask
