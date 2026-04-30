"""Tests for the MarketIntelligence (RAG) layer wired through the agent pipeline.

Two layers of coverage:

1. Unit — each agent that calls into MarketIntelligence (Intake, Strategist,
   Pricing, Scenario, Validator) hits the right method with the right args.
   Uses an in-memory FakeVectorStore + a deterministic FakeLLMCall — no
   chromadb, no sentence-transformers, no network.

2. Integration — full Intake→Strategist→Pricing→Scenario→Validator→Narrator
   flow with a pre-seeded FakeMarketIntelligence. Asserts:
     - market_context SSE events are emitted by the orchestrator
     - the final memo references at least one source id from the corpus
     - MARKET_INTEL_ENABLED=0 cleanly disables MI without breaking the pipeline
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest

# Replay mode + no key — keep the pre-existing offline-safe test posture.
os.environ.setdefault("DEMO_REPLAY", "1")
os.environ.setdefault("GEMINI_API_KEY", "")

from src.agents import llm_client  # noqa: E402
from src.agents.market_intelligence import (  # noqa: E402
    MarketIntelligence,
    QueryResponse,
    SearchResult,
    VectorStore,
    set_market_intelligence,
    reset_market_intelligence,
)
from src.agents.orchestrator import OrchestratorAgent, SessionStore  # noqa: E402
from src.agents.state import (  # noqa: E402
    Candidate,
    ClientObjective,
    Gate,
    GreeksSnapshot,
    Leg,
    MarketRegime,
    PricedCandidate,
    SessionStatus,
    StructureKind,
    StructuringSession,
)
from src.config import agent_config  # noqa: E402


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class FakeVectorStore(VectorStore):
    """In-memory store. Returns docs whose metadata.asset_class matches the
    filter (or all docs if no filter); ranks by simple keyword overlap."""

    docs: list[dict] = field(default_factory=list)

    def add_documents(self, docs):
        for d in docs:
            self.docs.append(
                {
                    "id": d.id,
                    "content": d.content,
                    "metadata": dict(d.metadata or {}),
                }
            )

    def search(self, query: str, k: int = 5, filters: dict | None = None):
        out = []
        for d in self.docs:
            md = d["metadata"] or {}
            if filters:
                if not all(md.get(fk) == fv for fk, fv in filters.items()):
                    continue
            # Simple lexical score: count of query words in content.
            score = 0.0
            qwords = [w for w in query.lower().split() if len(w) > 2]
            content_lower = d["content"].lower()
            for w in qwords:
                if w in content_lower:
                    score += 0.2
            out.append(
                SearchResult(
                    doc_id=d["id"],
                    content=d["content"],
                    metadata=md,
                    score=min(0.99, 0.4 + score),
                )
            )
        out.sort(key=lambda r: r.score, reverse=True)
        return out[:k]

    def count(self) -> int:
        return len(self.docs)


def _fake_llm_call(prompt: str, system: str | None = None) -> str:
    """Deterministic fake LLM. Surfaces well-known marker phrases the
    agents key off of, while staying short.
    """
    p_lower = (prompt or "").lower()
    if "market window" in p_lower or "issuance" in p_lower:
        return (
            "Market is OPEN for SPY structures. 1M ATM IV 14.5, 3M ATM IV 16.2, "
            "skew well-behaved, listed liquidity strong on barriers within 80-110%."
        )
    if "pricing" in p_lower or "tranche" in p_lower:
        return (
            "Comparable deals print 95-145bps of notional for 3M structures. "
            "Recent KO put printed at 95bps (~30% cheaper than vanilla put)."
        )
    if "deal" in p_lower or "structure" in p_lower:
        return (
            "Structure is in line with recent comparables. Premium is mid-range; "
            "delta sign matches the directional view."
        )
    return "Recent corpus context: vol regime normal, no extreme dislocations."


def _build_fake_mi() -> MarketIntelligence:
    """Construct a MarketIntelligence wired to the fake stores. We bypass the
    real ChromaVectorStore + EmbeddingsManager construction by injecting the
    fake store post-init.
    """
    # Pre-build with empty stores; we'll overwrite immediately.
    mi = MarketIntelligence.__new__(MarketIntelligence)
    fake_store = FakeVectorStore()
    mi.vector_store = fake_store

    # The MI's RetrievalEngine is just a thin wrapper over a VectorStore;
    # we can re-use the upstream class against the fake store.
    from src.agents.market_intelligence import RetrievalEngine

    mi.retrieval = RetrievalEngine(fake_store)
    mi.llm = _fake_llm_call
    return mi


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_singletons(monkeypatch):
    monkeypatch.setenv("DEMO_REPLAY", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("MARKET_INTEL_ENABLED", "1")
    agent_config.reload()
    llm_client.reset_llm_client()
    reset_market_intelligence()
    yield
    llm_client.reset_llm_client()
    reset_market_intelligence()


@pytest.fixture
def fake_mi():
    mi = _build_fake_mi()
    # Seed a tiny corpus.
    mi.seed_from_dicts(
        [
            {
                "id": "spy-mw-test",
                "doc_type": "market_window",
                "asset_class": "SPY",
                "content": (
                    "SPY market window OPEN. Liquidity strong on SPY listed strikes "
                    "within ±15% of spot."
                ),
            },
            {
                "id": "spy-deal-test",
                "doc_type": "deal",
                "asset_class": "SPY",
                "content": (
                    "Comparable SPY 3M ATM put spread, $50M notional, recently "
                    "printed at 60bps net debit."
                ),
            },
            {
                "id": "spy-pb-test",
                "doc_type": "pricing_benchmark",
                "asset_class": "SPY",
                "content": (
                    "Pricing benchmark Q1 2026: 3M ATM SPY put around 138bps; "
                    "3M ATM call around 145bps."
                ),
            },
        ]
    )
    set_market_intelligence(mi)
    return mi


@pytest.fixture
def fixed_market_data():
    fake = {
        "spot_price": 500.0,
        "dividend_yield": 0.015,
        "volatility_30d": 0.18,
        "volatility_90d": 0.20,
        "source": "fallback",
    }
    with patch("src.agents.orchestrator.market_data.fetch_market_params", return_value=fake):
        yield fake


# ---------------------------------------------------------------------------
# Unit tests — each agent talks to the right MI method with the right args
# ---------------------------------------------------------------------------


def test_intake_calls_general_query_with_underlying(fake_mi):
    from src.agents.intake import IntakeAgent

    obj = ClientObjective(
        underlying="SPY",
        notional_usd=10_000_000,
        view="bearish",
        horizon_days=90,
        budget_bps_notional=80,
        raw_rfq="protect $10M long SPY for 3 months",
    )
    sess = StructuringSession(objective=obj, intake_form=obj.model_dump())

    captured: dict[str, Any] = {}
    original_general_query = fake_mi.general_query

    def _spy_general_query(query, asset_class=None):
        captured["query"] = query
        captured["asset_class"] = asset_class
        return original_general_query(query=query, asset_class=asset_class)

    fake_mi.general_query = _spy_general_query

    agent = IntakeAgent(mi=fake_mi)
    sess = agent.run(sess)

    assert "SPY" in captured["query"]
    assert captured["asset_class"] == "SPY"
    # Intake recorded a market_context entry.
    assert any(e.get("agent") == "IntakeAgent" for e in sess.market_context)
    assert any(e.get("intent") == "general" for e in sess.market_context)


def test_strategist_calls_query_market_window(fake_mi):
    from src.agents.strategist import StrategistAgent

    obj = ClientObjective(
        underlying="SPY",
        notional_usd=10_000_000,
        view="bearish",
        horizon_days=90,
        budget_bps_notional=80,
        capped_upside_ok=True,
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
    sess = StructuringSession(objective=obj, regime=regime)

    captured: dict[str, Any] = {}
    original = fake_mi.query_market_window

    def _spy_market_window(asset_class, context=None):
        captured["asset_class"] = asset_class
        captured["context"] = context
        return original(asset_class=asset_class, context=context)

    fake_mi.query_market_window = _spy_market_window

    agent = StrategistAgent(mi=fake_mi)
    sess = agent.run(sess)

    assert captured["asset_class"] == "SPY"
    assert "horizon" in (captured.get("context") or "").lower()
    assert any(e.get("intent") == "market_window" for e in sess.market_context)


def test_strategist_softens_rationales_when_market_closed(fake_mi):
    """If the LLM answer contains 'CLOSED', candidate rationales must lead with
    the market-window warning."""
    from src.agents.strategist import StrategistAgent, _CLOSED_WINDOW_WARNING

    fake_mi.llm = lambda prompt, system=None: (
        "Market window is CLOSED for SPY structures this week."
    )

    obj = ClientObjective(
        underlying="SPY",
        notional_usd=10_000_000,
        view="bearish",
        horizon_days=90,
        budget_bps_notional=80,
        capped_upside_ok=True,
    )
    regime = MarketRegime(
        underlying="SPY",
        spot=500.0,
        risk_free_rate=0.045,
        realised_vol_30d=0.18,
        vol_regime="normal",
    )
    sess = StructuringSession(objective=obj, regime=regime)

    agent = StrategistAgent(mi=fake_mi)
    sess = agent.run(sess)

    assert sess.candidates
    for c in sess.candidates:
        assert c.rationale.startswith(_CLOSED_WINDOW_WARNING.split(":")[0])


def test_pricing_calls_query_pricing_per_candidate(fake_mi):
    from src.agents.pricing import PricingAgent

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
    cand = Candidate(
        kind=StructureKind.LONG_PUT,
        name="3M ATM put",
        legs=[Leg(option_type="european_put", strike=500.0, expiry_days=90, quantity=+1.0)],
        rationale="protective put",
        notional_usd=10_000_000,
    )
    sess = StructuringSession(objective=obj, regime=regime, candidates=[cand])

    calls: list[dict] = []
    original = fake_mi.query_pricing

    def _spy_pricing(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    fake_mi.query_pricing = _spy_pricing

    agent = PricingAgent(mi=fake_mi)
    sess = agent.run(sess)

    assert len(calls) == 1
    assert calls[0]["asset_class"] == "SPY"
    assert calls[0]["tranche_type"] == "long_put"
    assert calls[0]["deal_size"] == 10_000_000
    assert any(e.get("intent") == "pricing" for e in sess.market_context)


def test_validator_flags_structure_with_no_precedent(fake_mi):
    from src.agents.validator import ValidatorAgent

    fake_mi.llm = lambda prompt, system=None: (
        "This structure is unusual — no comparable trades found in the corpus."
    )

    obj = ClientObjective(
        underlying="SPY",
        notional_usd=10_000_000,
        view="bearish",
        horizon_days=90,
        budget_bps_notional=80,
    )
    regime = MarketRegime(underlying="SPY", spot=500.0, vol_regime="normal")

    cand = Candidate(
        kind=StructureKind.LONG_PUT,
        name="exotic test",
        legs=[Leg(option_type="european_put", strike=500.0, expiry_days=90, quantity=+1.0)],
        rationale="placeholder",
        notional_usd=10_000_000,
    )
    priced = PricedCandidate(
        candidate=cand,
        net_premium=100_000,
        net_premium_bps=10.0,
        greeks=GreeksSnapshot(delta=-0.5),
    )
    sess = StructuringSession(
        objective=obj, regime=regime, candidates=[cand], priced=[priced]
    )

    agent = ValidatorAgent(mi=fake_mi)
    sess = agent.run(sess)

    assert sess.validator is not None
    finding_names = [f.name for f in sess.validator.findings]
    assert "market_precedent_outlier" in finding_names


def test_narrator_appends_market_context_citations(fake_mi):
    """Narrator must read session.market_context (not re-query) and append a
    citations section to the memo's recommendation_md."""
    from src.agents.narrator import NarratorAgent

    obj = ClientObjective(
        underlying="SPY",
        notional_usd=10_000_000,
        view="bearish",
        horizon_days=90,
        budget_bps_notional=80,
    )
    cand = Candidate(
        kind=StructureKind.LONG_PUT,
        name="test",
        legs=[Leg(option_type="european_put", strike=500.0, expiry_days=90, quantity=+1.0)],
        rationale="r",
        notional_usd=10_000_000,
    )
    priced = PricedCandidate(
        candidate=cand, net_premium=100.0, net_premium_bps=10.0, greeks=GreeksSnapshot()
    )
    sess = StructuringSession(
        objective=obj,
        priced=[priced],
        candidates=[cand],
        market_context=[
            {
                "agent": "PricingAgent",
                "intent": "pricing",
                "answer": "3M ATM put for SPY trades around 138bps based on Q1 2026 prints.",
                "sources": [{"id": "spy-pb-test", "type": "pricing_benchmark", "score": 0.85}],
                "confidence": "high",
                "metadata": {},
            }
        ],
    )

    agent = NarratorAgent(mi=None)
    sess = agent.run(sess)

    assert sess.memo is not None
    assert "Market Intelligence Citations" in sess.memo.recommendation_md
    assert "spy-pb-test" in sess.memo.recommendation_md


# ---------------------------------------------------------------------------
# Integration test — full pipeline + SSE event capture
# ---------------------------------------------------------------------------


def test_full_pipeline_emits_market_context_events(fake_mi, fixed_market_data):
    """The orchestrator must emit at least one `market_context` SSE event,
    and the final memo must reference at least one source id from the corpus."""
    store = SessionStore()
    orch = OrchestratorAgent(store=store, market_intel=fake_mi)

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

    session = orch.decide_gate(session.session_id, Gate.A, approved=True)
    assert session.status == SessionStatus.AWAITING_GATE_B

    session = orch.decide_gate(session.session_id, Gate.B, approved=True)
    assert session.status == SessionStatus.AWAITING_GATE_C
    assert session.memo is not None

    # Drain the event queue and assert at least one market_context event.
    seen_event_types: list[str] = []
    seen_market_ctx_payload_keys: list[str] = []
    while True:
        event = store.drain(session.session_id, timeout=0.0)
        if event is None:
            break
        seen_event_types.append(event.get("type", ""))
        if event.get("type") == "market_context":
            payload = event.get("payload") or {}
            seen_market_ctx_payload_keys.append(payload.get("intent", ""))

    assert "market_context" in seen_event_types, (
        f"expected market_context event, got {set(seen_event_types)}"
    )
    assert any(intent in seen_market_ctx_payload_keys for intent in ("market_window", "pricing", "general", "deal_analysis"))

    # The memo's market-intel section is populated AND mentions at least one
    # source id from the seeded corpus.
    rec = session.memo.recommendation_md or ""
    assert "Market Intelligence Citations" in rec
    seeded_ids = {"spy-mw-test", "spy-deal-test", "spy-pb-test"}
    assert any(s in rec for s in seeded_ids), (
        "Expected at least one corpus source id in the memo citations."
    )

    session = orch.decide_gate(session.session_id, Gate.C, approved=True)
    assert session.status == SessionStatus.DONE


def test_pipeline_runs_without_market_intel(fixed_market_data, monkeypatch):
    """When MARKET_INTEL_ENABLED=0, the full pipeline still completes — no MI
    calls, no citations section, no errors."""
    monkeypatch.setenv("MARKET_INTEL_ENABLED", "0")
    agent_config.reload()
    reset_market_intelligence()

    orch = OrchestratorAgent(store=SessionStore())
    assert orch.market_intel is None

    session = orch.start_session(
        intake_form={
            "underlying": "SPY",
            "notional_usd": 10_000_000,
            "view": "bearish",
            "horizon_days": 90,
            "budget_bps_notional": 50,
        },
    )
    session = orch.decide_gate(session.session_id, Gate.A, approved=True)
    session = orch.decide_gate(session.session_id, Gate.B, approved=True)
    assert session.status == SessionStatus.AWAITING_GATE_C
    assert session.memo is not None
    assert session.market_context == []
    assert "Market Intelligence Citations" not in (session.memo.recommendation_md or "")


def test_existing_llm_adapter_wraps_llm_client():
    """The adapter should turn an LLMClient into a callable matching the
    LLMCall protocol, returning the .text from the underlying response."""
    from src.agents.market_intelligence import existing_llm_adapter

    class _FakeLLMClient:
        def __init__(self):
            self.calls = []

        def complete(self, **kwargs):
            self.calls.append(kwargs)

            class _R:
                text = "ok"

            return _R()

    fc = _FakeLLMClient()
    call = existing_llm_adapter(fc, model="gemini-2.5-flash", agent_name="test-agent")
    out = call("hello", "you are helpful")
    assert out == "ok"
    assert fc.calls[0]["model"] == "gemini-2.5-flash"
    assert fc.calls[0]["agent_name"] == "test-agent"
    assert fc.calls[0]["system"] == "you are helpful"
    assert fc.calls[0]["messages"] == [{"role": "user", "content": "hello"}]


def test_market_context_low_confidence_no_results_skipped(fake_mi):
    """If general_query reports `confidence='low'` AND no sources AND its
    answer is the 'no relevant documents' boilerplate, that entry should NOT
    be appended to session.market_context."""
    from src.agents.intake import IntakeAgent

    # Replace retrieval engine to simulate empty corpus for this query.
    class _EmptyRetrieval:
        def retrieve(self, **kwargs):
            return []

    fake_mi.retrieval = _EmptyRetrieval()

    obj = ClientObjective(
        underlying="SPY",
        notional_usd=10_000_000,
        view="bearish",
        horizon_days=90,
        budget_bps_notional=80,
        raw_rfq="test",
    )
    sess = StructuringSession(objective=obj, intake_form=obj.model_dump())
    sess = IntakeAgent(mi=fake_mi).run(sess)

    assert sess.market_context == []
