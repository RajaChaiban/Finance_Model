"""Unit tests for the Phase-1A objective-fit validator invariants.

Each invariant has a happy-path test (no finding fires) and a fire-path
test (the expected severity + finding name appear, tagged on the right
candidate_id).
"""

from __future__ import annotations

import pytest

from src.agents.state import (
    Candidate,
    ClientObjective,
    GreeksSnapshot,
    Leg,
    MarketRegime,
    PricedCandidate,
    Severity,
    StructureKind,
    StructuringSession,
)
from src.agents.validator import ValidatorAgent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _objective(**overrides) -> ClientObjective:
    base = dict(
        underlying="SPY",
        notional_usd=10_000_000.0,
        view="bullish",
        horizon_days=180,
        budget_bps_notional=90.0,
        premium_tolerance="low",
        capped_upside_ok=False,
        barrier_appetite=False,
    )
    base.update(overrides)
    return ClientObjective(**base)


def _regime() -> MarketRegime:
    return MarketRegime(underlying="SPY", spot=500.0, vol_regime="normal")


def _leg(
    option_type: str = "european_call",
    strike: float = 500.0,
    quantity: float = 1.0,
    expiry_days: int = 180,
    barrier_level=None,
) -> Leg:
    return Leg(
        option_type=option_type,
        strike=strike,
        expiry_days=expiry_days,
        quantity=quantity,
        barrier_level=barrier_level,
    )


def _candidate(
    *,
    kind: StructureKind = StructureKind.CALL_SPREAD,
    name: str = "test candidate",
    legs=None,
    notional_usd: float = 10_000_000.0,
) -> Candidate:
    return Candidate(
        kind=kind,
        name=name,
        legs=legs
        or [
            _leg(option_type="european_call", strike=495.0, quantity=+1.0),
            _leg(option_type="european_call", strike=525.0, quantity=-1.0),
        ],
        rationale="test",
        notional_usd=notional_usd,
    )


def _priced(
    *,
    cand: Candidate | None = None,
    net_premium_bps: float = 70.0,
    delta: float = 0.30,
    vega: float = 0.10,
    theta: float = -0.01,
    feasible: bool = True,
) -> PricedCandidate:
    if cand is None:
        cand = _candidate()
    notional = cand.notional_usd
    return PricedCandidate(
        candidate=cand,
        net_premium=net_premium_bps / 1e4 * notional,
        net_premium_bps=net_premium_bps,
        greeks=GreeksSnapshot(delta=delta, vega=vega, theta=theta),
        feasible=feasible,
    )


def _run_validator(
    *,
    objective: ClientObjective | None = None,
    priced: list[PricedCandidate] | None = None,
) -> StructuringSession:
    obj = objective or _objective()
    priced = priced or [_priced()]
    session = StructuringSession(
        objective=obj,
        regime=_regime(),
        candidates=[p.candidate for p in priced],
        priced=priced,
    )
    return ValidatorAgent().run(session)


def _findings_named(session: StructuringSession, name: str):
    assert session.validator is not None
    return [f for f in session.validator.findings if f.name == name]


# ---------------------------------------------------------------------------
# Invariant 1: budget breach
# ---------------------------------------------------------------------------


def test_budget_breach_happy_within_tolerance():
    """95bps premium vs 90bps budget — within +10bps tolerance, no fire."""
    obj = _objective(budget_bps_notional=90.0, premium_tolerance="low")
    priced = _priced(net_premium_bps=95.0)
    session = _run_validator(objective=obj, priced=[priced])
    assert not _findings_named(session, "budget_breach")


def test_budget_breach_fires_when_over():
    """330bps premium vs 90bps budget — fires BLOCK on the right candidate."""
    obj = _objective(budget_bps_notional=90.0, premium_tolerance="low")
    cand = _candidate()
    priced = _priced(cand=cand, net_premium_bps=330.0)
    session = _run_validator(objective=obj, priced=[priced])

    fires = _findings_named(session, "budget_breach")
    assert len(fires) == 1
    f = fires[0]
    assert f.severity is Severity.BLOCK
    assert f.candidate_id == cand.candidate_id
    assert "exceeds budget" in f.message


def test_budget_breach_credit_zero_cost_only_fires():
    """Net credit on a zero_cost_only brief should block when |bps| > cap."""
    obj = _objective(
        budget_bps_notional=0.0, premium_tolerance="zero_cost_only"
    )
    priced = _priced(net_premium_bps=-50.0)
    session = _run_validator(objective=obj, priced=[priced])
    fires = _findings_named(session, "budget_breach")
    assert len(fires) == 1
    assert fires[0].severity is Severity.BLOCK


def test_budget_breach_credit_low_tolerance_skips():
    """Net credit on a non-zero-cost brief is always fine — no fire."""
    obj = _objective(budget_bps_notional=90.0, premium_tolerance="low")
    priced = _priced(net_premium_bps=-200.0)
    session = _run_validator(objective=obj, priced=[priced])
    assert not _findings_named(session, "budget_breach")


# ---------------------------------------------------------------------------
# Invariant 2: delta sign mismatch
# ---------------------------------------------------------------------------


def test_delta_sign_happy_bullish_positive_delta():
    obj = _objective(view="bullish")
    priced = _priced(delta=+0.30)
    session = _run_validator(objective=obj, priced=[priced])
    assert not _findings_named(session, "delta_sign_vs_view")


def test_delta_sign_fires_bullish_with_negative_delta():
    """Covered call (Δ ≈ -0.36) on a bullish brief should BLOCK."""
    obj = _objective(view="bullish", capped_upside_ok=True)
    cand = _candidate(
        kind=StructureKind.COVERED_CALL,
        legs=[_leg(option_type="european_call", strike=525.0, quantity=-1.0)],
    )
    priced = _priced(cand=cand, delta=-0.36, net_premium_bps=-30.0)
    session = _run_validator(objective=obj, priced=[priced])
    fires = _findings_named(session, "delta_sign_vs_view")
    assert len(fires) == 1
    assert fires[0].severity is Severity.BLOCK
    assert fires[0].candidate_id == cand.candidate_id
    assert "bullish" in fires[0].message


def test_delta_sign_fires_bearish_with_positive_delta():
    obj = _objective(view="crash_hedge", capped_upside_ok=True)
    priced = _priced(delta=+0.40)
    session = _run_validator(objective=obj, priced=[priced])
    fires = _findings_named(session, "delta_sign_vs_view")
    assert len(fires) == 1
    assert fires[0].severity is Severity.BLOCK


def test_delta_sign_neutral_view_no_constraint():
    obj = _objective(view="neutral", premium_tolerance="zero_cost_only")
    priced = _priced(delta=+0.50)
    session = _run_validator(objective=obj, priced=[priced])
    assert not _findings_named(session, "delta_sign_vs_view")


def test_delta_sign_within_slop_no_fire():
    """A small near-zero negative Δ on a bullish brief is tolerated."""
    obj = _objective(view="mildly_bullish")
    priced = _priced(delta=-0.04)
    session = _run_validator(objective=obj, priced=[priced])
    assert not _findings_named(session, "delta_sign_vs_view")


# ---------------------------------------------------------------------------
# Invariant 3: capped_upside contradiction
# ---------------------------------------------------------------------------


def test_capped_upside_happy_when_client_accepts_caps():
    obj = _objective(capped_upside_ok=True)
    cand = _candidate(
        kind=StructureKind.COVERED_CALL,
        legs=[_leg(option_type="european_call", strike=525.0, quantity=-1.0)],
    )
    priced = _priced(cand=cand, delta=+0.30)
    session = _run_validator(objective=obj, priced=[priced])
    assert not _findings_named(session, "capped_upside_contradiction")


def test_capped_upside_fires_short_call_when_refused():
    obj = _objective(capped_upside_ok=False, view="bullish")
    cand = _candidate(
        kind=StructureKind.COVERED_CALL,
        name="Short call",
        legs=[_leg(option_type="european_call", strike=545.0, quantity=-1.0)],
    )
    priced = _priced(cand=cand, delta=+0.10, net_premium_bps=-30.0)
    session = _run_validator(objective=obj, priced=[priced])
    fires = _findings_named(session, "capped_upside_contradiction")
    assert len(fires) == 1
    assert fires[0].severity is Severity.BLOCK
    assert fires[0].candidate_id == cand.candidate_id
    assert "short" in fires[0].message.lower() and "call" in fires[0].message
    assert "545" in fires[0].message


def test_capped_upside_long_call_does_not_fire():
    """Long-only call structure is fine even when capped_upside_ok=False."""
    obj = _objective(capped_upside_ok=False, view="bullish")
    cand = _candidate(
        kind=StructureKind.LONG_CALL,
        legs=[_leg(option_type="european_call", strike=500.0, quantity=+1.0)],
    )
    priced = _priced(cand=cand, delta=+0.55)
    session = _run_validator(objective=obj, priced=[priced])
    assert not _findings_named(session, "capped_upside_contradiction")


# ---------------------------------------------------------------------------
# Invariant 4: yield-direction consistency
# ---------------------------------------------------------------------------


def test_neutral_yield_happy_short_vol_long_theta():
    obj = _objective(view="neutral", premium_tolerance="medium")
    priced = _priced(vega=-0.20, theta=+0.005)
    session = _run_validator(objective=obj, priced=[priced])
    assert not _findings_named(session, "neutral_yield_inconsistent")


def test_neutral_yield_fires_long_vol_short_theta():
    """Long-vol DEBIT put_spread on a neutral yield brief — WARN."""
    obj = _objective(view="neutral", premium_tolerance="medium")
    cand = _candidate(
        kind=StructureKind.PUT_SPREAD,
        legs=[
            _leg(option_type="european_put", strike=500.0, quantity=+1.0),
            _leg(option_type="european_put", strike=475.0, quantity=-1.0),
        ],
    )
    priced = _priced(cand=cand, delta=-0.15, vega=+0.30, theta=-0.02)
    session = _run_validator(objective=obj, priced=[priced])
    fires = _findings_named(session, "neutral_yield_inconsistent")
    assert len(fires) == 1
    assert fires[0].severity is Severity.WARN
    assert fires[0].candidate_id == cand.candidate_id
    assert "long-vol" in fires[0].message
    assert "short-theta" in fires[0].message


def test_neutral_yield_skipped_for_zero_cost_only():
    """zero_cost_only is a hedge brief, not a yield brief — rule should skip."""
    obj = _objective(view="neutral", premium_tolerance="zero_cost_only")
    priced = _priced(vega=+0.30, theta=-0.02)
    session = _run_validator(objective=obj, priced=[priced])
    assert not _findings_named(session, "neutral_yield_inconsistent")


def test_neutral_yield_skipped_for_non_neutral_view():
    obj = _objective(view="bullish", premium_tolerance="medium")
    priced = _priced(vega=+0.30, theta=-0.02)
    session = _run_validator(objective=obj, priced=[priced])
    assert not _findings_named(session, "neutral_yield_inconsistent")


# ---------------------------------------------------------------------------
# Cross-cutting: findings tagged with candidate_id (Narrator filter contract)
# ---------------------------------------------------------------------------


def test_findings_tagged_with_candidate_id():
    """Every objective-fit finding should carry a candidate_id."""
    obj = _objective(view="bullish", capped_upside_ok=False)
    cand = _candidate(
        kind=StructureKind.COVERED_CALL,
        legs=[_leg(option_type="european_call", strike=545.0, quantity=-1.0)],
    )
    priced = _priced(cand=cand, delta=-0.36, net_premium_bps=-30.0)
    session = _run_validator(objective=obj, priced=[priced])
    obj_findings = [
        f
        for f in session.validator.findings
        if f.name
        in {
            "budget_breach",
            "delta_sign_vs_view",
            "capped_upside_contradiction",
            "neutral_yield_inconsistent",
        }
    ]
    assert obj_findings
    for f in obj_findings:
        assert f.candidate_id == cand.candidate_id
