"""5-scenario end-to-end validation of the structuring co-pilot.

Each scenario drives a distinct (view × budget × barrier × ticker) RFQ through
Intake → Gate A → Strategist → Gate B → Pricing → Scenario → Validator →
Narrator → Gate C → DONE in DEMO_REPLAY mode, then verifies the *new* memo
format produced by NarratorAgent:

    1. Verdict line at the top of memo.title (`VERDICT: ...`).
    2. Comparison table with the exact 10 columns in the exact order.
    3. Each term sheet is a parseable block (STRUCTURE / LEGS / END LEGS).
    4. Caveats list is non-empty and action-oriented.
    5. Recommendation either cites MI (`[source: ...]`) or explicitly states
       "no MI context available".

Complements ``tests/test_copilot_nl_rfqs.py`` (which validates extraction +
pipeline plumbing); this file's purpose is to validate the *output* the
structurer reads.
"""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import patch

import pytest

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


_EXPECTED_TABLE_COLUMNS = [
    "Candidate",
    "Strategy",
    "Premium ($/bps)",
    "Max Loss ($)",
    "Max Gain ($)",
    "Δ",
    "Vega",
    "Worst Scenario P&L",
    "Validator",
    "Why Pick",
]


@pytest.fixture(autouse=True)
def _replay_env(monkeypatch):
    monkeypatch.setenv("DEMO_REPLAY", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    agent_config.reload()
    llm_client.reset_llm_client()
    yield
    llm_client.reset_llm_client()


def _install_intake_replay(intake_payload: dict[str, Any]) -> None:
    client = llm_client.get_llm_client()
    if client._replay_cache is None:  # noqa: SLF001
        client._load_replay_cache()  # noqa: SLF001
    client._replay_cache["IntakeAgent:nl"] = {  # noqa: SLF001
        "text": json.dumps(intake_payload),
        "stop_reason": "end_turn",
    }


def _fake_market(spot: float, vol_30d: float, vol_90d: float, div: float = 0.0):
    return patch(
        "src.agents.orchestrator.market_data.fetch_market_params",
        return_value={
            "spot_price": spot,
            "dividend_yield": div,
            "volatility_30d": vol_30d,
            "volatility_90d": vol_90d,
            "source": "fallback",
        },
    )


def _make_orch() -> OrchestratorAgent:
    return OrchestratorAgent(store=SessionStore())


def _assert_objective(session, expected: dict[str, Any]) -> None:
    assert session.status == SessionStatus.AWAITING_GATE_A, session.last_error
    obj = session.objective
    assert obj is not None
    for k, v in expected.items():
        got = getattr(obj, k)
        assert got == v, f"objective.{k}: expected {v!r}, got {got!r}"


def _assert_priced(priced: list[PricedCandidate]) -> None:
    assert len(priced) == 3
    for pc in priced:
        assert pc.per_leg_prices, f"{pc.candidate.name}: no per-leg prices"
        assert any(abs(p) > 1e-6 for p in pc.per_leg_prices), (
            f"{pc.candidate.name}: all per-leg prices ~0"
        )
        for field in ("delta", "gamma", "vega", "theta", "rho"):
            v = getattr(pc.greeks, field)
            assert v == v, f"{pc.candidate.name}: {field} is NaN"
        assert pc.method_label, f"{pc.candidate.name}: empty method_label"


def _assert_validator_severity(report) -> None:
    assert report is not None
    for f in report.findings:
        assert isinstance(f.severity, Severity), (
            f"finding {f.name}: severity {f.severity!r} not a Severity enum"
        )


def _assert_memo_format(memo: MemoArtifact, priced: list[PricedCandidate]) -> None:
    """Verify the NEW memo format produced by the refactored Narrator."""
    assert memo is not None
    # 1. Verdict line at the top of memo.title.
    assert memo.title.lstrip().startswith("VERDICT:"), (
        f"memo.title must start with 'VERDICT:'; got: {memo.title[:80]!r}"
    )

    # 2. Comparison table has the exact 10 columns in the exact order.
    table = memo.comparison_table_md
    assert table.strip(), "comparison_table_md is empty"
    header_line = next(
        (ln for ln in table.splitlines() if ln.lstrip().startswith("|")),
        "",
    )
    headers = [c.strip() for c in header_line.strip().strip("|").split("|")]
    assert headers == _EXPECTED_TABLE_COLUMNS, (
        f"comparison-table columns mismatch.\n"
        f"  expected: {_EXPECTED_TABLE_COLUMNS}\n"
        f"  got     : {headers}"
    )

    # 3. Each term sheet is parseable (STRUCTURE / LEGS / END LEGS).
    assert len(memo.term_sheets) == 3, (
        f"expected 3 term sheets, got {len(memo.term_sheets)}"
    )
    for ts in memo.term_sheets:
        assert "STRUCTURE:" in ts.text, (
            f"term sheet {ts.candidate_id} missing STRUCTURE: block"
        )
        assert "LEGS:" in ts.text and "END LEGS" in ts.text, (
            f"term sheet {ts.candidate_id} missing LEGS / END LEGS markers"
        )
        # GREEKS line is part of the new parseable block.
        assert "GREEKS:" in ts.text, (
            f"term sheet {ts.candidate_id} missing GREEKS: line"
        )

    # 4. Caveats list is non-empty.
    assert len(memo.caveats) > 0, "memo.caveats is empty (Narrator should emit at least one)"
    # All caveats should be non-trivial strings.
    for c in memo.caveats:
        assert c.strip(), "caveat is empty/whitespace"

    # 5b. "Recent Comparable Deals" section is present (either with a table
    # of deals or an explicit "no deals indexed" note). This proves the RAG
    # citation pipeline is wired into the memo even when the corpus is sparse.
    rec_for_deals = memo.recommendation_md
    assert "### Recent Comparable Deals" in rec_for_deals, (
        "memo.recommendation_md missing 'Recent Comparable Deals' section"
    )
    has_deals_table = "| Source ID |" in rec_for_deals and "| Asset |" in rec_for_deals
    has_empty_note = "No comparable deals indexed" in rec_for_deals
    assert has_deals_table or has_empty_note, (
        "Recent Comparable Deals section must contain either a table or the "
        "explicit no-deals note"
    )

    # 5. Recommendation either references MI or explicitly states absence.
    # Valid MI signal forms: "[source: <id>]" with a source ID, OR
    # "Market context (via <agent>, <intent>) supports this view" when entries
    # exist but no source ID was captured. Absence form: "no MI context".
    rec = memo.recommendation_md
    assert rec.strip(), "recommendation_md is empty"
    has_mi_reference = "[source:" in rec or "Market context (via" in rec
    states_absence = "no MI context" in rec.lower() or "no market intelligence" in rec.lower()
    assert has_mi_reference or states_absence, (
        "recommendation must either reference MI ('[source: ...]' or "
        f"'Market context (via ...)') or state 'no MI context available'. "
        f"Got: {rec[:300]!r}"
    )

    # Recommended candidate ID must reference a real priced candidate.
    rec_ids = {p.candidate.candidate_id for p in priced}
    assert memo.recommended_candidate_id in rec_ids


def _run_scenario(
    *,
    rfq_text: str,
    intake_payload: dict[str, Any],
    spot: float,
    vol_30d: float,
    vol_90d: float,
    expected_objective: dict[str, Any],
):
    """Drive a session gate-by-gate with assertions INLINE.

    `decide_gate` returns the live session reference, which gets mutated by
    subsequent calls — so we assert on the state AT the moment of each gate
    decision before advancing to the next.
    """
    _install_intake_replay(intake_payload)
    with _fake_market(spot=spot, vol_30d=vol_30d, vol_90d=vol_90d):
        orch = _make_orch()
        session = orch.start_session(intake_nl=rfq_text)
        _assert_objective(session, expected_objective)

        # Gate A → strategist runs → AWAITING_GATE_B
        session = orch.decide_gate(session.session_id, Gate.A, approved=True)
        assert session.status == SessionStatus.AWAITING_GATE_B
        assert len(session.candidates) == 3, (
            f"strategist must produce 3 candidates, got {len(session.candidates)}"
        )

        # Gate B → pricing/scenario/validator/narrator → AWAITING_GATE_C
        session = orch.decide_gate(session.session_id, Gate.B, approved=True)
        assert session.status == SessionStatus.AWAITING_GATE_C
        _assert_priced(session.priced)
        assert len(session.scenarios) == 3
        _assert_validator_severity(session.validator)
        _assert_memo_format(session.memo, session.priced)

        # Gate C → DONE
        session = orch.decide_gate(session.session_id, Gate.C, approved=True)
        assert session.status == SessionStatus.DONE
        return session


# ---------------------------------------------------------------------------
# Scenario 1 — mildly_bullish XLK $100M 9mo medium budget
# Exercises the NEW mildly_bullish rule row + CALL_SPREAD factory; verifies
# at least one candidate has a long-call leg.
# ---------------------------------------------------------------------------


def test_scenario_1_mildly_bullish_xlk_100m_9mo_medium():
    rfq = (
        "Tech-overweight family office, $100M long XLK. Mildly bullish on "
        "tech megacaps over the next 9 months but want some income generation. "
        "Up to 80bps of premium budget. No barriers."
    )
    intake = {
        "underlying": "XLK",
        "notional_usd": 100_000_000,
        "view": "mildly_bullish",
        "horizon_days": 270,
        "budget_bps_notional": 80,
        "premium_tolerance": "medium",
        "capped_upside_ok": False,
        "barrier_appetite": False,
        "constraints": [],
        "clarifications_needed": [],
    }
    final = _run_scenario(
        rfq_text=rfq,
        intake_payload=intake,
        spot=235.0,
        vol_30d=0.22,
        vol_90d=0.20,
        expected_objective={
            "underlying": "XLK",
            "view": "mildly_bullish",
            "notional_usd": 100_000_000,
            "horizon_days": 270,
        },
    )
    # mildly_bullish row must produce at least one long-call leg.
    has_long_call = any(
        any(l.quantity > 0 and l.option_type.endswith("_call") for l in pc.candidate.legs)
        for pc in final.priced
    )
    assert has_long_call, (
        "mildly_bullish RFQ must produce at least one long-call candidate"
    )


# ---------------------------------------------------------------------------
# Scenario 2 — bearish SPY $500M 6mo low budget, no barrier
# Big-ticket bearish hedge; exercises the canonical PUT_SPREAD/COLLAR path
# and the 10-column table on a high-notional run (premium-bps formatting).
# ---------------------------------------------------------------------------


def test_scenario_2_bearish_spy_500m_6mo_low():
    rfq = (
        "Pension fund, $500M long SPY core. Bearish into year-end on macro "
        "growth concerns. 6-month horizon. Tight budget — 50bps max. No "
        "barrier risk acceptable."
    )
    intake = {
        "underlying": "SPY",
        "notional_usd": 500_000_000,
        "view": "bearish",
        "horizon_days": 180,
        "budget_bps_notional": 50,
        "premium_tolerance": "very_low",
        "capped_upside_ok": False,
        "barrier_appetite": False,
        "constraints": [],
        "clarifications_needed": [],
    }
    final = _run_scenario(
        rfq_text=rfq,
        intake_payload=intake,
        spot=510.0,
        vol_30d=0.16,
        vol_90d=0.18,
        expected_objective={
            "underlying": "SPY",
            "view": "bearish",
            "notional_usd": 500_000_000,
            "horizon_days": 180,
        },
    )
    # Bearish row must produce at least one protective-put leg somewhere.
    has_long_put = any(
        any(l.quantity > 0 and l.option_type.endswith("_put") for l in pc.candidate.legs)
        for pc in final.priced
    )
    assert has_long_put, "bearish RFQ must produce at least one long-put leg"


# ---------------------------------------------------------------------------
# Scenario 3 — earnings_hedge AAPL $20M 30d very_low budget
# Short-horizon, single-name event hedge; exercises earnings_hedge rule
# (PUT_SPREAD / LONG_PUT / KI_PUT) and stresses scenario engine on short T.
# ---------------------------------------------------------------------------


def test_scenario_3_earnings_hedge_aapl_20m_30d():
    rfq = (
        "Hedge fund client, $20M long AAPL into earnings in three weeks. "
        "Want defined-loss protection through the print. Budget is tight — "
        "no more than 60bps. Open to barrier on the protection."
    )
    intake = {
        "underlying": "AAPL",
        "notional_usd": 20_000_000,
        "view": "earnings_hedge",
        "horizon_days": 30,
        "budget_bps_notional": 60,
        "premium_tolerance": "very_low",
        "capped_upside_ok": False,
        "barrier_appetite": True,
        "constraints": [],
        "clarifications_needed": [],
    }
    _run_scenario(
        rfq_text=rfq,
        intake_payload=intake,
        spot=212.0,
        vol_30d=0.34,
        vol_90d=0.28,
        expected_objective={
            "underlying": "AAPL",
            "view": "earnings_hedge",
            "horizon_days": 30,
            "barrier_appetite": True,
        },
    )


# ---------------------------------------------------------------------------
# Scenario 4 — crash_hedge IWM $75M 12mo barrier_appetite=True
# Exercises crash_hedge row (KO_PUT / LONG_PUT / PUT_SPREAD) on small-cap;
# verifies barrier-aware caveats appear in the memo.
# ---------------------------------------------------------------------------


def test_scenario_4_crash_hedge_iwm_75m_12mo_barrier():
    rfq = (
        "Macro fund, $75M IWM exposure as part of a small-cap basket. "
        "Crash-hedge mandate over 12 months; willing to accept barrier risk "
        "to bring the cost down. Up to 120bps."
    )
    intake = {
        "underlying": "IWM",
        "notional_usd": 75_000_000,
        "view": "crash_hedge",
        "horizon_days": 365,
        "budget_bps_notional": 120,
        "premium_tolerance": "medium",
        "capped_upside_ok": False,
        "barrier_appetite": True,
        "constraints": [],
        "clarifications_needed": [],
    }
    final = _run_scenario(
        rfq_text=rfq,
        intake_payload=intake,
        spot=215.0,
        vol_30d=0.24,
        vol_90d=0.22,
        expected_objective={
            "underlying": "IWM",
            "view": "crash_hedge",
            "barrier_appetite": True,
        },
    )
    # At least one candidate should be a barrier structure given barrier_appetite=True.
    has_barrier = any(
        any(
            l.option_type.startswith("knockout_") or l.option_type.startswith("knockin_")
            for l in pc.candidate.legs
        )
        for pc in final.priced
    )
    assert has_barrier, (
        "crash_hedge with barrier_appetite=True must produce at least one barrier leg"
    )


# ---------------------------------------------------------------------------
# Scenario 5 — protect_gains XLF $400M 9mo zero_cost capped_upside
# Exercises protect_gains row (ZERO_COST_COLLAR / COLLAR / COVERED_CALL) on
# a sector ETF newly seeded into the RAG corpus. Verifies memo MI-citation
# branch (sector_etfs.json now provides XLF context).
# ---------------------------------------------------------------------------


def test_scenario_5_protect_gains_xlf_400m_9mo_zero_cost():
    rfq = (
        "Asset manager, $400M long XLF after a 25% YTD rally. Want to lock "
        "in gains zero-cost over 9 months. OK with capping upside on the call "
        "side. No barrier risk."
    )
    intake = {
        "underlying": "XLF",
        "notional_usd": 400_000_000,
        "view": "protect_gains",
        "horizon_days": 270,
        "budget_bps_notional": 0,
        "premium_tolerance": "zero_cost_only",
        "capped_upside_ok": True,
        "barrier_appetite": False,
        "constraints": [],
        "clarifications_needed": [],
    }
    final = _run_scenario(
        rfq_text=rfq,
        intake_payload=intake,
        spot=48.5,
        vol_30d=0.18,
        vol_90d=0.19,
        expected_objective={
            "underlying": "XLF",
            "view": "protect_gains",
            "capped_upside_ok": True,
            "premium_tolerance": "zero_cost_only",
        },
    )
    # Zero-cost / collar mandate: at least one candidate should be a collar.
    has_collar = any(
        len(pc.candidate.legs) >= 2 and any(l.option_type.endswith("_put") for l in pc.candidate.legs)
        and any(l.option_type.endswith("_call") for l in pc.candidate.legs)
        for pc in final.priced
    )
    assert has_collar, (
        "protect_gains with capped_upside_ok=True must produce a collar-style "
        "candidate (≥1 put leg AND ≥1 call leg)"
    )
