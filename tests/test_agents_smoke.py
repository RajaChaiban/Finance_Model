"""End-to-end smoke test for the structuring co-pilot in DEMO_REPLAY mode.

Exercises Intake → Gate A → Regime + Strategist → Gate B → Pricing + Scenario +
Validator + Narrator → Gate C → DONE without touching the network. The LLM
client returns canned JSON from `tests/fixtures/demo_replay.json`; the
market-data layer falls back to placeholders when yfinance is unavailable.

This is the on-stage failsafe: if the demo's network drops, the same flow runs
with `DEMO_REPLAY=1` and produces a memo.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

# Force replay mode + bypass any installed dotenv overrides BEFORE importing
# the agent layer. The agent_config singleton reads env at first-touch.
os.environ.setdefault("DEMO_REPLAY", "1")
os.environ.setdefault("GEMINI_API_KEY", "")  # not needed in replay

from src.agents.orchestrator import (  # noqa: E402
    OrchestratorAgent,
    SessionStore,
)
from src.agents.state import Gate, SessionStatus  # noqa: E402
from src.config import agent_config  # noqa: E402
from src.agents import llm_client  # noqa: E402


@pytest.fixture(autouse=True)
def _replay_env(monkeypatch):
    """Ensure DEMO_REPLAY=1 and reset the cfg / llm singletons per test."""
    monkeypatch.setenv("DEMO_REPLAY", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    agent_config.reload()
    llm_client.reset_llm_client()
    yield
    llm_client.reset_llm_client()


@pytest.fixture
def fixed_market_data():
    """Stub the market data layer so the regime build is deterministic offline."""
    fake = {
        "spot_price": 500.0,
        "dividend_yield": 0.015,
        "volatility_30d": 0.18,
        "volatility_90d": 0.20,
        "source": "fallback",
    }
    with patch("src.agents.orchestrator.market_data.fetch_market_params", return_value=fake):
        yield fake


def _make_orchestrator() -> OrchestratorAgent:
    return OrchestratorAgent(store=SessionStore())


def test_smoke_flow_nl_to_memo(fixed_market_data):
    orch = _make_orchestrator()
    session = orch.start_session(
        intake_nl=(
            "Asset manager client, $50M long SPY, wants downside protection "
            "through year-end (about 8 months), comfortable spending up to 1% "
            "of notional, OK with capping upside above 8%."
        ),
    )

    # Phase: intake done, awaiting Gate A.
    assert session.status == SessionStatus.AWAITING_GATE_A
    assert session.objective is not None
    assert session.objective.underlying == "SPY"
    assert session.objective.notional_usd == 50_000_000
    assert session.objective.view == "bearish"

    # Approve Gate A → regime + strategist run; awaiting Gate B.
    session = orch.decide_gate(session.session_id, Gate.A, approved=True)
    assert session.status == SessionStatus.AWAITING_GATE_B
    assert session.regime is not None
    assert session.regime.spot == pytest.approx(500.0)
    assert session.regime.vol_regime in {"low", "normal", "high", "very_high"}
    assert len(session.candidates) >= 1
    assert len(session.candidates) <= 3
    # Each candidate has at least one leg with a strict positive strike.
    for c in session.candidates:
        assert c.legs, f"candidate {c.name} has no legs"
        for leg in c.legs:
            assert leg.strike > 0
            assert leg.expiry_days > 0

    # Approve Gate B → pricing + scenarios + validator + narrator;
    # awaiting Gate C.
    session = orch.decide_gate(session.session_id, Gate.B, approved=True)
    assert session.status == SessionStatus.AWAITING_GATE_C
    assert len(session.priced) == len(session.candidates)
    assert len(session.scenarios) == len(session.candidates)
    assert session.validator is not None
    # No blockers expected from the rules-table outputs.
    if session.validator.has_blockers:
        blockers = [f for f in session.validator.findings if f.severity.value == "block"]
        pytest.fail(f"Unexpected validator blockers: {blockers}")
    assert session.memo is not None
    assert session.memo.title
    assert session.memo.comparison_table_md
    assert session.memo.recommended_candidate_id
    # Recommended id is one of the priced candidates.
    rec_ids = {p.candidate.candidate_id for p in session.priced}
    assert session.memo.recommended_candidate_id in rec_ids

    # Approve Gate C → done.
    session = orch.decide_gate(session.session_id, Gate.C, approved=True)
    assert session.status == SessionStatus.DONE


def test_smoke_flow_form_path(fixed_market_data):
    """Form path bypasses the LLM entirely — useful when GEMINI_API_KEY is unset."""
    orch = _make_orchestrator()
    session = orch.start_session(
        intake_form={
            "underlying": "SPY",
            "notional_usd": 25_000_000,
            "view": "bearish",
            "horizon_days": 120,
            "budget_bps_notional": 80,
            "premium_tolerance": "low",
            "capped_upside_ok": True,
            "barrier_appetite": False,
        },
    )
    assert session.status == SessionStatus.AWAITING_GATE_A
    assert session.objective.underlying == "SPY"

    session = orch.decide_gate(session.session_id, Gate.A, approved=True)
    assert session.status == SessionStatus.AWAITING_GATE_B
    assert session.candidates

    session = orch.decide_gate(session.session_id, Gate.B, approved=True)
    assert session.status == SessionStatus.AWAITING_GATE_C
    assert session.memo is not None

    session = orch.decide_gate(session.session_id, Gate.C, approved=True)
    assert session.status == SessionStatus.DONE


def test_gate_rejection_cancels_session(fixed_market_data):
    orch = _make_orchestrator()
    session = orch.start_session(
        intake_form={
            "underlying": "SPY",
            "notional_usd": 10_000_000,
            "view": "bearish",
            "horizon_days": 90,
            "budget_bps_notional": 50,
        },
    )
    session = orch.decide_gate(session.session_id, Gate.A, approved=False)
    assert session.status == SessionStatus.CANCELLED


def test_validator_catches_bad_put_spread(fixed_market_data):
    """Inject a put spread with K_long < K_short and verify Validator blocks it."""
    from src.agents.pricing import PricingAgent
    from src.agents.state import (
        Candidate,
        ClientObjective,
        Leg,
        MarketRegime,
        StructureKind,
        StructuringSession,
    )
    from src.agents.validator import ValidatorAgent

    obj = ClientObjective(
        underlying="SPY",
        notional_usd=10_000_000,
        view="bearish",
        horizon_days=90,
        budget_bps_notional=80,
    )
    regime = MarketRegime(
        underlying="SPY",
        spot=500.0,
        dividend_yield=0.015,
        risk_free_rate=0.045,
        realised_vol_30d=0.18,
        realised_vol_90d=0.20,
        vol_regime="normal",
    )

    bad = Candidate(
        kind=StructureKind.PUT_SPREAD,
        name="Backwards put spread",
        legs=[
            # Long strike *lower* than short strike — wrong order!
            Leg(option_type="european_put", strike=440.0, expiry_days=90, quantity=+1.0),
            Leg(option_type="european_put", strike=475.0, expiry_days=90, quantity=-1.0),
        ],
        rationale="intentionally wrong",
        notional_usd=10_000_000,
    )
    session = StructuringSession(objective=obj, regime=regime, candidates=[bad])
    session = PricingAgent().run(session)
    session = ValidatorAgent().run(session)

    assert session.validator is not None
    block_names = [f.name for f in session.validator.findings if f.severity.value == "block"]
    assert "put_spread_strike_order" in block_names


def test_validator_catches_wrong_barrier_direction(fixed_market_data):
    """Down-and-out put with barrier *above* spot must be blocked."""
    from src.agents.state import (
        Candidate,
        ClientObjective,
        Leg,
        MarketRegime,
        PricedCandidate,
        StructureKind,
        StructuringSession,
        GreeksSnapshot,
    )
    from src.agents.validator import ValidatorAgent

    obj = ClientObjective(
        underlying="SPY",
        notional_usd=10_000_000,
        view="bearish",
        horizon_days=90,
        budget_bps_notional=80,
    )
    regime = MarketRegime(
        underlying="SPY",
        spot=500.0,
        vol_regime="normal",
    )
    bad = Candidate(
        kind=StructureKind.KO_PUT,
        name="Wrong-direction KO put",
        legs=[
            Leg(
                option_type="knockout_put",
                strike=475.0,
                expiry_days=90,
                quantity=+1.0,
                barrier_level=525.0,  # ABOVE spot — wrong for a DOWN-and-out put
            ),
        ],
        rationale="intentionally wrong",
        notional_usd=10_000_000,
    )
    # Skip pricing (the engine would reject this anyway). Synthesise a priced
    # placeholder so Validator runs.
    priced = PricedCandidate(
        candidate=bad,
        net_premium=1.0,
        net_premium_bps=2.0,
        greeks=GreeksSnapshot(),
    )
    session = StructuringSession(
        objective=obj, regime=regime, candidates=[bad], priced=[priced]
    )
    session = ValidatorAgent().run(session)
    assert session.validator is not None
    block_names = [f.name for f in session.validator.findings if f.severity.value == "block"]
    assert "barrier_direction_put" in block_names
