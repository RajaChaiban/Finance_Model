"""Tests for the risk engine.

Cover:
  * RiskLimits validation
  * Pre-trade blocks: notional, net delta, gamma (when enabled),
    concentration, rate limit
  * Post-trade breaches: daily-loss halt, drawdown warn vs halt,
    inventory age warn
  * Halt is sticky; reset() clears it
  * Breaches accumulate in chronological order
"""

from __future__ import annotations

import math

import pytest

from src.esmm.sim.risk import RiskBreach, RiskEngine, RiskLimits, RiskState


# ---------------------------------------------------------------------------
# RiskLimits validation
# ---------------------------------------------------------------------------
class TestRiskLimitsValidation:
    def test_defaults_construct(self) -> None:
        RiskLimits()

    @pytest.mark.parametrize(
        "field",
        [
            "max_notional_usd",
            "max_net_delta",
            "daily_loss_kill_switch_usd",
            "max_drawdown_pct",
            "inventory_age_sec",
        ],
    )
    def test_negative_rejected(self, field: str) -> None:
        with pytest.raises(ValueError):
            RiskLimits(**{field: -1.0})

    def test_concentration_in_unit_interval(self) -> None:
        with pytest.raises(ValueError):
            RiskLimits(concentration_pct=1.5)
        with pytest.raises(ValueError):
            RiskLimits(concentration_pct=-0.1)

    def test_drawdown_in_unit_interval(self) -> None:
        with pytest.raises(ValueError):
            RiskLimits(max_drawdown_pct=1.5)
        with pytest.raises(ValueError):
            RiskLimits(max_drawdown_pct=-0.5)

    def test_orders_per_sec_positive(self) -> None:
        with pytest.raises(ValueError):
            RiskLimits(max_orders_per_sec=0)
        with pytest.raises(ValueError):
            RiskLimits(max_orders_per_sec=-1)


# ---------------------------------------------------------------------------
# Pre-trade
# ---------------------------------------------------------------------------
def _state(**kwargs) -> RiskState:
    defaults = dict(ts=10.0)
    defaults.update(kwargs)
    return RiskState(**defaults)


class TestPretradeNotional:
    def test_allows_below_cap(self) -> None:
        # concentration_pct=1.0 disables the single-symbol concentration
        # check; this test is only about the notional gate.
        eng = RiskEngine(RiskLimits(max_notional_usd=100_000, concentration_pct=1.0))
        ok, b = eng.check_pretrade(
            participant_id="mm",
            symbol="SPY",
            order_notional_usd=50_000,
            order_delta=0,
            state=_state(gross_notional_usd=20_000),
        )
        assert ok and b is None

    def test_blocks_at_cap(self) -> None:
        eng = RiskEngine(RiskLimits(max_notional_usd=100_000))
        ok, b = eng.check_pretrade(
            participant_id="mm",
            symbol="SPY",
            order_notional_usd=90_000,
            order_delta=0,
            state=_state(gross_notional_usd=20_000),
        )
        assert not ok
        assert b is not None
        assert b.limit_name == "max_notional_usd"
        assert b.severity == "block"
        assert b.actual_value == 110_000

    def test_absolute_value_used(self) -> None:
        # Sells should also count toward gross notional.
        eng = RiskEngine(RiskLimits(max_notional_usd=100_000))
        ok, _ = eng.check_pretrade(
            participant_id="mm",
            symbol="SPY",
            order_notional_usd=-110_000,
            order_delta=0,
            state=_state(),
        )
        assert not ok


class TestPretradeDelta:
    def test_allows_in_band(self) -> None:
        eng = RiskEngine(RiskLimits(max_net_delta=1000, concentration_pct=1.0))
        ok, _ = eng.check_pretrade(
            participant_id="mm",
            symbol="SPY",
            order_notional_usd=10,
            order_delta=500,
            state=_state(net_delta=200),
        )
        assert ok

    def test_blocks_positive_breach(self) -> None:
        eng = RiskEngine(RiskLimits(max_net_delta=1000))
        ok, b = eng.check_pretrade(
            participant_id="mm",
            symbol="SPY",
            order_notional_usd=10,
            order_delta=900,
            state=_state(net_delta=200),
        )
        assert not ok and b.limit_name == "max_net_delta"

    def test_blocks_negative_breach(self) -> None:
        eng = RiskEngine(RiskLimits(max_net_delta=1000))
        ok, b = eng.check_pretrade(
            participant_id="mm",
            symbol="SPY",
            order_notional_usd=10,
            order_delta=-1500,
            state=_state(net_delta=0),
        )
        assert not ok and b.limit_name == "max_net_delta"


class TestPretradeGamma:
    def test_disabled_when_limit_zero(self) -> None:
        eng = RiskEngine(RiskLimits(max_gross_gamma=0, concentration_pct=1.0))
        ok, _ = eng.check_pretrade(
            participant_id="mm",
            symbol="SPY",
            order_notional_usd=10,
            order_delta=0,
            order_gamma=1_000_000,  # huge — would breach if enabled
            state=_state(gross_gamma=0),
        )
        assert ok

    def test_blocks_when_enabled(self) -> None:
        eng = RiskEngine(RiskLimits(max_gross_gamma=100))
        ok, b = eng.check_pretrade(
            participant_id="mm",
            symbol="SPY",
            order_notional_usd=10,
            order_delta=0,
            order_gamma=80,
            state=_state(gross_gamma=50),
        )
        assert not ok and b.limit_name == "max_gross_gamma"


class TestPretradeConcentration:
    def test_blocks_single_symbol_overweight(self) -> None:
        eng = RiskEngine(
            RiskLimits(max_notional_usd=1_000_000, concentration_pct=0.4)
        )
        ok, b = eng.check_pretrade(
            participant_id="mm",
            symbol="NVDA",
            order_notional_usd=400_000,
            order_delta=0,
            state=_state(
                gross_notional_usd=400_000,
                per_symbol_notional={"NVDA": 200_000},
            ),
        )
        # New NVDA notional = 600k, gross = 800k → 75% concentration > 40%
        assert not ok and b.limit_name == "concentration_pct"

    def test_allows_diversified(self) -> None:
        eng = RiskEngine(
            RiskLimits(max_notional_usd=1_000_000, concentration_pct=0.4)
        )
        ok, _ = eng.check_pretrade(
            participant_id="mm",
            symbol="NVDA",
            order_notional_usd=100_000,
            order_delta=0,
            state=_state(
                gross_notional_usd=400_000,
                per_symbol_notional={"NVDA": 100_000},
            ),
        )
        assert ok


class TestPretradeRateLimit:
    def test_blocks_burst(self) -> None:
        eng = RiskEngine(RiskLimits(max_orders_per_sec=3, concentration_pct=1.0))
        for i in range(3):
            ok, _ = eng.check_pretrade(
                participant_id="mm",
                symbol="SPY",
                order_notional_usd=10,
                order_delta=0,
                state=_state(ts=100.0 + i * 0.01),
            )
            assert ok
        # 4th order in the same second should block
        ok, b = eng.check_pretrade(
            participant_id="mm",
            symbol="SPY",
            order_notional_usd=10,
            order_delta=0,
            state=_state(ts=100.05),
        )
        assert not ok and b.limit_name == "max_orders_per_sec"

    def test_window_slides(self) -> None:
        eng = RiskEngine(RiskLimits(max_orders_per_sec=2, concentration_pct=1.0))
        # Two orders at t=100
        eng.check_pretrade(
            participant_id="mm",
            symbol="SPY",
            order_notional_usd=10,
            order_delta=0,
            state=_state(ts=100.0),
        )
        eng.check_pretrade(
            participant_id="mm",
            symbol="SPY",
            order_notional_usd=10,
            order_delta=0,
            state=_state(ts=100.1),
        )
        # At t=101.5 the window has slid past 100.0/100.1 → fresh quota
        ok, _ = eng.check_pretrade(
            participant_id="mm",
            symbol="SPY",
            order_notional_usd=10,
            order_delta=0,
            state=_state(ts=101.5),
        )
        assert ok

    def test_per_participant_independent(self) -> None:
        eng = RiskEngine(RiskLimits(max_orders_per_sec=2, concentration_pct=1.0))
        for _ in range(2):
            eng.check_pretrade(
                participant_id="mm1",
                symbol="SPY",
                order_notional_usd=10,
                order_delta=0,
                state=_state(),
            )
        # mm2 should not be rate-limited by mm1's orders
        ok, _ = eng.check_pretrade(
            participant_id="mm2",
            symbol="SPY",
            order_notional_usd=10,
            order_delta=0,
            state=_state(),
        )
        assert ok


# ---------------------------------------------------------------------------
# Post-trade
# ---------------------------------------------------------------------------
class TestPosttradeDailyLoss:
    def test_kill_switch_trips(self) -> None:
        eng = RiskEngine(RiskLimits(daily_loss_kill_switch_usd=10_000))
        b = eng.check_posttrade(_state(daily_pnl=-12_000, peak_daily_pnl=0))
        assert eng.is_halted
        assert b is not None and b.severity == "halt"
        assert b.limit_name == "daily_loss_kill_switch_usd"

    def test_profit_does_not_trip(self) -> None:
        eng = RiskEngine(RiskLimits(daily_loss_kill_switch_usd=10_000))
        b = eng.check_posttrade(_state(daily_pnl=50_000, peak_daily_pnl=50_000))
        assert not eng.is_halted
        assert b is None

    def test_below_threshold_does_not_trip(self) -> None:
        eng = RiskEngine(RiskLimits(daily_loss_kill_switch_usd=10_000))
        b = eng.check_posttrade(_state(daily_pnl=-5_000, peak_daily_pnl=0))
        assert not eng.is_halted
        assert b is None


class TestPosttradeDrawdown:
    def test_hard_halt_at_full_cap(self) -> None:
        eng = RiskEngine(
            RiskLimits(
                max_drawdown_pct=0.5, daily_loss_kill_switch_usd=math.inf
            )
        )
        # Peak 10k, current 4k → 60% drawdown > 50% cap → halt
        b = eng.check_posttrade(_state(peak_daily_pnl=10_000, daily_pnl=4_000))
        assert eng.is_halted
        assert b.limit_name == "max_drawdown_pct"

    def test_soft_warn_at_half_cap(self) -> None:
        eng = RiskEngine(
            RiskLimits(
                max_drawdown_pct=0.8, daily_loss_kill_switch_usd=math.inf
            )
        )
        # 50% drawdown, cap=80% → 50% > 40% (half of cap) → warn
        b = eng.check_posttrade(_state(peak_daily_pnl=10_000, daily_pnl=5_000))
        assert not eng.is_halted
        assert b is not None and b.severity == "warn"


class TestPosttradeInventoryAge:
    def test_warn_on_stale(self) -> None:
        eng = RiskEngine(
            RiskLimits(
                inventory_age_sec=60.0,
                daily_loss_kill_switch_usd=math.inf,
                max_drawdown_pct=1.0,
            )
        )
        b = eng.check_posttrade(
            _state(ts=200.0, per_symbol_oldest_ts={"SPY": 100.0})
        )
        assert b is not None
        assert b.severity == "warn"
        assert b.limit_name == "inventory_age_sec"
        assert b.symbol == "SPY"

    def test_no_warn_when_fresh(self) -> None:
        eng = RiskEngine(
            RiskLimits(
                inventory_age_sec=600.0,
                daily_loss_kill_switch_usd=math.inf,
                max_drawdown_pct=1.0,
            )
        )
        b = eng.check_posttrade(
            _state(ts=200.0, per_symbol_oldest_ts={"SPY": 150.0})
        )
        assert b is None


# ---------------------------------------------------------------------------
# Halt control + composite behaviour
# ---------------------------------------------------------------------------
class TestHaltControl:
    def test_halt_blocks_subsequent_pretrade(self) -> None:
        eng = RiskEngine(RiskLimits(daily_loss_kill_switch_usd=1_000))
        eng.check_posttrade(_state(daily_pnl=-2_000))
        assert eng.is_halted

        ok, b = eng.check_pretrade(
            participant_id="mm",
            symbol="SPY",
            order_notional_usd=10,
            order_delta=0,
            state=_state(),
        )
        assert not ok and b.limit_name == "halted"

    def test_reset_clears_state(self) -> None:
        eng = RiskEngine(
            RiskLimits(daily_loss_kill_switch_usd=1_000, concentration_pct=1.0)
        )
        eng.check_posttrade(_state(daily_pnl=-2_000))
        assert eng.is_halted
        assert len(eng.breaches) == 1

        eng.reset()
        assert not eng.is_halted
        assert eng.breaches == []
        ok, _ = eng.check_pretrade(
            participant_id="mm",
            symbol="SPY",
            order_notional_usd=10,
            order_delta=0,
            state=_state(),
        )
        assert ok

    def test_breaches_accumulate_in_order(self) -> None:
        eng = RiskEngine(RiskLimits(max_notional_usd=100, max_net_delta=10))
        eng.check_pretrade(
            participant_id="mm",
            symbol="SPY",
            order_notional_usd=1000,  # notional breach
            order_delta=0,
            state=_state(ts=1.0),
        )
        eng.check_pretrade(
            participant_id="mm",
            symbol="SPY",
            order_notional_usd=10,
            order_delta=1000,  # delta breach
            state=_state(ts=2.0),
        )
        assert len(eng.breaches) == 2
        assert eng.breaches[0].limit_name == "max_notional_usd"
        assert eng.breaches[1].limit_name == "max_net_delta"
        assert eng.breaches[0].ts < eng.breaches[1].ts
