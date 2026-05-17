"""Tests for :class:`~src.esmm.sim.participants.market_maker.MarketMakerParticipant`.

These tests exercise the MM participant in isolation (no kernel) by hand-
feeding snapshots, fills, and timestamps. The intent is to nail down the
quoting cadence, inventory accounting, quote-pull behaviour at the
inventory cap, and the auto-hedger handoff.
"""

from __future__ import annotations

import math

import pytest

from src.esmm.schemas import (
    Fill,
    MarketMakingConfig,
    OrderBookLevel,
    OrderBookSnapshot,
    Side,
)
from src.esmm.sim.lob import Order, OrderSide, OrderType
from src.esmm.sim.participants.base import Participant
from src.esmm.sim.participants.market_maker import MarketMakerParticipant


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


def _default_config(
    *,
    symbol: str = "SPY",
    quote_size: float = 100.0,
    max_inventory: float = 1000.0,
    delta_hedge_threshold: float = 50.0,
    delta_hedge_band: float = 10.0,
) -> MarketMakingConfig:
    return MarketMakingConfig(
        symbol=symbol,
        base_half_spread_bps=5.0,
        inventory_skew_bps_per_unit=0.5,
        max_inventory=max_inventory,
        quote_size=quote_size,
        fee_bps=-0.2,
        delta_hedge_threshold=delta_hedge_threshold,
        delta_hedge_band=delta_hedge_band,
    )


# =====================================================================
# Protocol conformance + construction
# =====================================================================
def test_market_maker_is_a_participant() -> None:
    mm = MarketMakerParticipant("mm1", _default_config())
    assert isinstance(mm, Participant)
    assert mm.participant_id == "mm1"
    assert hasattr(mm, "on_book")
    assert hasattr(mm, "on_fill")
    assert hasattr(mm, "decide")


def test_market_maker_rejects_bad_requote_interval() -> None:
    with pytest.raises(ValueError):
        MarketMakerParticipant("mm", _default_config(), requote_interval_sec=-0.1)


# =====================================================================
# decide() behaviour
# =====================================================================
def test_no_orders_before_first_snapshot() -> None:
    mm = MarketMakerParticipant("mm", _default_config())
    assert mm.decide(0.0) == []
    assert mm.decide(1.0) == []


def test_emits_two_limit_orders_on_first_decide() -> None:
    """After a snapshot, the first decide should emit one bid + one ask LIMIT."""
    mm = MarketMakerParticipant("mm", _default_config(), use_hedger=False)
    mm.on_book(_snap(ts=0.0, bid=99.95, ask=100.05))
    out = mm.decide(0.0)
    assert len(out) == 2

    buys = [o for o in out if o.side == OrderSide.BUY]
    sells = [o for o in out if o.side == OrderSide.SELL]
    assert len(buys) == 1
    assert len(sells) == 1
    for o in out:
        assert o.order_type == OrderType.LIMIT
        assert o.owner_id == "mm"
        assert o.symbol == "SPY"
        assert o.order_id == 0
        assert math.isfinite(o.price)


def test_quote_prices_straddle_the_mid() -> None:
    """BID < mid < ASK at zero inventory."""
    cfg = _default_config()
    mm = MarketMakerParticipant("mm", cfg, use_hedger=False)
    mm.on_book(_snap(ts=0.0, bid=99.95, ask=100.05))
    out = mm.decide(0.0)
    bid = next(o for o in out if o.side == OrderSide.BUY).price
    ask = next(o for o in out if o.side == OrderSide.SELL).price
    # mid is at the micro_price of the snapshot — for equal sizes that's
    # exactly the midpoint of best bid/ask.
    assert bid < ask
    assert bid < 100.0 < ask


def test_quote_sizes_equal_config_quote_size() -> None:
    """At zero inventory both quote sizes equal config.quote_size."""
    cfg = _default_config(quote_size=250.0)
    mm = MarketMakerParticipant("mm", cfg, use_hedger=False)
    mm.on_book(_snap(ts=0.0))
    out = mm.decide(0.0)
    for o in out:
        assert o.size == 250.0


def test_on_fill_updates_inventory_buy_then_sell() -> None:
    """BUY fill → +size; SELL fill → -size."""
    mm = MarketMakerParticipant("mm", _default_config())
    assert mm.inventory.get("SPY").quantity == 0.0
    assert mm.n_fills == 0

    mm.on_fill(
        Fill(
            ts=0.1,
            symbol="SPY",
            side=Side.BUY,
            price=99.95,
            size=200.0,
            fair_value_at_fill=100.0,
        )
    )
    assert mm.inventory.get("SPY").quantity == 200.0
    assert mm.n_fills == 1

    mm.on_fill(
        Fill(
            ts=0.2,
            symbol="SPY",
            side=Side.SELL,
            price=100.05,
            size=50.0,
            fair_value_at_fill=100.0,
        )
    )
    assert mm.inventory.get("SPY").quantity == 150.0
    assert mm.n_fills == 2


def test_requote_interval_rate_limits_inside_window() -> None:
    """Two decide() calls inside the requote window emit only once."""
    mm = MarketMakerParticipant(
        "mm", _default_config(), requote_interval_sec=0.1, use_hedger=False
    )
    mm.on_book(_snap(ts=0.0))
    first = mm.decide(0.0)
    assert len(first) == 2  # first tick quotes

    # 50 ms later — still inside the 100 ms window.
    mm.on_book(_snap(ts=0.05))
    second = mm.decide(0.05)
    assert second == []

    # 150 ms after the first quote — now we should requote.
    mm.on_book(_snap(ts=0.15))
    third = mm.decide(0.15)
    assert len(third) == 2


def test_bid_pulled_when_inventory_at_max() -> None:
    """When position.quantity >= max_inventory, no BID is emitted."""
    cfg = _default_config(max_inventory=100.0, quote_size=100.0)
    mm = MarketMakerParticipant("mm", cfg, use_hedger=False)

    # Push us to the cap via a BUY fill of 100.
    mm.on_fill(
        Fill(
            ts=0.0,
            symbol="SPY",
            side=Side.BUY,
            price=99.95,
            size=100.0,
            fair_value_at_fill=100.0,
        )
    )
    assert mm.inventory.get("SPY").quantity == 100.0

    mm.on_book(_snap(ts=0.1))
    out = mm.decide(0.1)
    sides = [o.side for o in out]
    assert OrderSide.BUY not in sides, "bid should be pulled at the cap"
    assert OrderSide.SELL in sides, "ask still active when long the cap"


def test_ask_pulled_when_inventory_at_negative_max() -> None:
    """When position.quantity <= -max_inventory, no ASK is emitted."""
    cfg = _default_config(max_inventory=100.0, quote_size=100.0)
    mm = MarketMakerParticipant("mm", cfg, use_hedger=False)

    mm.on_fill(
        Fill(
            ts=0.0,
            symbol="SPY",
            side=Side.SELL,
            price=100.05,
            size=100.0,
            fair_value_at_fill=100.0,
        )
    )
    assert mm.inventory.get("SPY").quantity == -100.0

    mm.on_book(_snap(ts=0.1))
    out = mm.decide(0.1)
    sides = [o.side for o in out]
    assert OrderSide.SELL not in sides, "ask should be pulled at the short cap"
    assert OrderSide.BUY in sides, "bid still active when short the cap"


def test_hedger_fires_when_inventory_crosses_threshold() -> None:
    """Inventory above delta_hedge_threshold → a MARKET hedge order appears."""
    cfg = _default_config(
        max_inventory=1000.0,
        delta_hedge_threshold=50.0,
        delta_hedge_band=10.0,
    )
    mm = MarketMakerParticipant("mm", cfg, use_hedger=True)

    # Drive position past the hedge threshold.
    mm.on_fill(
        Fill(
            ts=0.0,
            symbol="SPY",
            side=Side.BUY,
            price=99.95,
            size=100.0,
            fair_value_at_fill=100.0,
        )
    )
    assert mm.inventory.get("SPY").quantity == 100.0  # > threshold of 50

    mm.on_book(_snap(ts=0.1))
    out = mm.decide(0.1)

    markets = [o for o in out if o.order_type == OrderType.MARKET]
    assert len(markets) == 1, "expected exactly one hedge MARKET order"
    hedge = markets[0]
    # Long inventory → hedger SELLs to bring us back to band.
    assert hedge.side == OrderSide.SELL
    assert math.isnan(hedge.price)
    assert hedge.owner_id == "mm"
    # Hedge size = |net_delta - band| = |100 - 10| = 90
    assert hedge.size == pytest.approx(90.0)


def test_hedger_silent_when_inventory_within_threshold() -> None:
    """Below threshold → no MARKET orders, just the two LIMIT quotes."""
    cfg = _default_config(delta_hedge_threshold=50.0)
    mm = MarketMakerParticipant("mm", cfg, use_hedger=True)
    mm.on_fill(
        Fill(
            ts=0.0,
            symbol="SPY",
            side=Side.BUY,
            price=99.95,
            size=10.0,  # well under threshold of 50
            fair_value_at_fill=100.0,
        )
    )
    mm.on_book(_snap(ts=0.1))
    out = mm.decide(0.1)
    assert all(o.order_type == OrderType.LIMIT for o in out)


def test_hedger_disabled_emits_no_market_orders() -> None:
    """use_hedger=False — even with huge inventory, no MARKET order."""
    cfg = _default_config(delta_hedge_threshold=50.0)
    mm = MarketMakerParticipant("mm", cfg, use_hedger=False)
    mm.on_fill(
        Fill(
            ts=0.0,
            symbol="SPY",
            side=Side.BUY,
            price=99.95,
            size=500.0,  # way above threshold
            fair_value_at_fill=100.0,
        )
    )
    mm.on_book(_snap(ts=0.1))
    out = mm.decide(0.1)
    assert all(o.order_type == OrderType.LIMIT for o in out)


def test_other_symbol_snapshots_are_ignored() -> None:
    """A snapshot for a different symbol shouldn't trigger quoting."""
    mm = MarketMakerParticipant("mm", _default_config(symbol="SPY"))
    mm.on_book(_snap(ts=0.0, symbol="AAPL"))
    assert mm.decide(0.0) == []


def test_last_quote_is_stored_after_decide() -> None:
    """The most recent Quote is exposed for telemetry."""
    mm = MarketMakerParticipant("mm", _default_config(), use_hedger=False)
    assert mm.last_quote is None
    mm.on_book(_snap(ts=0.0))
    mm.decide(0.0)
    assert mm.last_quote is not None
    assert mm.last_quote.symbol == "SPY"
