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
import os
import threading
from queue import Empty, SimpleQueue
from typing import Any, Optional

from src.config.agent_config import get_agent_config
from src.data import market_data
from src.data.rate_curve import RateCurve

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


def _resolve_market_intel():
    """Lazy import + resolution. Heavy deps (chromadb, sentence-transformers)
    only get pulled in when the flag is on AND import succeeds."""
    try:
        from .market_intelligence import get_market_intelligence
    except Exception as exc:  # noqa: BLE001
        logger.info("MarketIntelligence module unavailable: %s", exc)
        return None
    try:
        return get_market_intelligence()
    except Exception as exc:  # noqa: BLE001
        logger.info("MarketIntelligence resolution failed: %s", exc)
        return None


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
    """Resolve the global SessionStore.

    Default is in-memory. Production deployments set ``VOL_DESK_PERSIST=1``
    to switch to the SQLite-backed store (recommended for any environment
    where session loss on restart is unacceptable — i.e. all non-test envs).

    The SQLite path falls back to in-memory if its import fails (keeps
    minimal-deps test environments tolerant). When the SQLite store is
    selected but the import succeeds, ``VOL_DESK_DB_PATH`` (default
    ``vol_desk_sessions.db``) controls the file location.

    Test-isolation note: tests that need a fresh global store between cases
    should call ``reset_store()`` directly. The default in-memory mode keeps
    the existing test suite stable; flipping to SQLite-by-default is a one-
    line change once a test fixture writes to ``tmp_path`` and resets after
    each run.
    """
    global _GLOBAL_STORE
    if _GLOBAL_STORE is None:
        if os.getenv("VOL_DESK_PERSIST") == "1":
            try:
                from .persistence import SQLiteSessionStore
                _GLOBAL_STORE = SQLiteSessionStore(
                    db_path=os.getenv("VOL_DESK_DB_PATH", "vol_desk_sessions.db")
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "SQLite session store unavailable (%s) — falling back to in-memory.",
                    exc,
                )
                _GLOBAL_STORE = SessionStore()
        else:
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

    def __init__(
        self,
        store: Optional[SessionStore] = None,
        market_intel: Optional[Any] = None,
    ) -> None:
        self.store = store or get_store()
        # Resolve the RAG layer at construction. Caller can inject `market_intel`
        # (used by tests + FastAPI startup); otherwise we lazy-load the
        # process-wide singleton if MARKET_INTEL_ENABLED is on.
        self.market_intel = market_intel if market_intel is not None else _resolve_market_intel()
        self.intake = IntakeAgent(mi=self.market_intel)
        self.strategist = StrategistAgent(mi=self.market_intel)
        self.pricing = PricingAgent(mi=self.market_intel)
        self.scenario = ScenarioAgent(mi=self.market_intel)
        self.validator = ValidatorAgent(mi=self.market_intel)
        self.narrator = NarratorAgent(mi=self.market_intel)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def start_session(
        self,
        *,
        intake_form: Optional[dict] = None,
        intake_nl: Optional[str] = None,
    ) -> StructuringSession:
        """Create a session AND run intake synchronously.

        Kept for backwards compatibility (tests, CLI demos, anything that
        wants the parsed objective before returning). The HTTP layer should
        prefer ``create_session_shell`` + ``advance_async`` so the request
        does not block on the LLM.
        """
        session = StructuringSession(intake_form=intake_form, intake_nl=intake_nl)
        self.store.add(session)
        self._emit(session, "session_created", "Session created.")
        # Run intake immediately so the first thing the user sees at Gate A
        # is the parsed objective.
        self._safe_advance(session.session_id)
        return self.store.get(session.session_id) or session

    def create_session_shell(
        self,
        *,
        intake_form: Optional[dict] = None,
        intake_nl: Optional[str] = None,
    ) -> StructuringSession:
        """Create the session, register it in the store, but do NOT advance.

        Returns immediately so the HTTP POST can respond ``202 Accepted +
        session_id`` and the client can subscribe to the SSE event stream
        before the first agent has even started — no race where the client
        misses the ``intake_started`` event.

        Pair with ``advance_async(session_id)`` (typically scheduled as a
        FastAPI BackgroundTask) to run the actual pipeline.
        """
        session = StructuringSession(intake_form=intake_form, intake_nl=intake_nl)
        self.store.add(session)
        self._emit(session, "session_created", "Session created.")
        return session

    def advance_async(self, session_id: str) -> None:
        """Advance the session through the state machine.

        Designed to be invoked via ``fastapi.BackgroundTasks`` (or any
        worker thread). Errors are caught by ``_safe_advance`` and surfaced
        as an ``error`` event on the SSE queue + ``status=ERROR`` in the
        store; this method itself never raises so a background-task failure
        cannot crash the worker pool.
        """
        self._safe_advance(session_id)

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

        # Phase 7 — at Gate C approval, emit a hedge ticket per priced
        # candidate so the flow desk receives the opening-hedge spec without
        # polling. Failures here must NOT block the gate decision; the
        # ticket is a downstream artefact, not part of the structuring path.
        if gate == Gate.C and approved:
            try:
                self._emit_hedge_tickets(session)
            except Exception as exc:  # noqa: BLE001
                logger.warning("HedgeTicket emission failed: %s", exc)

        # Approved — advance.
        self._safe_advance(session_id)
        return self.store.get(session_id) or session

    # ------------------------------------------------------------------
    # Phase 7 — Post-Gate-C hedge-ticket emission
    # ------------------------------------------------------------------

    def _emit_hedge_tickets(self, session: StructuringSession) -> None:
        """Build a HedgeTicket for the recommended candidate (and any others
        the desk wants visibility on) and stamp it onto session.hedge_tickets,
        then emit a `hedge_ticket` event so the SSE stream surfaces it."""
        from datetime import date, timedelta
        from .hedge_ticket import build_hedge_ticket
        from .state import HedgeTicketState

        if session.regime is None or not session.priced or session.objective is None:
            return

        for pc in session.priced:
            try:
                # Use the first leg's expiry as the structure expiry approximation.
                first_leg = pc.candidate.legs[0]
                exp_date = (date.today() + timedelta(days=int(first_leg.expiry_days))).isoformat()
                ticket = build_hedge_ticket(
                    candidate_id=pc.candidate.candidate_id,
                    structure_name=pc.candidate.name,
                    notional_usd=pc.candidate.notional_usd,
                    delta_per_share=pc.greeks.delta,
                    gamma_per_share=pc.greeks.gamma,
                    vega_per_share=pc.greeks.vega,
                    spot=session.regime.spot,
                    sigma=(session.regime.atm_iv or session.regime.realised_vol_30d or 0.20),
                    underlier=session.objective.underlying,
                    expiry_iso=exp_date,
                )
                state_obj = HedgeTicketState(**ticket.to_dict())
                session.hedge_tickets.append(state_obj)
                self._emit(session, "hedge_ticket", state_obj.model_dump())
            except Exception as exc:  # noqa: BLE001
                logger.warning("HedgeTicket build failed for %s: %s", pc.candidate.name, exc)
        self.store.update(session)

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
                session = self._run_agent(self.intake, session)
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
                    # Smile-aware pricing path (opt-in, default OFF). Builds a
                    # QuantLib ``BlackVarianceSurface`` from the live option
                    # chain and stows the handle on ``session.vol_handle``;
                    # PricingAgent then forwards it to the router. On any
                    # failure we log and fall through — pricing keeps the
                    # scalar-σ path so the session still completes.
                    if session.use_vol_surface:
                        self._maybe_build_vol_surface(session)
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
                session = self._run_agent(self.strategist, session)
                session.status = SessionStatus.AWAITING_GATE_B
                self.store.update(session)
                self._emit(session, "agent_finished", "StrategistAgent")
                self._emit(session, "gate_pending", Gate.B.value)
                return

            if status == SessionStatus.PENDING_PRICING:
                self._emit(session, "agent_started", "PricingAgent")
                session = self._run_agent(self.pricing, session)
                session.status = SessionStatus.PENDING_SCENARIO
                self.store.update(session)
                self._emit(session, "agent_finished", "PricingAgent")
                continue

            if status == SessionStatus.PENDING_SCENARIO:
                self._emit(session, "agent_started", "ScenarioAgent")
                session = self._run_agent(self.scenario, session)
                session.status = SessionStatus.PENDING_VALIDATION
                self.store.update(session)
                self._emit(session, "agent_finished", "ScenarioAgent")
                continue

            if status == SessionStatus.PENDING_VALIDATION:
                self._emit(session, "agent_started", "ValidatorAgent")
                session = self._run_agent(self.validator, session)
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
                session = self._run_agent(self.narrator, session)
                session.status = SessionStatus.AWAITING_GATE_C
                self.store.update(session)
                self._emit(session, "agent_finished", "NarratorAgent")
                self._emit(session, "gate_pending", Gate.C.value)
                return

            # Unknown / terminal.
            return

    # ------------------------------------------------------------------
    # Agent runner — wraps every agent.run() call so we can emit any new
    # `market_context` entries the agent appended to the session.
    # ------------------------------------------------------------------

    def _run_agent(self, agent: Any, session: StructuringSession) -> StructuringSession:
        prev_len = len(session.market_context or [])
        session = agent.run(session)
        self._emit_new_market_context(session, prev_len)
        return session

    def _emit_new_market_context(
        self, session: StructuringSession, prev_len: int
    ) -> None:
        ctx = session.market_context or []
        for entry in ctx[prev_len:]:
            self.store.emit(
                session.session_id,
                {
                    "session_id": session.session_id,
                    "type": "market_context",
                    "status": session.status.value,
                    "agent": entry.get("agent"),
                    "intent": entry.get("intent"),
                    "payload": entry,
                },
            )

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

        curve = RateCurve.from_env()
        maturity_years = (objective.horizon_days / 365.0) if objective.horizon_days else 1.0
        rfr = curve.spot_rate(maturity_years=maturity_years)

        regime = MarketRegime(
            underlying=objective.underlying,
            spot=float(spot),
            dividend_yield=float(params.get("dividend_yield") or 0.015),
            risk_free_rate=float(rfr),
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
    # Smile-aware surface build (opt-in). Mirrors src/api/handlers.py:88-112.
    # ------------------------------------------------------------------

    @staticmethod
    def _maybe_build_vol_surface(session: StructuringSession) -> None:
        """Best-effort: build a live IV surface and attach the handle.

        Mirrors the pattern in ``src/api/handlers.py:88-112``. Skips
        gracefully when the regime is a placeholder fallback (no real spot
        was fetched), when the option chain is empty, or when any layer of
        the surface stack raises — in every failure mode we log a warning
        and leave ``session.vol_handle`` unset, so PricingAgent falls
        through to the scalar-σ path.
        """
        regime = session.regime
        if regime is None:
            return
        # Detect the orchestrator's $100 placeholder spot. The regime build
        # appends "Spot price missing; using $100 placeholder." when yfinance
        # came back empty; in that case there's no point hitting Yahoo for
        # an option chain it doesn't have either.
        if any("placeholder" in w.lower() for w in regime.data_source_warnings):
            logger.info(
                "Skipping vol-surface build for %s: regime is placeholder.",
                regime.underlying,
            )
            return
        try:
            import QuantLib as ql
            from src.api.market_data import fetch_option_chain
            from src.data.iv_grid import build_iv_grid
            from src.data.vol_surface import build_vol_surface

            logger.info("Building live IV surface for %s...", regime.underlying)
            chain = fetch_option_chain(regime.underlying, max_expiries=6)
            if not chain:
                logger.warning(
                    "Empty option chain for %s; falling back to scalar σ.",
                    regime.underlying,
                )
                return
            grid = build_iv_grid(
                chain,
                S=regime.spot,
                r=regime.risk_free_rate,
                q=regime.dividend_yield,
                min_success_rate=0.4,
            )
            surface = build_vol_surface(grid)
            session.vol_handle = ql.BlackVolTermStructureHandle(surface)
            logger.info(
                "Surface built for %s: %d/%d quotes inverted.",
                regime.underlying,
                grid.n_quotes_inverted,
                grid.n_quotes_total,
            )
        except Exception as exc:  # noqa: BLE001 — surface build is best-effort
            logger.warning(
                "Surface build failed for %s (%s); falling back to scalar σ.",
                regime.underlying,
                exc,
            )
            session.vol_handle = None

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

    def _budget_exceeded(self, session: StructuringSession) -> bool:
        cfg = get_agent_config()
        # Per-session ceiling (existing behaviour).
        if 0 < cfg.cost_ceiling_usd < session.total_cost_usd:
            return True
        # Phase 7 — tenant-level ceiling. Only checked when explicitly set
        # (positive value); 0.0 disables the cap so existing tests don't
        # change behaviour.
        if cfg.tenant_cost_ceiling_usd > 0:
            try:
                tenant_total = sum(
                    (self.store.get(sid).total_cost_usd or 0.0)
                    for sid in self.store.list_ids()
                    if self.store.get(sid) is not None
                )
            except Exception:  # noqa: BLE001 — defensive; never break the loop
                tenant_total = session.total_cost_usd
            if tenant_total > cfg.tenant_cost_ceiling_usd:
                return True
        return False


_GLOBAL_ORCHESTRATOR: Optional[OrchestratorAgent] = None


def get_orchestrator() -> OrchestratorAgent:
    global _GLOBAL_ORCHESTRATOR
    if _GLOBAL_ORCHESTRATOR is None:
        _GLOBAL_ORCHESTRATOR = OrchestratorAgent()
    return _GLOBAL_ORCHESTRATOR


def reset_orchestrator() -> None:
    global _GLOBAL_ORCHESTRATOR
    _GLOBAL_ORCHESTRATOR = None
