"""Tests for the attribution module.

Cover:
  * regime bucketing
  * participant bucketing
  * realized P&L sign for buys vs sells with positive/negative fee_bps
  * counterfactual passive: long inventory in up market vs down market
  * edge_over_passive correctness
  * mismatched fills / contexts raises
"""

from __future__ import annotations

import math

import pytest

from src.esmm.schemas import Fill, Side
from src.esmm.sim.attribution import (
    AttributionReport,
    FillContext,
    attribute,
)


def _fill(
    side: Side,
    price: float,
    size: float = 100.0,
    fair: float = 100.0,
    fee_bps: float = -0.2,
    ts: float = 0.0,
) -> Fill:
    return Fill(
        ts=ts,
        symbol="SPY",
        side=side,
        price=price,
        size=size,
        fair_value_at_fill=fair,
        fee_bps=fee_bps,
        is_hedge=False,
    )


def _ctx(regime: str = "CALM", participant_kind: str = "noise") -> FillContext:
    return FillContext(regime=regime, participant_kind=participant_kind)


class TestEmptyCase:
    def test_no_fills(self) -> None:
        r = attribute(
            fills=[],
            contexts=[],
            initial_inventory=0.0,
            initial_mid=100.0,
            final_mid=100.0,
        )
        assert r.actual_realized_pnl == 0.0
        assert r.counterfactual_passive_pnl == 0.0
        assert r.edge_over_passive == 0.0
        assert r.by_regime == []
        assert r.by_participant == []


class TestMismatchValidation:
    def test_lengths_must_align(self) -> None:
        with pytest.raises(ValueError):
            attribute(
                fills=[_fill(Side.BUY, 99.5)],
                contexts=[],
                initial_inventory=0.0,
                initial_mid=100.0,
                final_mid=100.0,
            )


class TestPnlSign:
    def test_buy_below_fair_is_positive_pnl(self) -> None:
        # We BUY at 99.5 with fair=100 → +0.5 per share * 100 = +50
        # Fee = -(-0.2/10000) * 99.5 * 100 = +0.199 (maker rebate)
        f = _fill(Side.BUY, price=99.5, fair=100.0, fee_bps=-0.2, size=100)
        r = attribute([f], [_ctx()], 0.0, 100.0, 100.0)
        assert r.actual_realized_pnl == pytest.approx(50 + 0.199, abs=1e-6)

    def test_sell_above_fair_is_positive_pnl(self) -> None:
        f = _fill(Side.SELL, price=100.5, fair=100.0, fee_bps=-0.2, size=100)
        r = attribute([f], [_ctx()], 0.0, 100.0, 100.0)
        # edge = (100.5 - 100) * 100 = 50; fee = -(-0.2/10000)*100.5*100 ≈ 0.201
        assert r.actual_realized_pnl == pytest.approx(50 + 0.201, abs=1e-6)

    def test_buy_above_fair_is_negative_pnl(self) -> None:
        f = _fill(Side.BUY, price=100.5, fair=100.0, fee_bps=0.0, size=100)
        r = attribute([f], [_ctx()], 0.0, 100.0, 100.0)
        assert r.actual_realized_pnl == pytest.approx(-50.0, abs=1e-6)

    def test_taker_fee_subtracts(self) -> None:
        # Positive fee_bps = taker cost.
        f = _fill(Side.BUY, price=100.0, fair=100.0, fee_bps=5.0, size=100)
        r = attribute([f], [_ctx()], 0.0, 100.0, 100.0)
        # No spread edge; fee = -(5/10000)*100*100 = -5.0
        assert r.actual_realized_pnl == pytest.approx(-5.0, abs=1e-6)


class TestRegimeBucketing:
    def test_two_regimes_separated(self) -> None:
        fills = [
            _fill(Side.BUY, 99.5),
            _fill(Side.BUY, 99.0),
            _fill(Side.SELL, 100.5),
        ]
        contexts = [_ctx("CALM"), _ctx("STRESS"), _ctx("CALM")]
        r = attribute(fills, contexts, 0.0, 100.0, 100.0)
        regimes = {b.regime: b for b in r.by_regime}
        assert set(regimes) == {"CALM", "STRESS"}
        assert regimes["CALM"].n_fills == 2
        assert regimes["STRESS"].n_fills == 1
        # Notional sums should equal price*size totals.
        assert regimes["CALM"].notional == pytest.approx(99.5 * 100 + 100.5 * 100)
        assert regimes["STRESS"].notional == pytest.approx(99.0 * 100)

    def test_regimes_sorted_alphabetically(self) -> None:
        fills = [_fill(Side.BUY, 99.5)] * 3
        contexts = [_ctx("VOLATILE"), _ctx("CALM"), _ctx("STRESS")]
        r = attribute(fills, contexts, 0.0, 100.0, 100.0)
        assert [b.regime for b in r.by_regime] == ["CALM", "STRESS", "VOLATILE"]


class TestParticipantBucketing:
    def test_buckets_separate_by_kind(self) -> None:
        fills = [_fill(Side.BUY, 99.5), _fill(Side.SELL, 100.5)]
        contexts = [
            _ctx(participant_kind="noise"),
            _ctx(participant_kind="informed"),
        ]
        r = attribute(fills, contexts, 0.0, 100.0, 100.0)
        kinds = {b.participant_kind: b for b in r.by_participant}
        assert set(kinds) == {"noise", "informed"}
        assert kinds["noise"].n_fills == 1
        assert kinds["informed"].n_fills == 1

    def test_informed_flow_negative_pnl(self) -> None:
        # Adverse selection scenario: we filled against an informed trader
        # who saw the move coming. We *buy* at 100.5 when fair was 100,
        # then the market moves up — but P&L per fill at the moment is
        # already negative (-0.5 * 100 = -50).
        f = _fill(Side.BUY, price=100.5, fair=100.0, size=100, fee_bps=0)
        r = attribute([f], [_ctx(participant_kind="informed")], 0.0, 100.0, 105.0)
        assert r.by_participant[0].realized_pnl < 0


class TestCounterfactualPassive:
    def test_long_in_up_market_positive_counterfactual(self) -> None:
        r = attribute(
            fills=[],
            contexts=[],
            initial_inventory=1000.0,
            initial_mid=100.0,
            final_mid=102.0,
        )
        # 1000 * (102 - 100) = +2000 passive P&L
        assert r.counterfactual_passive_pnl == pytest.approx(2000.0)

    def test_long_in_down_market_negative_counterfactual(self) -> None:
        r = attribute(
            fills=[],
            contexts=[],
            initial_inventory=500.0,
            initial_mid=100.0,
            final_mid=98.0,
        )
        assert r.counterfactual_passive_pnl == pytest.approx(-1000.0)

    def test_short_in_down_market_positive_counterfactual(self) -> None:
        r = attribute(
            fills=[],
            contexts=[],
            initial_inventory=-200.0,
            initial_mid=100.0,
            final_mid=95.0,
        )
        # -200 * (95 - 100) = +1000
        assert r.counterfactual_passive_pnl == pytest.approx(1000.0)

    def test_flat_inventory_zero_counterfactual(self) -> None:
        r = attribute(
            fills=[],
            contexts=[],
            initial_inventory=0.0,
            initial_mid=100.0,
            final_mid=110.0,
        )
        assert r.counterfactual_passive_pnl == 0.0


class TestEdgeOverPassive:
    def test_active_beats_passive(self) -> None:
        # Captured 50 of spread edge while holding 100 shares in a flat market
        # → passive = 0, actual = 50, edge = +50
        f = _fill(Side.BUY, price=99.5, fair=100.0, size=100, fee_bps=0)
        r = attribute([f], [_ctx()], 100.0, 100.0, 100.0)
        assert r.edge_over_passive == pytest.approx(50.0)
        assert r.actual_realized_pnl == pytest.approx(50.0)
        assert r.counterfactual_passive_pnl == 0.0

    def test_passive_beats_active(self) -> None:
        # Held 1000 shares as market rallied 5pts → passive = +5000.
        # Single bad fill: bought at fair, paid 5 bps fee. realized = -5 bps * 100*100 = -5.
        f = _fill(Side.BUY, price=100.0, fair=100.0, size=100, fee_bps=5.0)
        r = attribute([f], [_ctx()], 1000.0, 100.0, 105.0)
        assert r.actual_realized_pnl == pytest.approx(-5.0, abs=1e-6)
        assert r.counterfactual_passive_pnl == pytest.approx(5000.0)
        assert r.edge_over_passive == pytest.approx(-5005.0, abs=1e-6)


class TestReportSerializable:
    def test_to_dict(self) -> None:
        f = _fill(Side.BUY, 99.5)
        r = attribute([f], [_ctx()], 0.0, 100.0, 101.0)
        d = r.to_dict()
        assert isinstance(d, dict)
        assert "by_regime" in d
        assert "by_participant" in d
        assert "counterfactual_passive_pnl" in d
        assert "edge_over_passive" in d
        assert d["actual_realized_pnl"] == pytest.approx(r.actual_realized_pnl)
