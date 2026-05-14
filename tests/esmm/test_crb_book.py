"""Tests for the multi-symbol CRB extension (internalise_book)."""

from __future__ import annotations

import pytest

from src.esmm.crb import CentralRiskBook
from src.esmm.schemas import CRBBookFlow, OrderBookLevel, OrderBookSnapshot


def _snap(symbol: str, mid: float, spread: float = 0.10) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        ts=0.0,
        symbol=symbol,
        bids=[OrderBookLevel(price=mid - spread / 2, size=500)],
        asks=[OrderBookLevel(price=mid + spread / 2, size=500)],
    )


def test_internalise_book_handles_multiple_symbols():
    crb = CentralRiskBook()
    snaps = {
        "SPY": _snap("SPY", 500.0),
        "QQQ": _snap("QQQ", 400.0),
    }
    flows = [
        CRBBookFlow(symbol="SPY", incoming_buys=200, incoming_sells=150),
        CRBBookFlow(symbol="QQQ", incoming_buys=100, incoming_sells=300),
    ]
    result = crb.internalise_book(snaps, flows)
    assert len(result.per_symbol) == 2
    # SPY: matched 150 → notional 150 * 500 = 75_000; residual buy = 50 * 500 = 25_000
    # QQQ: matched 100 → notional 100 * 400 = 40_000; residual sell = 200 * 400 = 80_000
    assert result.total_internalised_notional == pytest.approx(115_000.0)
    assert result.total_residual_buy_notional == pytest.approx(25_000.0)
    assert result.total_residual_sell_notional == pytest.approx(80_000.0)


def test_internalise_book_skips_symbol_without_snapshot():
    crb = CentralRiskBook()
    snaps = {"SPY": _snap("SPY", 500.0)}
    flows = [
        CRBBookFlow(symbol="SPY", incoming_buys=100, incoming_sells=100),
        CRBBookFlow(symbol="MISSING", incoming_buys=99, incoming_sells=99),
    ]
    result = crb.internalise_book(snaps, flows)
    assert len(result.per_symbol) == 1
    assert result.per_symbol[0].symbol == "SPY"


def test_internalise_book_savings_weighted_by_notional():
    """A small high-spread match should not dominate a large low-spread match."""
    crb = CentralRiskBook()
    snaps = {
        "BIG": _snap("BIG", 100.0, spread=0.10),    # 10 bps spread
        "SMALL": _snap("SMALL", 100.0, spread=1.00),  # 100 bps spread
    }
    flows = [
        CRBBookFlow(symbol="BIG", incoming_buys=10_000, incoming_sells=10_000),
        CRBBookFlow(symbol="SMALL", incoming_buys=10, incoming_sells=10),
    ]
    result = crb.internalise_book(snaps, flows)
    # Notional-weighted: BIG dominates → average should be near 10 bps, not 55 bps.
    assert result.total_estimated_savings_bps_weighted < 20.0
    assert result.total_estimated_savings_bps_weighted > 5.0


def test_internalise_book_empty_flow_returns_empty_result():
    crb = CentralRiskBook()
    result = crb.internalise_book({}, [])
    assert result.per_symbol == []
    assert result.total_internalised_notional == 0.0
    assert result.total_estimated_savings_bps_weighted == 0.0


def test_internalise_book_residual_split_is_signed_correctly():
    crb = CentralRiskBook()
    snaps = {"X": _snap("X", 100.0)}
    flows = [CRBBookFlow(symbol="X", incoming_buys=500, incoming_sells=200)]
    result = crb.internalise_book(snaps, flows)
    # Residual is +300 (net buy); should land in residual_buy_notional, not sell.
    assert result.total_residual_buy_notional == pytest.approx(30_000.0)
    assert result.total_residual_sell_notional == 0.0
