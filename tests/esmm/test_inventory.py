"""Tests for inventory accounting + skew function."""

from __future__ import annotations

import pytest

from src.esmm.inventory import InventoryBook, inventory_skew_bps
from src.esmm.schemas import Fill, Side


def _fill(side: Side, price: float, size: float, fee_bps: float = 0.0) -> Fill:
    return Fill(
        ts=0.0,
        symbol="TEST",
        side=side,
        price=price,
        size=size,
        fair_value_at_fill=price,
        fee_bps=fee_bps,
    )


def test_first_buy_creates_long_position_at_fill_price():
    book = InventoryBook()
    pos = book.apply_fill(_fill(Side.BUY, 100.0, 50.0))
    assert pos.quantity == 50.0
    assert pos.avg_cost == 100.0
    assert pos.realized_pnl == 0.0


def test_subsequent_same_side_averages_cost():
    book = InventoryBook()
    book.apply_fill(_fill(Side.BUY, 100.0, 50.0))
    pos = book.apply_fill(_fill(Side.BUY, 110.0, 50.0))
    assert pos.quantity == 100.0
    assert pos.avg_cost == pytest.approx(105.0)


def test_partial_close_books_realized_pnl():
    book = InventoryBook()
    book.apply_fill(_fill(Side.BUY, 100.0, 100.0))  # long 100 @ 100
    pos = book.apply_fill(_fill(Side.SELL, 110.0, 40.0))  # close 40 @ 110
    assert pos.quantity == 60.0
    assert pos.avg_cost == pytest.approx(100.0)
    assert pos.realized_pnl == pytest.approx(40.0 * 10.0)  # 40 shares * 10 profit


def test_position_flip_books_realized_then_opens_residual():
    book = InventoryBook()
    book.apply_fill(_fill(Side.BUY, 100.0, 100.0))  # long 100 @ 100
    pos = book.apply_fill(_fill(Side.SELL, 110.0, 150.0))  # close 100, open 50 short
    assert pos.quantity == -50.0
    assert pos.avg_cost == pytest.approx(110.0)
    assert pos.realized_pnl == pytest.approx(100.0 * 10.0)


def test_short_then_buy_back_at_lower_price_profits():
    book = InventoryBook()
    book.apply_fill(_fill(Side.SELL, 100.0, 50.0))  # short 50 @ 100
    pos = book.apply_fill(_fill(Side.BUY, 90.0, 50.0))  # cover @ 90
    assert pos.quantity == 0.0
    assert pos.realized_pnl == pytest.approx(50.0 * 10.0)


def test_mark_to_market_on_long_long_pnl_with_higher_mark():
    book = InventoryBook()
    book.apply_fill(_fill(Side.BUY, 100.0, 100.0))
    assert book.mark_to_market("TEST", 105.0) == pytest.approx(500.0)
    assert book.mark_to_market("TEST", 95.0) == pytest.approx(-500.0)


def test_total_pnl_sums_realised_and_unrealised():
    book = InventoryBook()
    book.apply_fill(_fill(Side.BUY, 100.0, 100.0))
    book.apply_fill(_fill(Side.SELL, 110.0, 50.0))  # realize 500
    # Remaining 50 long @ 100
    total = book.total_pnl({"TEST": 105.0})
    # realized 500 + unrealized (105-100)*50 = 250 → total 750
    assert total == pytest.approx(750.0)


def test_inventory_skew_zero_when_flat():
    assert inventory_skew_bps(0.0, max_inventory=1000, skew_bps_per_unit=0.5) == 0.0


def test_inventory_skew_caps_at_max():
    over_cap = inventory_skew_bps(2000, max_inventory=1000, skew_bps_per_unit=0.5)
    at_cap = inventory_skew_bps(1000, max_inventory=1000, skew_bps_per_unit=0.5)
    assert over_cap == at_cap == pytest.approx(500.0)


def test_inventory_skew_symmetric_around_zero():
    pos_skew = inventory_skew_bps(500, max_inventory=1000, skew_bps_per_unit=0.5)
    neg_skew = inventory_skew_bps(-500, max_inventory=1000, skew_bps_per_unit=0.5)
    assert pos_skew == -neg_skew
