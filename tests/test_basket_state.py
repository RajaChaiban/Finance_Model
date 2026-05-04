"""Phase 6 — Round-trip serialisation tests for multi-asset state types."""
from src.agents.state import (
    BasketObjective, ObservationSchedule, AutocallTerms, Leg, Structure,
)


def test_basket_objective_serialises():
    b = BasketObjective(
        underliers=["NVDA", "AMD", "AVGO"],
        weights=[1/3, 1/3, 1/3],
        worst_of=True,
        maturity_years=1.0,
    )
    j = b.model_dump_json()
    assert "NVDA" in j


def test_observation_schedule_quarterly():
    s = ObservationSchedule.quarterly(maturity_years=1.0)
    assert len(s.dates_years) == 4
    assert s.dates_years[-1] == 1.0


def test_autocall_terms_validation():
    t = AutocallTerms(
        coupon_rate=0.10,
        autocall_barrier=1.00,
        coupon_barrier=0.70,
        protection_barrier=0.60,
    )
    assert t.protection_barrier < t.coupon_barrier <= t.autocall_barrier
