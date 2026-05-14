"""Edge-case + invariant tests authored during the QA review pass.

Each test targets a class of bug a unit-testing engineer would actively
hunt for in a market-making pipeline:
  - degenerate inputs (zero size, zero price, single-snap path)
  - boundary saturation (exactly at max inventory)
  - direct hedger drive (the integration test fires it stochastically)
  - both-side fill in one slot (backtest 96-97 coverage gap)
  - TCA on empty input (early-return path)
  - synthetic.imbalanced_path coverage
  - property invariants: spread bps non-negative, OBI in [-1, +1],
    micro-price between bid and ask, P&L sign symmetry.
"""

from __future__ import annotations

import math

import pytest

from src.esmm.backtest import _check_fills, run_backtest
from src.esmm.crb import CentralRiskBook
from src.esmm.features import FeatureEngine, realized_variance
from src.esmm.hedger import AutoHedger
from src.esmm.inventory import InventoryBook, inventory_skew_bps
from src.esmm.orderbook import (
    book_depth,
    micro_price,
    mid_price,
    order_book_imbalance,
    spread_bps,
    weighted_mid_price,
)
from src.esmm.quote_engine import QuoteEngine
from src.esmm.schemas import (
    Fill,
    MarketMakingConfig,
    OrderBookLevel,
    OrderBookSnapshot,
    Quote,
    Side,
)
from src.esmm.synthetic import generate_order_book_path, imbalanced_path
from src.esmm.tca import attribute_pnl


def _book(bid: float, ask: float, bs: float = 100.0, as_: float = 100.0) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        ts=0.0,
        symbol="TEST",
        bids=[OrderBookLevel(price=bid, size=bs)],
        asks=[OrderBookLevel(price=ask, size=as_)],
    )


# ---------------------------------------------------------------------------
# DEGENERATE INPUTS
# ---------------------------------------------------------------------------


def test_micro_price_falls_back_to_mid_when_both_sizes_zero():
    snap = _book(99.5, 100.5, bs=0.0, as_=0.0)
    assert micro_price(snap) == pytest.approx(mid_price(snap))


def test_weighted_mid_falls_back_to_mid_when_one_side_empty():
    """Cover orderbook.py:65 — bid_size or ask_size <= 0."""
    snap = OrderBookSnapshot(
        ts=0.0,
        symbol="X",
        bids=[OrderBookLevel(price=99.5, size=0.0)],
        asks=[OrderBookLevel(price=100.5, size=100.0)],
    )
    assert weighted_mid_price(snap, depth=1) == pytest.approx(mid_price(snap))


def test_obi_zero_when_both_sides_empty():
    """Cover orderbook.py:81 — total <= 0."""
    snap = _book(99.5, 100.5, bs=0.0, as_=0.0)
    assert order_book_imbalance(snap) == 0.0


def test_spread_bps_nan_when_mid_nonpositive():
    """Cover orderbook.py:32 — defensive against bad data."""
    snap = _book(-1.0, 1.0)
    assert math.isnan(spread_bps(snap))


def test_book_depth_zero_for_empty_side():
    snap = OrderBookSnapshot(
        ts=0.0,
        symbol="X",
        bids=[OrderBookLevel(price=99.5, size=100.0)],
        asks=[OrderBookLevel(price=100.5, size=100.0)],
    )
    assert book_depth(snap, "ask", depth=10) == 100.0
    # Empty side: no levels at all
    snap2 = OrderBookSnapshot(
        ts=0.0,
        symbol="X",
        bids=[OrderBookLevel(price=99.5, size=100.0)],
        asks=[OrderBookLevel(price=100.5, size=0.0)],
    )
    assert book_depth(snap2, "ask", depth=5) == 0.0


def test_realized_variance_zero_with_single_snapshot():
    snap = _book(99.5, 100.5)
    assert realized_variance([snap]) == 0.0


def test_realized_variance_zero_with_two_identical_snapshots():
    """Cover features.py:106 — len(rets) < 2 branch."""
    snap = _book(99.5, 100.5)
    assert realized_variance([snap, snap]) == 0.0


def test_feature_engine_micro_minus_mid_zero_when_mid_zero():
    """Defensive: features.py:45 — m > 0 guard."""
    engine = FeatureEngine()
    # Negative-mid book → m > 0 false → micro_minus_mid_bps must be 0
    snap = OrderBookSnapshot(
        ts=0.0,
        symbol="X",
        bids=[OrderBookLevel(price=-1.0, size=100)],
        asks=[OrderBookLevel(price=1.0, size=100)],
    )
    feats = engine.update(snap)
    assert feats["micro_minus_mid_bps"] == 0.0


def test_attribute_pnl_returns_zero_breakdown_on_empty_input():
    """Cover tca.py:43 — empty fills early return."""
    breakdown = attribute_pnl([], [])
    assert breakdown.total_pnl == 0.0
    assert breakdown.n_fills == 0
    assert breakdown.spread_capture_pnl == 0.0
    assert breakdown.avg_fill_size == 0.0


def test_run_backtest_with_single_snapshot_does_not_crash():
    snap = _book(99.5, 100.5)
    config = MarketMakingConfig(symbol="TEST")
    result = run_backtest([snap], config)
    assert result.n_quotes == 1
    assert result.n_fills == 0  # no prev quote to fill


# ---------------------------------------------------------------------------
# BOUNDARY SATURATION
# ---------------------------------------------------------------------------


def test_inventory_skew_at_exact_cap_is_max_skew():
    """Boundary: inventory == max_inventory should saturate, not overshoot."""
    skew = inventory_skew_bps(1000.0, max_inventory=1000.0, skew_bps_per_unit=0.5)
    assert skew == pytest.approx(500.0)


def test_quote_engine_pulls_bid_at_exact_cap():
    """Boundary: position == max_inventory triggers bid pull (>= comparison)."""
    config = MarketMakingConfig(symbol="X", max_inventory=100.0, quote_size=50.0)
    engine = QuoteEngine(config)
    inv = InventoryBook()
    inv.apply_fill(
        Fill(ts=0, symbol="X", side=Side.BUY, price=100, size=100, fair_value_at_fill=100)
    )
    snap = _book(99.5, 100.5)
    snap = OrderBookSnapshot(
        ts=0.0, symbol="X",
        bids=[OrderBookLevel(price=99.5, size=100)],
        asks=[OrderBookLevel(price=100.5, size=100)],
    )
    quote = engine.quote(snap, inv)
    assert quote.bid_size == 0.0


# ---------------------------------------------------------------------------
# DIRECT HEDGER DRIVE — coverage gap on hedger.py
# ---------------------------------------------------------------------------


def test_hedger_returns_none_inside_band():
    config = MarketMakingConfig(symbol="X", delta_hedge_threshold=100.0, delta_hedge_band=20.0)
    h = AutoHedger(config)
    assert h.evaluate(ts=0, net_delta=50.0, hedge_price=100.0) is None
    assert h.evaluate(ts=0, net_delta=-50.0, hedge_price=100.0) is None
    assert h.evaluate(ts=0, net_delta=0.0, hedge_price=100.0) is None


def test_hedger_sells_when_long_delta_breaches_threshold():
    config = MarketMakingConfig(symbol="X", delta_hedge_threshold=100.0, delta_hedge_band=20.0)
    h = AutoHedger(config)
    fill = h.evaluate(ts=42.0, net_delta=200.0, hedge_price=100.0)
    assert fill is not None
    assert fill.side == Side.SELL
    assert fill.size == pytest.approx(180.0)  # 200 - 20
    assert fill.price == 100.0
    assert fill.is_hedge is True
    assert fill.ts == 42.0


def test_hedger_buys_when_short_delta_breaches_threshold():
    config = MarketMakingConfig(symbol="X", delta_hedge_threshold=100.0, delta_hedge_band=20.0)
    h = AutoHedger(config)
    fill = h.evaluate(ts=99.0, net_delta=-300.0, hedge_price=50.0)
    assert fill is not None
    assert fill.side == Side.BUY
    assert fill.size == pytest.approx(280.0)  # |-300 - (-20)| = 280
    assert fill.is_hedge is True


def test_hedger_supports_distinct_hedge_symbol():
    config = MarketMakingConfig(symbol="OPTION", delta_hedge_threshold=10, delta_hedge_band=2)
    h = AutoHedger(config, hedge_symbol="UNDERLIER_FUT")
    fill = h.evaluate(ts=0, net_delta=50, hedge_price=500.0)
    assert fill.symbol == "UNDERLIER_FUT"


# ---------------------------------------------------------------------------
# BACKTEST: BOTH-SIDES-CROSS (coverage gap 96-97)
# ---------------------------------------------------------------------------


def test_check_fills_both_sides_filled_when_book_collapses_through_quote():
    """If the next snapshot has best_ask <= our_bid AND best_bid >= our_ask,
    both sides fill (very fast move)."""
    quote = Quote(
        ts=0.0, symbol="X",
        bid_price=99.0, bid_size=10.0,
        ask_price=101.0, ask_size=10.0,
        fair_value=100.0,
    )
    # Construct a snapshot that satisfies both crossing conditions.
    # next_best_ask <= 99 (we get hit on bid) AND next_best_bid >= 101 (we get lifted).
    # That requires next_best_bid >= 101 > 99 >= next_best_ask → bid > ask in next snap (crossed).
    next_snap = OrderBookSnapshot(
        ts=1.0, symbol="X",
        bids=[OrderBookLevel(price=101.0, size=50)],
        asks=[OrderBookLevel(price=99.0, size=50)],
    )
    config = MarketMakingConfig(symbol="X")
    fills = _check_fills(quote, next_snap, prev_mid=100.0, config=config)
    assert len(fills) == 2
    sides = {f.side for f in fills}
    assert sides == {Side.BUY, Side.SELL}


def test_check_fills_returns_empty_when_quote_size_zero_on_both_sides():
    quote = Quote(
        ts=0.0, symbol="X",
        bid_price=99.0, bid_size=0.0,
        ask_price=101.0, ask_size=0.0,
        fair_value=100.0,
    )
    next_snap = _book(98.0, 102.0)  # would cross both quotes if size > 0
    next_snap = OrderBookSnapshot(
        ts=1.0, symbol="X",
        bids=[OrderBookLevel(price=98.0, size=50)],
        asks=[OrderBookLevel(price=102.0, size=50)],
    )
    fills = _check_fills(quote, next_snap, prev_mid=100.0, config=MarketMakingConfig(symbol="X"))
    assert fills == []


# ---------------------------------------------------------------------------
# SYNTHETIC GENERATOR — imbalanced_path coverage
# ---------------------------------------------------------------------------


def test_imbalanced_path_bid_heavy_has_bigger_bid_size():
    bid_heavy = imbalanced_path(n_snaps=5, bias="bid_heavy", seed=1)
    balanced = generate_order_book_path(n_snaps=5, seed=1)
    assert bid_heavy[0].best_bid_size > balanced[0].best_bid_size
    assert bid_heavy[0].best_ask_size == balanced[0].best_ask_size


def test_imbalanced_path_ask_heavy_has_bigger_ask_size():
    ask_heavy = imbalanced_path(n_snaps=5, bias="ask_heavy", seed=2)
    balanced = generate_order_book_path(n_snaps=5, seed=2)
    assert ask_heavy[0].best_ask_size > balanced[0].best_ask_size
    assert ask_heavy[0].best_bid_size == balanced[0].best_bid_size


def test_imbalanced_path_unknown_bias_returns_balanced():
    out = imbalanced_path(n_snaps=3, bias="other", seed=3)
    assert len(out) == 3


# ---------------------------------------------------------------------------
# CRB EDGE CASES
# ---------------------------------------------------------------------------


def test_crb_zero_inputs_no_internalisation_no_residual():
    crb = CentralRiskBook()
    snap = _book(99.95, 100.05)
    snap = OrderBookSnapshot(
        ts=0.0, symbol="X",
        bids=[OrderBookLevel(price=99.95, size=100)],
        asks=[OrderBookLevel(price=100.05, size=100)],
    )
    result = crb.internalise(snap, incoming_buys=0, incoming_sells=0)
    assert result.internalised == 0
    assert result.residual_to_street == 0


def test_crb_internalisation_cap_zero_disables_netting():
    """If cap=0, the CRB is a pass-through and no flow is internalised."""
    crb = CentralRiskBook(internalisation_cap_pct=0.0)
    snap = OrderBookSnapshot(
        ts=0.0, symbol="X",
        bids=[OrderBookLevel(price=99.95, size=500)],
        asks=[OrderBookLevel(price=100.05, size=500)],
    )
    result = crb.internalise(snap, incoming_buys=1000, incoming_sells=1000)
    assert result.internalised == 0


# ---------------------------------------------------------------------------
# PROPERTY-STYLE INVARIANTS
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 2, 3, 7, 13, 21, 42, 99])
def test_property_obi_bounded_minus_one_to_plus_one_across_paths(seed: int):
    snaps = generate_order_book_path(n_snaps=20, seed=seed)
    for s in snaps:
        for d in (1, 3, 5):
            obi = order_book_imbalance(s, depth=d)
            assert -1.0 <= obi <= 1.0


@pytest.mark.parametrize("seed", [1, 2, 7, 42])
def test_property_micro_price_between_best_bid_and_best_ask(seed: int):
    snaps = generate_order_book_path(n_snaps=20, seed=seed)
    for s in snaps:
        mp = micro_price(s)
        assert s.best_bid <= mp <= s.best_ask


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_property_spread_bps_nonnegative_on_clean_book(seed: int):
    snaps = generate_order_book_path(n_snaps=20, seed=seed)
    for s in snaps:
        assert spread_bps(s) >= 0


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_property_inventory_round_trip_pnl_equals_price_delta(seed: int):
    """Buy then sell the same qty: realised P&L == (sell - buy) * qty exactly,
    regardless of ordering of intermediate marks."""
    book = InventoryBook()
    book.apply_fill(Fill(ts=0, symbol="X", side=Side.BUY, price=100.0 + seed, size=10, fair_value_at_fill=100))
    pos = book.apply_fill(Fill(ts=1, symbol="X", side=Side.SELL, price=110.0 + seed, size=10, fair_value_at_fill=110))
    assert pos.realized_pnl == pytest.approx(10.0 * 10.0)  # always $10 profit per share


@pytest.mark.parametrize("inv", [-1000, -500, -100, 0, 100, 500, 1000])
def test_property_quote_bid_strictly_below_ask(inv: float):
    config = MarketMakingConfig(
        symbol="X",
        base_half_spread_bps=8.0,
        inventory_skew_bps_per_unit=0.5,
        max_inventory=2000.0,
    )
    engine = QuoteEngine(config)
    book = InventoryBook()
    if inv != 0:
        book.apply_fill(
            Fill(
                ts=0, symbol="X",
                side=Side.BUY if inv > 0 else Side.SELL,
                price=100.0, size=abs(inv), fair_value_at_fill=100.0,
            )
        )
    snap = OrderBookSnapshot(
        ts=0.0, symbol="X",
        bids=[OrderBookLevel(price=99.5, size=100)],
        asks=[OrderBookLevel(price=100.5, size=100)],
    )
    quote = engine.quote(snap, book)
    assert quote.bid_price < quote.ask_price


def test_inventory_total_pnl_with_no_marks_returns_realised_only():
    book = InventoryBook()
    book.apply_fill(Fill(ts=0, symbol="A", side=Side.BUY, price=100, size=10, fair_value_at_fill=100))
    book.apply_fill(Fill(ts=1, symbol="A", side=Side.SELL, price=110, size=10, fair_value_at_fill=110))
    # No marks dict at all → pure realised
    assert book.total_pnl({}) == pytest.approx(100.0)
    # Still has no open position so MTM should be 0 anyway.
    assert book.total_pnl({"A": 999.0}) == pytest.approx(100.0)


def test_inventory_apply_fill_does_not_mutate_input_fill():
    """Frozen Pydantic schemas should make this trivially true; assert anyway."""
    fill = Fill(ts=0, symbol="X", side=Side.BUY, price=100, size=10, fair_value_at_fill=100)
    book = InventoryBook()
    book.apply_fill(fill)
    assert fill.price == 100  # unchanged
    assert fill.size == 10


# ---------------------------------------------------------------------------
# 100% COVERAGE TOPPING-OFF — small targeted misses
# ---------------------------------------------------------------------------


def test_inventory_book_positions_returns_all_symbols():
    """Cover inventory.py:29 — positions() accessor."""
    book = InventoryBook()
    book.apply_fill(Fill(ts=0, symbol="A", side=Side.BUY, price=100, size=10, fair_value_at_fill=100))
    book.apply_fill(Fill(ts=0, symbol="B", side=Side.SELL, price=50, size=5, fair_value_at_fill=50))
    positions = book.positions()
    symbols = {p.symbol for p in positions}
    assert symbols == {"A", "B"}


def test_rolling_stats_std_is_sqrt_of_variance():
    """Cover features.py:45 — std property."""
    from src.esmm.features import RollingStats
    rs = RollingStats(window=10)
    for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
        rs.add(v)
    assert rs.std == pytest.approx(math.sqrt(rs.variance))


def test_feature_engine_signed_flow_with_recent_fills():
    """Cover features.py:76-77 — recent_fills branch."""
    snap = OrderBookSnapshot(
        ts=0.0, symbol="X",
        bids=[OrderBookLevel(price=99.5, size=100)],
        asks=[OrderBookLevel(price=100.5, size=100)],
    )
    fills = [
        Fill(ts=0.0, symbol="X", side=Side.BUY, price=100, size=10, fair_value_at_fill=100),
        Fill(ts=0.0, symbol="X", side=Side.SELL, price=100, size=4, fair_value_at_fill=100),
    ]
    engine = FeatureEngine()
    feats = engine.update(snap, recent_fills=fills)
    # The signed_flow stat is a rolling MEAN — first call so equals the only sample.
    assert feats["signed_flow"] == pytest.approx(6.0)


def test_backtest_executes_hedge_fill_when_inventory_explodes():
    """Force-fire the hedger in a backtest. Cover backtest.py:96-97 (the
    inventory.apply_fill + fills.append for hedge fills)."""
    config = MarketMakingConfig(
        symbol="SPY",
        base_half_spread_bps=2.0,            # tight spread → more fills
        inventory_skew_bps_per_unit=0.0,     # no skew → quotes don't pull from inventory
        max_inventory=1_000_000.0,           # never pull
        quote_size=200.0,
        fee_bps=0.0,
        delta_hedge_threshold=10.0,           # tiny band → hedger fires fast
        delta_hedge_band=2.0,
    )
    snaps = generate_order_book_path(n_snaps=300, sigma_per_step=0.002, seed=12345)
    result = run_backtest(snaps, config)
    hedge_fills = [f for f in result.fills if f.is_hedge]
    assert len(hedge_fills) > 0, "hedger should have fired at least once"


def test_attribute_pnl_buckets_hedge_fill_into_hedge_pnl():
    """Cover tca.py:78-79 — is_hedge branch in attribution."""
    snaps = [
        OrderBookSnapshot(
            ts=float(i), symbol="X",
            bids=[OrderBookLevel(price=99.5, size=100)],
            asks=[OrderBookLevel(price=100.5, size=100)],
        )
        for i in range(20)
    ]
    fills = [
        # A non-hedge fill that earns spread
        Fill(ts=1.0, symbol="X", side=Side.SELL, price=100.5, size=10,
             fair_value_at_fill=100.0, fee_bps=0.0, is_hedge=False),
        # A hedge fill (paying the spread)
        Fill(ts=2.0, symbol="X", side=Side.BUY, price=100.5, size=5,
             fair_value_at_fill=100.0, fee_bps=0.0, is_hedge=True),
    ]
    breakdown = attribute_pnl(fills, snaps)
    # hedge_pnl should be negative (we paid the half-spread on the hedge leg).
    assert breakdown.hedge_pnl < 0
    assert breakdown.n_fills == 2
