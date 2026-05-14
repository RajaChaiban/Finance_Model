"""Tests for the gamma-hedging extension."""

from __future__ import annotations

import pytest

from src.esmm.hedger import AutoHedger
from src.esmm.schemas import MarketMakingConfig, Side


def _config(**overrides) -> MarketMakingConfig:
    base = dict(
        symbol="X",
        delta_hedge_threshold=100.0,
        delta_hedge_band=20.0,
        gamma_hedge_threshold=1000.0,
        gamma_hedge_band=200.0,
    )
    base.update(overrides)
    return MarketMakingConfig(**base)


def test_evaluate_with_gamma_returns_empty_when_both_inside_band():
    h = AutoHedger(_config())
    fills = h.evaluate_with_gamma(ts=0, net_delta=50, net_gamma_dollar=500, hedge_price=100.0)
    assert fills == []


def test_evaluate_with_gamma_emits_only_delta_fill_when_gamma_inside_band():
    h = AutoHedger(_config())
    fills = h.evaluate_with_gamma(ts=0, net_delta=200, net_gamma_dollar=500, hedge_price=100.0)
    assert len(fills) == 1
    assert fills[0].side == Side.SELL


def test_evaluate_with_gamma_emits_only_gamma_fill_when_delta_inside_band():
    h = AutoHedger(_config())
    fills = h.evaluate_with_gamma(ts=0, net_delta=50, net_gamma_dollar=2000, hedge_price=100.0)
    assert len(fills) == 1
    assert fills[0].is_hedge is True
    assert fills[0].counterparty == "gamma_hedge_venue"


def test_evaluate_with_gamma_emits_two_fills_when_both_outside_band():
    h = AutoHedger(_config())
    fills = h.evaluate_with_gamma(ts=0, net_delta=300, net_gamma_dollar=3000, hedge_price=100.0)
    assert len(fills) == 2
    assert all(f.is_hedge for f in fills)


def test_gamma_hedge_disabled_when_threshold_zero():
    h = AutoHedger(_config(gamma_hedge_threshold=0.0))
    fills = h.evaluate_with_gamma(ts=0, net_delta=50, net_gamma_dollar=99_999_999, hedge_price=100.0)
    assert fills == []  # neither delta nor gamma triggered


def test_gamma_hedge_buys_when_short_gamma_dollar():
    h = AutoHedger(_config())
    fills = h.evaluate_with_gamma(ts=0, net_delta=10, net_gamma_dollar=-2000, hedge_price=100.0)
    assert len(fills) == 1
    assert fills[0].side == Side.BUY


def test_gamma_hedge_size_scales_with_excess():
    h = AutoHedger(_config(gamma_hedge_threshold=1000, gamma_hedge_band=100))
    # gamma_dollar = 5000 → excess to band 100 = 4900 / 100 (price) = 49 shares
    fills = h.evaluate_with_gamma(ts=0, net_delta=10, net_gamma_dollar=5000, hedge_price=100.0)
    assert len(fills) == 1
    assert fills[0].size == pytest.approx(49.0)
