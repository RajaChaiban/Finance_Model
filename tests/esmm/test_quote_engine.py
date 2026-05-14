"""Tests for the QuoteEngine."""

from __future__ import annotations

import pytest

from src.esmm.inventory import InventoryBook
from src.esmm.quote_engine import QuoteEngine
from src.esmm.schemas import Fill, MarketMakingConfig, OrderBookLevel, OrderBookSnapshot, Side


def _config(symbol: str = "TEST", **overrides) -> MarketMakingConfig:
    base = dict(
        symbol=symbol,
        base_half_spread_bps=10.0,
        inventory_skew_bps_per_unit=0.5,
        max_inventory=1000.0,
        quote_size=100.0,
    )
    base.update(overrides)
    return MarketMakingConfig(**base)


def _book(bid: float = 99.5, ask: float = 100.5, bid_size: float = 100.0, ask_size: float = 100.0) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        ts=0.0,
        symbol="TEST",
        bids=[OrderBookLevel(price=bid, size=bid_size)],
        asks=[OrderBookLevel(price=ask, size=ask_size)],
    )


def test_quote_centered_on_micro_when_flat():
    config = _config()
    engine = QuoteEngine(config)
    inv = InventoryBook()
    snap = _book()
    quote = engine.quote(snap, inv)
    # Symmetric book → micro = mid = 100. Half-spread = 10 bps of 100 = 0.1
    assert quote.bid_price == pytest.approx(100.0 - 0.1)
    assert quote.ask_price == pytest.approx(100.0 + 0.1)
    assert quote.bid_size == 100.0
    assert quote.ask_size == 100.0


def test_long_inventory_skews_quotes_lower():
    config = _config()
    engine = QuoteEngine(config)
    inv = InventoryBook()
    snap = _book()
    inv.apply_fill(Fill(ts=0.0, symbol="TEST", side=Side.BUY, price=100.0, size=200.0, fair_value_at_fill=100.0))
    quote = engine.quote(snap, inv)
    # Long 200 → skew = 200 * 0.5 = 100 bps. Both bid and ask shift down.
    expected_skew_amt = 100.0 * 100 * 1e-4  # = 1.0
    expected_bid = 100.0 - 1.0 - 0.1
    expected_ask = 100.0 - 1.0 + 0.1
    assert quote.bid_price == pytest.approx(expected_bid)
    assert quote.ask_price == pytest.approx(expected_ask)


def test_short_inventory_skews_quotes_higher():
    config = _config()
    engine = QuoteEngine(config)
    inv = InventoryBook()
    snap = _book()
    inv.apply_fill(Fill(ts=0.0, symbol="TEST", side=Side.SELL, price=100.0, size=200.0, fair_value_at_fill=100.0))
    quote = engine.quote(snap, inv)
    expected_bid = 100.0 + 1.0 - 0.1
    expected_ask = 100.0 + 1.0 + 0.1
    assert quote.bid_price == pytest.approx(expected_bid)
    assert quote.ask_price == pytest.approx(expected_ask)


def test_max_inventory_pulls_bid_when_too_long():
    config = _config(max_inventory=100.0)
    engine = QuoteEngine(config)
    inv = InventoryBook()
    snap = _book()
    inv.apply_fill(Fill(ts=0.0, symbol="TEST", side=Side.BUY, price=100.0, size=100.0, fair_value_at_fill=100.0))
    quote = engine.quote(snap, inv)
    assert quote.bid_size == 0.0  # don't get longer
    assert quote.ask_size > 0.0


def test_max_inventory_pulls_ask_when_too_short():
    config = _config(max_inventory=100.0)
    engine = QuoteEngine(config)
    inv = InventoryBook()
    snap = _book()
    inv.apply_fill(Fill(ts=0.0, symbol="TEST", side=Side.SELL, price=100.0, size=100.0, fair_value_at_fill=100.0))
    quote = engine.quote(snap, inv)
    assert quote.ask_size == 0.0
    assert quote.bid_size > 0.0


def test_adverse_selection_widens_quotes():
    config = _config()
    engine = QuoteEngine(config)
    inv = InventoryBook()
    snap = _book()
    base = engine.quote(snap, inv)
    wide = engine.quote(snap, inv, adverse_selection_bps=20.0)
    # half_spread_bps grew from 10 → 30; spread tripled.
    assert (wide.ask_price - wide.bid_price) > (base.ask_price - base.bid_price)


def test_symbol_mismatch_raises():
    config = _config(symbol="OTHER")
    engine = QuoteEngine(config)
    inv = InventoryBook()
    snap = _book()
    with pytest.raises(ValueError):
        engine.quote(snap, inv)
