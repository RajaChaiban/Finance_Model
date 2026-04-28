"""Multi-agent structuring co-pilot.

A planner-and-specialists architecture layered on top of the QuantLib-backed
pricing pipeline. Seven agents:

  * IntakeAgent      — RFQ (form or NL) → typed ClientObjective
  * StrategistAgent  — ClientObjective + MarketRegime → 3 candidate structures
  * PricingAgent     — Each candidate's legs → price + Greeks via existing engines
  * ScenarioAgent    — Each priced candidate → client-outcome scenarios + hedgeability
  * ValidatorAgent   — No-arb / parity / structural invariants
  * NarratorAgent    — 3-way comparison memo + recommendation
  * OrchestratorAgent — State machine, HITL gates A/B/C, audit trail

State flows through a single Pydantic StructuringSession (state.py). Agents
never call each other directly — the orchestrator mediates every step.
"""

from .state import (
    ClientObjective,
    MarketRegime,
    Leg,
    Candidate,
    GreeksSnapshot,
    PricedCandidate,
    ScenarioRow,
    ScenarioReport,
    Severity,
    StructureKind,
    ValidatorFinding,
    ValidatorReport,
    TermSheetSnippet,
    MemoArtifact,
    AuditEntry,
    Gate,
    SessionStatus,
    StructuringSession,
)

__all__ = [
    "ClientObjective",
    "MarketRegime",
    "Leg",
    "Candidate",
    "GreeksSnapshot",
    "PricedCandidate",
    "ScenarioRow",
    "ScenarioReport",
    "Severity",
    "StructureKind",
    "ValidatorFinding",
    "ValidatorReport",
    "TermSheetSnippet",
    "MemoArtifact",
    "AuditEntry",
    "Gate",
    "SessionStatus",
    "StructuringSession",
]
