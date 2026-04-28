"""OrchestratorAgent — state machine and SessionStore.

Drives the pipeline:

    Intake → [Gate A] → BuildRegime → Strategist → [Gate B] →
    Pricing → Scenario → Validator → Narrator → [Gate C] → DONE

The orchestrator is the *only* code path that mutates a session across agents.
Each agent gets a session, returns a session, and the orchestrator advances
status accordingly. Gates are entry points the API exposes; the API thread
calls `advance_session` after a gate decision and the orchestrator runs to
the next gate or to terminal status.

State storage is an in-memory dict keyed by session_id, guarded by an RLock.
A per-session `Queue[dict]` carries event payloads that the SSE endpoint
streams to the client. Events are best-effort: if no consumer is listening,
they're dropped on the next state transition.
"""

from __future__ import annotations

import logging
import threading
from queue import Empty, SimpleQueue
from typing import Any, Optional

from src.config.agent_config import get_agent_config
from src.data import market_data

from .base import AgentError
from .intake import IntakeAgent
from .narrator import NarratorAgent
from .pricing import PricingAgent
from .scenario import ScenarioAgent
from .state import (
    AuditEntry,
    ClientObjective,
    Gate,
    MarketRegime,
    SessionStatus,
    StructuringSession,
)
from .strategist import StrategistAgent
from .validator import ValidatorAgent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session store — single-process, in-memory. Phase 6 swaps for SQLite.
# ---------------------------------------------------------------------------


class SessionStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, StructuringSession] = {}
        self._queues: dict[str, SimpleQueue] = {}

    def add(self, session: StructuringSession) -> None:
        with self._lock:
            self._sessions[session.session_id] = session
            self._queues[session.session_id] = SimpleQueue()

    def get(self, session_id: str) -> Optional[StructuringSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def update(self, session: StructuringSession) -> None:
        with self._lock:
            self._sessions[session.session_id] = session

    def list_ids(self) -> list[str]:
        with self._lock:
            return list(self._sessions.keys())

    def queue(self, session_id: str) -> Optional[SimpleQueue]:
        with self._lock:
            return self._queues.get(session_id)

    def emit(self, session_id: str, event: dict[str, Any]) -> None:
        q = self.queue(session_id)
        if q is not None:
            q.put(event)

    def drain(self, session_id: str, timeout: float = 0.0) -> Optional[dict[str, Any]]:
        q = self.queue(session_id)
        if q is None:
            return None
        try:
            return q.get(timeout=timeout) if timeout > 0 else q.get_nowait()
        except Empty:
            return None


_GLOBAL_STORE: Optional[SessionStore] = None


def get_store() -> SessionStore:
    global _GLOBAL_STORE
    if _GLOBAL_STORE is None:
        _GLOBAL_STORE = SessionStore()
    return _GLOBAL_STORE


def reset_store() -> None:
    global _GLOBAL_STORE
    _GLOBAL_STORE = SessionStore()


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class OrchestratorAgent:
    """Owns the state machine. Stateless across sessions; uses SessionStore."""

    def __init__(self, store: Optional[SessionStore] = None) -> None:
        self.store = store or get_store()
        self.intake = IntakeAgent()
        self.strategist = StrategistAgent()
        self.pricing = PricingAgent()
        self.scenario = ScenarioAgent()
        self.validator = ValidatorAgent()
        self.narrator = NarratorAgent()

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def start_session(
        self,
        *,
        intake_form: Optional[dict] = None,
        intake_nl: Optional[str] = None,
    ) -> StructuringSession:
        session = StructuringSession(intake_form=intake_form, intake_nl=intake_nl)
        self.store.add(session)
        self._emit(session, "session_created", "Session created.")
        # Run intake immediately so the first thing the user sees at Gate A
        # is the parsed objective.
        self._safe_advance(session.session_id)
        return self.store.get(session.session_id) or session

    def decide_gate(
        self,
        session_id: str,
        gate: Gate,
        *,
        approved: bool,
        payload: Optional[dict] = None,
    ) -> StructuringSession:
        session = self.store.get(session_id)
        if session is None:
            raise AgentError(f"Unknown session_id: {session_id}")

        if gate == Gate.A:
            session.gate_a_decision = approved
            if approved and payload and "edits" in payload and payload["edits"]:
                try:
                    edited = ClientObjective(**payload["edits"])
                    session.gate_a_edits = edited
                    session.objective = edited
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Gate A edits invalid: %s", exc)
        elif gate == Gate.B:
            session.gate_b_decision = approved
            if payload:
                session.gate_b_swap = payload.get("swap")
        elif gate == Gate.C:
            session.gate_c_decision = approved
            if payload and "edits" in payload:
                session.gate_c_edits = payload.get("edits")
        else:
            raise AgentError(f"Unknown gate: {gate}")

        self.store.update(session)
        self._emit(session, "gate_decision", f"{gate.value}: approved={approved}")

        if not approved:
            session.status = SessionStatus.CANCELLED
            self.store.update(session)
            self._emit(session, "cancelled", f"Cancelled at {gate.value}.")
            return session

        # Approved — advance.
        self._safe_advance(session_id)
        return self.store.get(session_id) or session

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    def _safe_advance(self, session_id: str) -> None:
        session = self.store.get(session_id)
        if session is None:
            return
        try:
            self._advance(session)
        except AgentError as exc:
            logger.warning("Session %s soft error: %s", session_id, exc)
            session.status = SessionStatus.ERROR
            session.last_error = str(exc)
            self.store.update(session)
            self._emit(session, "error", str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Session %s hard error", session_id)
            session.status = SessionStatus.ERROR
            session.last_error = f"unhandled: {exc}"
            self.store.update(session)
            self._emit(session, "error", session.last_error)

    def _advance(self, session: StructuringSession) -> None:
        """Run the state machine until the next gate / terminal status."""

        # Drive forward until we hit something that needs the user.
        while True:
            if self._budget_exceeded(session):
                session.status = SessionStatus.ERROR
                session.last_error = (
                    f"Cost ceiling exceeded "
                    f"(${session.total_cost_usd:.4f} > ${get_agent_config().cost_ceiling_usd:.2f})."
                )
                self.store.update(session)
                self._emit(session, "error", session.last_error)
                return

            status = session.status

            if status == SessionStatus.PENDING_INTAKE:
                self._emit(session, "agent_started", "IntakeAgent")
                session = self.intake.run(session)
                session.status = SessionStatus.AWAITING_GATE_A
                self.store.update(session)
                self._emit(session, "agent_finished", "IntakeAgent")
                self._emit(session, "gate_pending", Gate.A.value)
                return

            if status == SessionStatus.AWAITING_GATE_A:
                if session.gate_a_decision is True:
                    # Approved — build regime then run strategist.
                    self._emit(session, "agent_started", "RegimeBuild")
                    session.regime = self._build_regime(session.objective)
                    session.status = SessionStatus.PENDING_STRATEGIST
                    self.store.update(session)
                    self._emit(session, "agent_finished", "RegimeBuild")
                    continue
                # Decision not yet made.
                return

            if status == SessionStatus.AWAITING_GATE_B:
                if session.gate_b_decision is True:
                    session.status = SessionStatus.PENDING_PRICING
                    self.store.update(session)
                    continue
                return

            if status == SessionStatus.AWAITING_GATE_C:
                if session.gate_c_decision is True:
                    session.status = SessionStatus.DONE
                    self.store.update(session)
                    self._emit(session, "done", "Session complete.")
                    return
                return

            if status == SessionStatus.PENDING_STRATEGIST:
                self._emit(session, "agent_started", "StrategistAgent")
                session = self.strategist.run(session)
                session.status = SessionStatus.AWAITING_GATE_B
                self.store.update(session)
                self._emit(session, "agent_finished", "StrategistAgent")
                self._emit(session, "gate_pending", Gate.B.value)
                return

            if status == SessionStatus.PENDING_PRICING:
                self._emit(session, "agent_started", "PricingAgent")
                session = self.pricing.run(session)
                session.status = SessionStatus.PENDING_SCENARIO
                self.store.update(session)
                self._emit(session, "agent_finished", "PricingAgent")
                continue

            if status == SessionStatus.PENDING_SCENARIO:
                self._emit(session, "agent_started", "ScenarioAgent")
                session = self.scenario.run(session)
                session.status = SessionStatus.PENDING_VALIDATION
                self.store.update(session)
                self._emit(session, "agent_finished", "ScenarioAgent")
                continue

            if status == SessionStatus.PENDING_VALIDATION:
                self._emit(session, "agent_started", "ValidatorAgent")
                session = self.validator.run(session)
                self._emit(session, "agent_finished", "ValidatorAgent")
                if session.validator and session.validator.has_blockers:
                    if session.validator_retries < 2:
                        session.validator_retries += 1
                        # Phase 3: feedback loop. Phase 1: surface to user at Gate C.
                        logger.info(
                            "Validator blocker(s) on session %s; surfacing at Gate C.",
                            session.session_id,
                        )
                session.status = SessionStatus.PENDING_NARRATOR
                self.store.update(session)
                continue

            if status == SessionStatus.PENDING_NARRATOR:
                self._emit(session, "agent_started", "NarratorAgent")
                session = self.narrator.run(session)
                session.status = SessionStatus.AWAITING_GATE_C
                self.store.update(session)
                self._emit(session, "agent_finished", "NarratorAgent")
                self._emit(session, "gate_pending", Gate.C.value)
                return

            # Unknown / terminal.
            return

    # ------------------------------------------------------------------
    # Regime build (Phase 1: minimal)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_regime(objective: ClientObjective) -> MarketRegime:
        """Phase 1 regime: spot / div / hist vol via existing market_data layer.

        Falls back to safe defaults when offline (DEMO_REPLAY) or when the
        ticker isn't found.
        """
        warnings: list[str] = []
        params: dict[str, Any] = {}
        try:
            params = market_data.fetch_market_params(objective.underlying)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"market_data fetch failed: {exc}")

        spot = params.get("spot_price") or 100.0
        if not params.get("spot_price"):
            warnings.append("Spot price missing; using $100 placeholder.")

        regime = MarketRegime(
            underlying=objective.underlying,
            spot=float(spot),
            dividend_yield=float(params.get("dividend_yield") or 0.015),
            risk_free_rate=0.045,  # Phase 2: pull SOFR from FRED
            realised_vol_30d=params.get("volatility_30d"),
            realised_vol_90d=params.get("volatility_90d"),
            data_source_warnings=warnings,
        )
        # Tag vol regime from whatever vol we have.
        sigma = regime.realised_vol_30d or regime.realised_vol_90d
        if sigma is not None:
            if sigma >= 0.40:
                regime.vol_regime = "very_high"
            elif sigma >= 0.25:
                regime.vol_regime = "high"
            elif sigma <= 0.12:
                regime.vol_regime = "low"
            else:
                regime.vol_regime = "normal"
        return regime

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _emit(self, session: StructuringSession, event_type: str, message: str) -> None:
        payload = {
            "session_id": session.session_id,
            "type": event_type,
            "status": session.status.value,
            "message": message,
        }
        self.store.emit(session.session_id, payload)
        session.append_audit(
            AuditEntry(agent="Orchestrator", event=event_type, message=message),
        )

    @staticmethod
    def _budget_exceeded(session: StructuringSession) -> bool:
        ceiling = get_agent_config().cost_ceiling_usd
        return session.total_cost_usd > ceiling > 0


_GLOBAL_ORCHESTRATOR: Optional[OrchestratorAgent] = None


def get_orchestrator() -> OrchestratorAgent:
    global _GLOBAL_ORCHESTRATOR
    if _GLOBAL_ORCHESTRATOR is None:
        _GLOBAL_ORCHESTRATOR = OrchestratorAgent()
    return _GLOBAL_ORCHESTRATOR


def reset_orchestrator() -> None:
    global _GLOBAL_ORCHESTRATOR
    _GLOBAL_ORCHESTRATOR = None
