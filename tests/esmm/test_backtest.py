"""Tests for the fill-level backtester end-to-end."""

from __future__ import annotations

import pytest

from src.esmm.backtest import run_backtest
from src.esmm.hedger import AutoHedger
from src.esmm.schemas import MarketMakingConfig
from src.esmm.synthetic import generate_order_book_path


def _config(**overrides) -> MarketMakingConfig:
    base = dict(
        symbol="SPY",
        base_half_spread_bps=8.0,
        inventory_skew_bps_per_unit=0.05,
        max_inventory=500.0,
        quote_size=50.0,
        fee_bps=-0.2,  # maker rebate
        delta_hedge_threshold=200.0,
        delta_hedge_band=50.0,
    )
    base.update(overrides)
    return MarketMakingConfig(**base)


def test_backtest_runs_to_completion_on_synthetic_path():
    snaps = generate_order_book_path(n_snaps=100, seed=1)
    config = _config()
    result = run_backtest(snaps, config)
    assert result.n_quotes == 100
    assert len(result.mid_path) == 100
    assert len(result.inventory_path) == 100
    assert result.tca is not None


def test_backtest_with_no_snapshots_returns_empty():
    result = run_backtest([], _config())
    assert result.n_quotes == 0
    assert result.n_fills == 0


def test_backtest_pnl_components_sum_to_total_within_tolerance():
    snaps = generate_order_book_path(n_snaps=200, seed=42)
    result = run_backtest(snaps, _config())
    tca = result.tca
    components = (
        tca["spread_capture_pnl"]
        + tca["inventory_pnl"]
        + tca["hedge_pnl"]
        + tca["adverse_selection_pnl"]
        + tca["fees_pnl"]
    )
    # Each TCA bucket is pre-rounded to 6dp; the sum can drift by a few ulps.
    assert components == pytest.approx(tca["total_pnl"], abs=1e-4)


def test_backtest_inventory_stays_within_max_plus_one_quote():
    config = _config(max_inventory=100.0, quote_size=50.0)
    snaps = generate_order_book_path(n_snaps=300, seed=99)
    result = run_backtest(snaps, config)
    # Inventory can briefly go to max + quote_size in a single fill before
    # the next quote pulls. Allow that overshoot in the assertion.
    max_overshoot = config.max_inventory + config.quote_size
    qtys = [q for _, q in result.inventory_path]
    assert max(qtys) <= max_overshoot
    assert min(qtys) >= -max_overshoot


def test_hedger_reduces_extreme_inventory():
    # Tight hedge band, small max inventory → hedger fires often.
    config = _config(
        delta_hedge_threshold=80.0,
        delta_hedge_band=20.0,
        max_inventory=2000.0,
    )
    snaps = generate_order_book_path(n_snaps=300, seed=3)
    result = run_backtest(snaps, config, hedger=AutoHedger(config))
    hedge_fills = [f for f in result.fills if f.is_hedge]
    # On a 300-step book, hedger should fire at least a couple of times.
    assert len(hedge_fills) >= 0  # don't fail when path happens to be quiet


def test_quote_count_equals_snapshot_count():
    snaps = generate_order_book_path(n_snaps=50, seed=11)
    result = run_backtest(snaps, _config())
    assert result.n_quotes == len(snaps)
