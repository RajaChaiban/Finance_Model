"""Tests for the Central Risk Book simulator."""

from __future__ import annotations

import pytest

from src.esmm.crb import CentralRiskBook
from src.esmm.schemas import OrderBookLevel, OrderBookSnapshot


def _snap(bid: float = 99.95, ask: float = 100.05) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        ts=0.0,
        symbol="SPY",
        bids=[OrderBookLevel(price=bid, size=500)],
        asks=[OrderBookLevel(price=ask, size=500)],
    )


def test_perfect_overlap_internalises_fully():
    crb = CentralRiskBook(internalisation_cap_pct=1.0)
    result = crb.internalise(_snap(), incoming_buys=1000, incoming_sells=1000)
    assert result.internalised == 1000
    assert result.residual_to_street == 0


def test_partial_overlap_internalises_min_and_residual_signed():
    crb = CentralRiskBook(internalisation_cap_pct=1.0)
    result = crb.internalise(_snap(), incoming_buys=1500, incoming_sells=1000)
    assert result.internalised == 1000
    assert result.residual_to_street == 500  # net buy still goes to street


def test_internalisation_cap_limits_matching():
    crb = CentralRiskBook(internalisation_cap_pct=0.6)
    result = crb.internalise(_snap(), incoming_buys=1000, incoming_sells=1000)
    assert result.internalised == 600


def test_no_overlap_zero_internalisation():
    crb = CentralRiskBook()
    result = crb.internalise(_snap(), incoming_buys=1000, incoming_sells=0)
    assert result.internalised == 0
    assert result.residual_to_street == 1000


def test_savings_in_bps_reflects_street_spread():
    crb = CentralRiskBook()
    snap = _snap(bid=99.5, ask=100.5)  # 100 bps spread
    result = crb.internalise(snap, incoming_buys=100, incoming_sells=100)
    assert result.estimated_savings_bps == pytest.approx(100.0)


def test_negative_residual_means_net_sell_to_street():
    crb = CentralRiskBook()
    result = crb.internalise(_snap(), incoming_buys=200, incoming_sells=800)
    assert result.residual_to_street == -600  # net sell on street
