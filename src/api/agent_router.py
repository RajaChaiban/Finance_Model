"""FastAPI router for the agent layer.

Endpoints
---------
POST   /api/agent/sessions                 — start session, run Intake, return session
GET    /api/agent/sessions/{id}            — current session state
POST   /api/agent/sessions/{id}/gate/{a|b|c} — gate decision; advance pipeline
GET    /api/agent/sessions/{id}/events     — SSE stream of state-transition events

The orchestrator runs synchronously inside the request handler (inside a
threadpool) — Phase 1 sessions complete in well under a request-timeout's
worth of time. Phase 6 will add background workers + SQLite.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse

try:
    from sse_starlette.sse import EventSourceResponse
    _SSE_AVAILABLE = True
except ImportError:  # pragma: no cover — sse-starlette in requirements.txt
    EventSourceResponse = None
    _SSE_AVAILABLE = False

from src.agents.orchestrator import get_orchestrator, get_store
from src.agents.state import Gate, SessionStatus, StructuringSession

from .agent_models import (
    GateDecisionRequest,
    SessionView,
    StartSessionRequest,
    StartSessionResponse,
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/agent", tags=["agent"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_view(session: StructuringSession) -> SessionView:
    raw = session.model_dump(mode="json")
    return SessionView(**raw)


def _gate_for_path(letter: str) -> Gate:
    letter = letter.lower()
    if letter == "a":
        return Gate.A
    if letter == "b":
        return Gate.B
    if letter == "c":
        return Gate.C
    raise HTTPException(status_code=400, detail=f"Unknown gate '{letter}'.")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/sessions", response_model=StartSessionResponse, status_code=202)
async def start_session(
    req: StartSessionRequest, background_tasks: BackgroundTasks
) -> StartSessionResponse:
    """Create the session shell synchronously, kick off the pipeline in a
    background task, and return ``202 Accepted + session_id`` immediately.

    The previous behaviour ran intake (an LLM call) inside the request
    handler, blocking the POST for 30-60s. The frontend's "Run" button
    appeared frozen while the user waited, often triggering refresh
    storms. Now the POST returns in <100ms and the client subscribes to
    ``GET /sessions/{id}/events`` (SSE) for real-time progress.
    """
    if not req.intake_form and not req.intake_nl:
        raise HTTPException(
            status_code=400, detail="Provide intake_form, intake_nl, or both."
        )
    orch = get_orchestrator()
    # Create the session shell synchronously so we can return its id; the
    # store has it before we exit, so a fast SSE subscription cannot race
    # against the first ``agent_started`` event (the queue buffers).
    session = orch.create_session_shell(
        intake_form=req.intake_form,
        intake_nl=req.intake_nl,
    )
    # Schedule the heavy work (intake → regime → strategist) to run after
    # the response is sent. BackgroundTasks runs on the same event loop's
    # thread pool, so the orchestrator's ``_safe_advance`` (which is sync
    # blocking work) is wrapped in ``asyncio.to_thread`` to avoid blocking
    # the loop.
    async def _kickoff() -> None:
        await asyncio.to_thread(orch.advance_async, session.session_id)
    background_tasks.add_task(_kickoff)
    return StartSessionResponse(
        session_id=session.session_id,
        status=session.status.value,
        message=(
            f"Session created (status={session.status.value}); "
            f"intake running in background — subscribe to "
            f"/api/agent/sessions/{session.session_id}/events for progress."
        ),
    )


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, full: bool = False) -> Any:
    store = get_store()
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"No session {session_id}")
    if full:
        # Full state for debugging / replay export.
        return JSONResponse(content=session.model_dump(mode="json"))
    return JSONResponse(content=_to_view(session).model_dump(mode="json"))


@router.post("/sessions/{session_id}/gate/{letter}")
async def decide_gate(
    session_id: str, letter: str, req: GateDecisionRequest
) -> Any:
    gate = _gate_for_path(letter)
    orch = get_orchestrator()
    session = await asyncio.to_thread(
        orch.decide_gate,
        session_id,
        gate,
        approved=req.approved,
        payload=req.payload,
    )
    return JSONResponse(content=_to_view(session).model_dump(mode="json"))


@router.get("/sessions/{session_id}/events")
async def stream_events(session_id: str):
    """SSE stream. Emits events the orchestrator pushed onto the session
    queue, plus periodic heartbeats. Closes when status is terminal or
    awaiting a gate (the client can re-subscribe after deciding the gate).
    """
    if not _SSE_AVAILABLE:
        raise HTTPException(
            status_code=500,
            detail="sse-starlette not installed; cannot stream events.",
        )

    store = get_store()
    if store.get(session_id) is None:
        raise HTTPException(status_code=404, detail=f"No session {session_id}")

    async def gen() -> AsyncGenerator[dict[str, str], None]:
        last_status: str | None = None
        idle_ticks = 0
        while True:
            event = await asyncio.to_thread(store.drain, session_id, 0.0)
            if event is not None:
                yield {"event": event.get("type", "message"), "data": json.dumps(event)}
                idle_ticks = 0
            else:
                # Heartbeat every 5 idle ticks (~5s).
                idle_ticks += 1
                if idle_ticks >= 5:
                    idle_ticks = 0
                    yield {"event": "heartbeat", "data": "{}"}

            sess = store.get(session_id)
            if sess is None:
                break
            if sess.status.value != last_status:
                last_status = sess.status.value
            terminal = {
                SessionStatus.DONE.value,
                SessionStatus.ERROR.value,
                SessionStatus.CANCELLED.value,
            }
            awaiting = {
                SessionStatus.AWAITING_GATE_A.value,
                SessionStatus.AWAITING_GATE_B.value,
                SessionStatus.AWAITING_GATE_C.value,
            }
            if sess.status.value in terminal:
                yield {"event": "stream_close", "data": json.dumps({"reason": "terminal"})}
                break
            if sess.status.value in awaiting:
                # Hold the stream open briefly so the client sees the gate event,
                # then close so the client re-subscribes after deciding.
                await asyncio.sleep(0.5)
                yield {"event": "stream_close", "data": json.dumps({"reason": "gate"})}
                break
            await asyncio.sleep(1.0)

    return EventSourceResponse(gen())


# ---------------------------------------------------------------------------
# Phase 7 — Senior-structurer endpoints: termsheet, KID, book, lifecycle
# ---------------------------------------------------------------------------


@router.get("/sessions/{session_id}/termsheet")
async def get_termsheet(session_id: str, candidate_id: str | None = None) -> Any:
    """Generate the PRIIPs-style termsheet PDF for the session's recommended
    candidate (or a specific candidate if ``candidate_id`` is supplied).

    Returns the PDF inline. Generation is synchronous because the underlying
    reportlab call is fast (<200ms typical).
    """
    import io
    import os
    import tempfile
    from fastapi.responses import FileResponse

    from src.report.term_sheet import generate_term_sheet
    from src.agents.state import (
        Structure as StructureModel,
        StructureLeg as StructureLegModel,
        AutocallTerms,
        ObservationSchedule,
    )

    store = get_store()
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"No session {session_id}")
    if not session.priced:
        raise HTTPException(status_code=400, detail="Session has no priced candidates.")

    # Pick the candidate.
    target = None
    if candidate_id:
        target = next((p for p in session.priced if p.candidate.candidate_id == candidate_id), None)
    elif session.memo and session.memo.recommended_candidate_id:
        target = next(
            (p for p in session.priced
             if p.candidate.candidate_id == session.memo.recommended_candidate_id),
            None,
        )
    target = target or session.priced[0]

    # Build a minimal Structure from the agent-pipeline Candidate (the term
    # sheet generator was built against the multi-asset Structure schema; we
    # adapt the single-asset legs into StructureLeg entries).
    structure_legs = []
    for leg in target.candidate.legs:
        structure_legs.append(StructureLegModel(
            side="long" if leg.quantity > 0 else "short",
            quantity=abs(float(leg.quantity)),
            instrument_kind=leg.option_type if leg.option_type in {
                "european_call", "european_put", "knockout_call", "knockout_put",
                "knockin_call", "knockin_put", "asian_call", "asian_put",
                "lookback_call", "lookback_put",
            } else "european_call",   # safe fallback
            strike=float(leg.strike),
            barrier=float(leg.barrier_level) if leg.barrier_level is not None else None,
        ))
    maturity_years = (
        max((leg.expiry_days for leg in target.candidate.legs), default=365) / 365.0
    )
    structure = StructureModel(
        name=target.candidate.name,
        legs=structure_legs,
        maturity_years=maturity_years,
        notional=float(target.candidate.notional_usd),
    )

    # Indicative scenarios — flat moves around 1.0 (no MC for this stub).
    scenarios = {
        "favourable": 1.30,
        "moderate": 1.05,
        "unfavourable": 0.75,
        "stress": 0.50,
    }

    # Write to a temp file and stream the response.
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.close()
    try:
        out_path = generate_term_sheet(
            structure=structure,
            mid_price=float(target.net_premium),
            scenarios=scenarios,
            output_path=tmp.name,
        )
        return FileResponse(
            out_path,
            media_type="application/pdf",
            filename=f"termsheet_{session_id[:8]}.pdf",
        )
    except Exception as exc:  # noqa: BLE001
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail=f"Termsheet generation failed: {exc}") from exc


@router.get("/sessions/{session_id}/kid")
async def get_kid(session_id: str, candidate_id: str | None = None) -> Any:
    """Return a JSON KID payload (SRI bucket, cost table, scenarios) for the
    session's recommended candidate. v1 — JSON only; PDF rendering is
    plumbed through the existing termsheet endpoint."""
    from src.report.kid import build_kid

    store = get_store()
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"No session {session_id}")
    if not session.priced or session.regime is None:
        raise HTTPException(status_code=400, detail="Session not priced.")

    target = None
    if candidate_id:
        target = next((p for p in session.priced if p.candidate.candidate_id == candidate_id), None)
    elif session.memo and session.memo.recommended_candidate_id:
        target = next(
            (p for p in session.priced
             if p.candidate.candidate_id == session.memo.recommended_candidate_id),
            None,
        )
    target = target or session.priced[0]

    rhp = max((leg.expiry_days for leg in target.candidate.legs), default=365) / 365.0
    sigma = (session.regime.atm_iv or session.regime.realised_vol_30d or 0.20)
    kid = build_kid(
        product_name=target.candidate.name,
        notional=float(target.candidate.notional_usd),
        rhp_years=rhp,
        annualised_vol=float(sigma),
    )
    return JSONResponse(content=kid.to_dict())


@router.get("/sessions/{session_id}/hedge_tickets")
async def get_hedge_tickets(session_id: str) -> Any:
    """Return all hedge tickets emitted at Gate C approval for this session."""
    store = get_store()
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"No session {session_id}")
    return JSONResponse(content=[t.model_dump() for t in session.hedge_tickets])


@router.get("/book")
async def get_book(name: str = "default-book") -> Any:
    """Aggregate all sessions in the store into a book view."""
    from src.agents.book import aggregate_book

    store = get_store()
    ids = store.list_ids()
    sessions = [store.get(sid) for sid in ids]
    sessions = [s for s in sessions if s is not None]
    summary = aggregate_book(sessions=sessions, name=name)
    return JSONResponse(content=summary.to_dict())


@router.post("/sessions/{session_id}/lifecycle")
async def assess_lifecycle(session_id: str, payload: dict[str, Any]) -> Any:
    """Re-mark a prior session against today's regime.

    Body: {"current_regime": {"spot": ..., "realised_vol_30d": ..., ...}}
    Returns: LifecycleAssessment for the session's recommended candidate.
    """
    from src.agents.lifecycle import LifecycleAgent
    from src.agents.state import MarketRegime

    store = get_store()
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"No session {session_id}")
    if not session.priced or session.regime is None:
        raise HTTPException(status_code=400, detail="Session not priced.")

    target = next(
        (p for p in session.priced
         if session.memo and p.candidate.candidate_id == session.memo.recommended_candidate_id),
        session.priced[0],
    )
    try:
        current_regime = MarketRegime(**(payload.get("current_regime") or {}))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Invalid current_regime: {exc}") from exc

    agent = LifecycleAgent()
    assessment = agent.assess(
        prior=target,
        prior_regime=session.regime,
        current_regime=current_regime,
    )
    return JSONResponse(content=assessment.to_dict())
