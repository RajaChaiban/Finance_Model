"""End-to-end pipeline tests for the structuring co-pilot across 6 diverse RFQs.

Complements ``tests/test_agents_smoke.py`` (which covers a single RFQ) by
exercising different views, sizes, horizons, premium tolerances, capped-upside
and barrier-appetite combinations, plus 6 different underlyings.

DEMO_REPLAY=1 mode is used to avoid live LLM calls. The IntakeAgent's NL path
always uses the fixed replay key ``IntakeAgent:nl``, so per-test we monkeypatch
the replay cache to return a JSON objective tailored to the RFQ being tested.
The market-data layer is stubbed per RFQ to vary spot / vol so different code
paths in the rules table fire.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any
from unittest.mock import patch

import pytest

# Force replay mode + neuter the API key BEFORE importing the agent layer.
os.environ.setdefault("DEMO_REPLAY", "1")
os.environ.setdefault("GEMINI_API_KEY", "")

from src.agents.orchestrator import (  # noqa: E402
    OrchestratorAgent,
    SessionStore,
)
from src.agents.state import (  # noqa: E402
    Gate,
    MemoArtifact,
    PricedCandidate,
    SessionStatus,
    Severity,
)
from src.config import agent_config  # noqa: E402
from src.agents import llm_client  # noqa: E402


# ---------------------------------------------------------------------------
# Replay-cache plumbing
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _replay_env(monkeypatch):
    """Force DEMO_REPLAY=1 + reset agent_config + LLM-client singletons per test."""
    monkeypatch.setenv("DEMO_REPLAY", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    agent_config.reload()
    llm_client.reset_llm_client()
    yield
    llm_client.reset_llm_client()


def _install_intake_replay(intake_payload: dict[str, Any]) -> None:
    """Replace the IntakeAgent:nl replay entry for the duration of one test.

    The LLMClient lazy-loads the replay cache on first construction; we trigger
    that here, then mutate the in-memory dict so the next ``client.complete``
    call returns our tailored JSON. Other replay keys (StrategistAgent:polish,
    NarratorAgent:memo) keep their existing fixture values — those are
    parsed by their agents tolerantly so the SPY-flavoured prose from the
    fixture doesn't matter for these tests.
    """
    client = llm_client.get_llm_client()
    if client._replay_cache is None:  # noqa: SLF001 — test surface
        client._load_replay_cache()  # noqa: SLF001
    cache = client._replay_cache  # noqa: SLF001
    # Preserve untouched keys; override only the intake entry.
    cache["IntakeAgent:nl"] = {
        "text": json.dumps(intake_payload),
        "stop_reason": "end_turn",
    }


def _fake_market_factory(spot: float, vol_30d: float, vol_90d: float, div: float = 0.0):
    """Return a mock for market_data.fetch_market_params with this regime."""
    payload = {
        "spot_price": spot,
        "dividend_yield": div,
        "volatility_30d": vol_30d,
        "volatility_90d": vol_90d,
        "source": "fallback",
    }
    return patch(
        "src.agents.orchestrator.market_data.fetch_market_params",
        return_value=payload,
    )


def _make_orchestrator() -> OrchestratorAgent:
    return OrchestratorAgent(store=SessionStore())


# ---------------------------------------------------------------------------
# Shared assertion helpers
# ---------------------------------------------------------------------------


def _assert_objective_matches(session, expected: dict[str, Any]) -> None:
    """Verify the parsed ClientObjective matches the expected fields."""
    assert session.status == SessionStatus.AWAITING_GATE_A, session.last_error
    obj = session.objective
    assert obj is not None
    for key, want in expected.items():
        got = getattr(obj, key)
        assert got == want, f"objective.{key} expected {want!r}, got {got!r}"


def _assert_protective_put_greeks(priced: list[PricedCandidate]) -> None:
    """Verify protective-put Greek signs on candidates that are PURE long puts.

    For single-leg long-put structures (LONG_PUT, KO_PUT, KI_PUT), the
    aggregated Greeks ARE the put-leg Greeks, so we can directly assert
    delta<0 and gamma>0 — the contract for a long protective put.

    For multi-leg structures (collars, put spreads, etc.) the aggregated
    Greeks aren't a clean test of the put leg in isolation: a zero-cost
    collar's net gamma can flip negative when the short call is closer to
    ATM than the long put. We DO require that at least one candidate exists
    that contains a long-put leg, but we only enforce the strict sign check
    on the single-leg subset.
    """
    has_protective_put_leg = False
    asserted_at_least_one_pure = False
    for pc in priced:
        legs = pc.candidate.legs
        if any(l.quantity > 0 and l.option_type.endswith("_put") for l in legs):
            has_protective_put_leg = True
        # Only a PURE long-put / KO-put / KI-put candidate (single leg, qty>0,
        # *_put option_type) lets us read the put Greeks straight off the
        # aggregated snapshot.
        if len(legs) == 1:
            leg = legs[0]
            if leg.quantity > 0 and leg.option_type.endswith("_put"):
                assert pc.greeks.delta < 0, (
                    f"{pc.candidate.name}: single long put expected delta<0, "
                    f"got {pc.greeks.delta:.4f}"
                )
                assert pc.greeks.gamma > 0, (
                    f"{pc.candidate.name}: single long put expected gamma>0, "
                    f"got {pc.greeks.gamma:.6f}"
                )
                asserted_at_least_one_pure = True
    assert has_protective_put_leg, (
        "No candidate contains a long-protective-put leg — adjust RFQ params "
        "so the rules table picks at least one structure with a long put."
    )
    # If at least one pure long put was present, we asserted on it above.
    # If only multi-leg structures exist, we still passed via the existence
    # check on `has_protective_put_leg`.
    _ = asserted_at_least_one_pure  # documents the branch even when unused.


def _assert_bullish_call_leg(priced: list[PricedCandidate]) -> None:
    """Verify at least one candidate contains a long-call leg (bullish exposure)."""
    has_long_call_leg = any(
        any(l.quantity > 0 and l.option_type.endswith("_call") for l in pc.candidate.legs)
        for pc in priced
    )
    assert has_long_call_leg, (
        "No candidate contains a long-call leg — strategist did not produce "
        "bullish exposure for a mildly_bullish RFQ."
    )


def _assert_priced_basics(priced: list[PricedCandidate]) -> None:
    """Each priced candidate has non-zero per-leg prices and well-formed greeks."""
    assert len(priced) == 3
    for pc in priced:
        # Net premium for a debit structure can be 0 only on degenerate inputs;
        # at minimum one of the per-leg prices must be non-trivial.
        assert pc.per_leg_prices, f"{pc.candidate.name}: no per-leg prices"
        assert any(abs(p) > 1e-6 for p in pc.per_leg_prices), (
            f"{pc.candidate.name}: all per-leg prices are ~0"
        )
        # Greeks must be finite numbers.
        for field in ("delta", "gamma", "vega", "theta", "rho"):
            v = getattr(pc.greeks, field)
            assert v == v, f"{pc.candidate.name}: {field} is NaN"  # NaN check
        # Method label is filled in by the engine router.
        assert pc.method_label, f"{pc.candidate.name}: empty method_label"


def _assert_validator_findings_severity(report) -> None:
    """Every finding's severity must be a member of the Severity enum."""
    assert report is not None
    for f in report.findings:
        assert isinstance(f.severity, Severity), (
            f"finding {f.name}: severity {f.severity!r} not a Severity enum"
        )


def _assert_memo_complete(memo: MemoArtifact, priced: list[PricedCandidate]) -> None:
    assert memo is not None
    assert memo.title.strip(), "memo.title is empty"
    assert memo.comparison_table_md.strip(), "memo.comparison_table_md is empty"
    assert memo.recommendation_md.strip(), "memo.recommendation_md is empty"
    assert len(memo.term_sheets) == 3, (
        f"expected 3 term sheets, got {len(memo.term_sheets)}"
    )
    rec_ids = {p.candidate.candidate_id for p in priced}
    assert memo.recommended_candidate_id in rec_ids


def _drive_full_pipeline(session_id: str, orch: OrchestratorAgent):
    """Approve all three gates and walk a session to DONE.

    Returns the session at each gate so the caller can assert intermediate
    state. The pattern mirrors `tests/test_agents_smoke.py`.
    """
    after_a = orch.decide_gate(session_id, Gate.A, approved=True)
    after_b = orch.decide_gate(session_id, Gate.B, approved=True)
    after_c = orch.decide_gate(session_id, Gate.C, approved=True)
    return after_a, after_b, after_c


def _run_rfq(
    *,
    rfq_text: str,
    intake_payload: dict[str, Any],
    spot: float,
    vol_30d: float,
    vol_90d: float,
    expected_objective: dict[str, Any],
):
    """Execute one RFQ through Intake → Gate A → … → Gate C → DONE and run
    the standard battery of assertions. Returns the final DONE session for
    the caller to add RFQ-specific extra assertions if needed.
    """
    _install_intake_replay(intake_payload)

    with _fake_market_factory(spot, vol_30d, vol_90d):
        orch = _make_orchestrator()
        session = orch.start_session(intake_nl=rfq_text)

        # 1. Intake parsed correctly.
        _assert_objective_matches(session, expected_objective)

        # 2. Gate A approval → 3 candidates from rules table.
        session = orch.decide_gate(session.session_id, Gate.A, approved=True)
        assert session.status == SessionStatus.AWAITING_GATE_B, session.last_error
        # The rules table targets 3 structures per row. The smoke test allows
        # 1..3 to absorb factory failures, but for these RFQs (sane inputs)
        # we expect a full top-3.
        assert len(session.candidates) == 3, (
            f"expected 3 candidates, got {len(session.candidates)} "
            f"(rule fired off objective={session.objective!r})"
        )

        # 3. Gate B approval → priced + scenarios + validator + narrator.
        session = orch.decide_gate(session.session_id, Gate.B, approved=True)
        assert session.status == SessionStatus.AWAITING_GATE_C, session.last_error

        _assert_priced_basics(session.priced)
        _assert_protective_put_greeks(session.priced)

        # 4. Three scenario reports, one per priced candidate.
        assert len(session.scenarios) == 3
        priced_ids = {p.candidate.candidate_id for p in session.priced}
        scenario_ids = {s.candidate_id for s in session.scenarios}
        assert priced_ids == scenario_ids

        # 5. Validator populated with proper severities.
        _assert_validator_findings_severity(session.validator)

        # 6. Memo populated with title, table, recommendation, term sheets.
        _assert_memo_complete(session.memo, session.priced)

        # 7. Gate C approval → DONE.
        session = orch.decide_gate(session.session_id, Gate.C, approved=True)
        assert session.status == SessionStatus.DONE
    return session


# ---------------------------------------------------------------------------
# RFQ #1 — Bearish, $50M, 6mo, low premium tolerance, no caps, no barriers
# ---------------------------------------------------------------------------


def test_rfq_bearish_qqq_50m_6mo_low():
    """Bearish QQQ hedge over 6 months with low premium tolerance.

    Exercises: bearish view + mid horizon + low budget + normal vol +
    no barriers → rules table should pick PUT_SPREAD / ZCC / LONG_PUT.
    """
    rfq = (
        "Hedge fund client, $50M long QQQ, growing concerned about a tech "
        "rotation over the next 6 months. Premium budget is tight — "
        "30bps of notional max. No appetite for barriers, OK with capped "
        "upside above 10%."
    )
    intake_payload = {
        "underlying": "QQQ",
        "notional_usd": 50_000_000,
        "view": "bearish",
        "horizon_days": 180,
        "budget_bps_notional": 30,
        "premium_tolerance": "low",
        "capped_upside_ok": True,
        "barrier_appetite": False,
        "constraints": [],
        "clarifications_needed": [],
    }
    expected = {
        "underlying": "QQQ",
        "notional_usd": 50_000_000,
        "view": "bearish",
        "horizon_days": 180,
        "budget_bps_notional": 30,
        "premium_tolerance": "low",
        "capped_upside_ok": True,
        "barrier_appetite": False,
    }
    _run_rfq(
        rfq_text=rfq,
        intake_payload=intake_payload,
        spot=420.0,
        vol_30d=0.22,
        vol_90d=0.21,
        expected_objective=expected,
    )


# ---------------------------------------------------------------------------
# RFQ #2 — protect_gains, $250M, 12mo, zero_cost_only, capped upside (collar)
# ---------------------------------------------------------------------------


def test_rfq_protect_gains_spy_250m_12mo_zero_cost():
    """Lock in unrealised gains zero-cost over 12 months (collar implied).

    Exercises: protect_gains view + capped_upside_ok=True + zero_cost.
    Rules row: ZCC / COLLAR / COVERED_CALL.
    """
    rfq = (
        "Pension plan with $250M of SPY at a $380 cost basis — current value "
        "shown above. They want to lock in gains for the next 12 months "
        "without paying any premium. Cap on upside is fine."
    )
    intake_payload = {
        "underlying": "SPY",
        "notional_usd": 250_000_000,
        "shares": None,
        "avg_cost": 380.0,
        "view": "protect_gains",
        "horizon_days": 365,
        "budget_bps_notional": 0,
        "premium_tolerance": "zero_cost_only",
        "capped_upside_ok": True,
        "barrier_appetite": False,
        "constraints": [],
        "clarifications_needed": [],
    }
    expected = {
        "underlying": "SPY",
        "notional_usd": 250_000_000,
        "view": "protect_gains",
        "horizon_days": 365,
        "premium_tolerance": "zero_cost_only",
        "capped_upside_ok": True,
        "barrier_appetite": False,
    }
    session = _run_rfq(
        rfq_text=rfq,
        intake_payload=intake_payload,
        spot=510.0,
        vol_30d=0.16,
        vol_90d=0.18,
        expected_objective=expected,
    )
    # Extra: at least one candidate should be a collar variant.
    kinds = {c.kind.value for c in session.candidates}
    assert kinds & {"zero_cost_collar", "collar", "covered_call"}, (
        f"protect_gains RFQ produced no collar/covered-call candidate; got {kinds}"
    )


# ---------------------------------------------------------------------------
# RFQ #3 — mildly_bearish, $5M, 90d, low budget + barrier_appetite=True
# ---------------------------------------------------------------------------


def test_rfq_mildly_bearish_aapl_5m_90d_barrier():
    """Mild bearish on AAPL over 90 days with barrier appetite (KO put implied).

    Exercises: mildly_bearish + mid horizon + low budget + barriers ok.
    Rules row: KO_PUT / PUT_SPREAD / ZCC.
    """
    rfq = (
        "Family office, $5M long AAPL. Mildly worried about a pullback over "
        "the next quarter. Premium budget around 40bps, willing to take "
        "barrier risk if it cheapens the structure significantly."
    )
    intake_payload = {
        "underlying": "AAPL",
        "notional_usd": 5_000_000,
        "view": "mildly_bearish",
        "horizon_days": 90,
        "budget_bps_notional": 40,
        "premium_tolerance": "low",
        "capped_upside_ok": False,
        "barrier_appetite": True,
        "constraints": [],
        "clarifications_needed": [],
    }
    expected = {
        "underlying": "AAPL",
        "notional_usd": 5_000_000,
        "view": "mildly_bearish",
        "horizon_days": 90,
        "budget_bps_notional": 40,
        "barrier_appetite": True,
    }
    session = _run_rfq(
        rfq_text=rfq,
        intake_payload=intake_payload,
        spot=185.0,
        vol_30d=0.20,
        vol_90d=0.22,
        expected_objective=expected,
    )
    # Extra: barrier_appetite=True should yield at least one barrier structure.
    kinds = {c.kind.value for c in session.candidates}
    assert kinds & {"ko_put", "ki_put", "ko_call", "ki_call"}, (
        f"barrier_appetite=True RFQ produced no barrier structure; got {kinds}"
    )


# ---------------------------------------------------------------------------
# RFQ #4 — earnings_hedge, $50M, 30d, very_high vol (NVDA earnings)
# ---------------------------------------------------------------------------


def test_rfq_earnings_hedge_nvda_50m_30d():
    """Single-name earnings hedge in very-high IV.

    Exercises: earnings_hedge view + short horizon + low budget + very_high vol.
    Rules row: PUT_SPREAD / LONG_PUT / KI_PUT.
    """
    rfq = (
        "Long-only fund with $50M NVDA, earnings in 3 weeks and the stock has "
        "run hard into the print. Want a tactical hedge through the event — "
        "30 days max, premium budget 80bps."
    )
    intake_payload = {
        "underlying": "NVDA",
        "notional_usd": 50_000_000,
        "view": "earnings_hedge",
        "horizon_days": 30,
        "budget_bps_notional": 80,
        "premium_tolerance": "medium",
        "capped_upside_ok": False,
        "barrier_appetite": False,
        "constraints": ["expiry past earnings"],
        "clarifications_needed": [],
    }
    expected = {
        "underlying": "NVDA",
        "notional_usd": 50_000_000,
        "view": "earnings_hedge",
        "horizon_days": 30,
        "budget_bps_notional": 80,
        "premium_tolerance": "medium",
    }
    # very_high vol regime requires sigma >= 0.40
    session = _run_rfq(
        rfq_text=rfq,
        intake_payload=intake_payload,
        spot=900.0,
        vol_30d=0.55,
        vol_90d=0.45,
        expected_objective=expected,
    )
    # very_high vol regime must be classified by the orchestrator.
    assert session.regime is not None
    assert session.regime.vol_regime == "very_high", (
        f"vol 55% should classify as very_high, got {session.regime.vol_regime}"
    )


# ---------------------------------------------------------------------------
# RFQ #5 — neutral / yield, $1B, 30d, credit budget on IWM (covered call)
# ---------------------------------------------------------------------------


def test_rfq_neutral_yield_iwm_1bn_30d_credit():
    """Neutral / income view in high vol — sell-premium products.

    Exercises: neutral view + short horizon + credit budget + high vol.
    Rules row: COVERED_CALL / PUT_SPREAD / RISK_REVERSAL. Note: this row
    has *no* long-protective put leg, so the protective-Greeks check must
    only trigger when a long-put-style candidate is present. We verify
    the candidate kinds are sell-premium structures and skip the
    protective-put check by routing through a custom assertion path.
    """
    rfq = (
        "Quant fund with $1B long IWM, expects a range-bound month and "
        "wants to monetise elevated put-wing IV. Net-credit structures "
        "preferred, 30-day tenor, no barriers."
    )
    intake_payload = {
        "underlying": "IWM",
        "notional_usd": 1_000_000_000,
        "view": "neutral",
        "horizon_days": 30,
        # ClientObjective schema enforces budget_bps_notional >= 0, but
        # _budget_band's first branch returns "zero" when bps<=0 BEFORE
        # the credit check. So a credit posture must be encoded with
        # bps>0 AND premium_tolerance="credit" — the credit branch then
        # fires via the second if. We use 1bp as the smallest positive
        # value the schema accepts that still routes to the credit row.
        "budget_bps_notional": 1,
        "premium_tolerance": "credit",
        "capped_upside_ok": True,
        "barrier_appetite": False,
        "constraints": [],
        "clarifications_needed": [],
    }
    expected = {
        "underlying": "IWM",
        "notional_usd": 1_000_000_000,
        "view": "neutral",
        "horizon_days": 30,
        "budget_bps_notional": 1,
        "premium_tolerance": "credit",
        "capped_upside_ok": True,
        "barrier_appetite": False,
    }

    _install_intake_replay(intake_payload)
    with _fake_market_factory(spot=210.0, vol_30d=0.28, vol_90d=0.26):
        orch = _make_orchestrator()
        session = orch.start_session(intake_nl=rfq)
        _assert_objective_matches(session, expected)

        session = orch.decide_gate(session.session_id, Gate.A, approved=True)
        assert session.status == SessionStatus.AWAITING_GATE_B
        assert len(session.candidates) == 3

        session = orch.decide_gate(session.session_id, Gate.B, approved=True)
        assert session.status == SessionStatus.AWAITING_GATE_C
        # Sell-premium row: covered_call / put_spread / risk_reversal.
        kinds = {c.kind.value for c in session.candidates}
        assert kinds & {"covered_call", "risk_reversal", "put_spread"}, (
            f"neutral/credit RFQ produced no sell-premium candidate; got {kinds}"
        )

        _assert_priced_basics(session.priced)
        # Skip _assert_protective_put_greeks here — covered-call row has no
        # long put. Instead verify the covered-call leg has delta<0 (short
        # call delta is negative on a per-share long-equity basis).
        cc = next(
            (p for p in session.priced if p.candidate.kind.value == "covered_call"),
            None,
        )
        if cc is not None:
            # Short call → aggregated delta on the option leg is negative.
            assert cc.greeks.delta < 0, (
                f"covered call: expected delta<0 (short call), got {cc.greeks.delta:.4f}"
            )

        assert len(session.scenarios) == 3
        _assert_validator_findings_severity(session.validator)
        _assert_memo_complete(session.memo, session.priced)

        session = orch.decide_gate(session.session_id, Gate.C, approved=True)
        assert session.status == SessionStatus.DONE


# ---------------------------------------------------------------------------
# RFQ #6 — mildly_bullish on XLE, $250M, 18mo, high budget
# ---------------------------------------------------------------------------


def test_rfq_mildly_bullish_xle_250m_18mo_high():
    """Mildly bullish on energy ETF over a long horizon with a healthy budget.

    Exercises: mildly_bullish view + long horizon + high budget. The rules
    table now has a dedicated mildly_bullish row producing call-side
    candidates (CALL_SPREAD / LONG_CALL / RISK_REVERSAL).
    """
    rfq = (
        "Energy desk, $250M long XLE. Mildly bullish on the energy complex "
        "over the next 18 months but want to hedge the left tail. Premium "
        "budget is generous — up to 200bps of notional, no barriers."
    )
    intake_payload = {
        "underlying": "XLE",
        "notional_usd": 250_000_000,
        "view": "mildly_bullish",
        "horizon_days": 540,
        "budget_bps_notional": 200,
        "premium_tolerance": "high",
        "capped_upside_ok": False,
        "barrier_appetite": False,
        "constraints": [],
        "clarifications_needed": [],
    }
    expected = {
        "underlying": "XLE",
        "notional_usd": 250_000_000,
        "view": "mildly_bullish",
        "horizon_days": 540,
        "budget_bps_notional": 200,
        "premium_tolerance": "high",
        "capped_upside_ok": False,
        "barrier_appetite": False,
    }

    _install_intake_replay(intake_payload)
    with _fake_market_factory(spot=88.0, vol_30d=0.24, vol_90d=0.23):
        orch = _make_orchestrator()
        session = orch.start_session(intake_nl=rfq)
        _assert_objective_matches(session, expected)

        session = orch.decide_gate(session.session_id, Gate.A, approved=True)
        assert session.status == SessionStatus.AWAITING_GATE_B
        assert len(session.candidates) == 3, (
            f"mildly_bullish row should produce 3 candidates; got {len(session.candidates)}"
        )

        session = orch.decide_gate(session.session_id, Gate.B, approved=True)
        assert session.status == SessionStatus.AWAITING_GATE_C
        _assert_priced_basics(session.priced)
        _assert_bullish_call_leg(session.priced)
        assert len(session.scenarios) == 3
        _assert_validator_findings_severity(session.validator)
        _assert_memo_complete(session.memo, session.priced)

        session = orch.decide_gate(session.session_id, Gate.C, approved=True)
        assert session.status == SessionStatus.DONE
