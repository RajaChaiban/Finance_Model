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

from fastapi import APIRouter, HTTPException
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


@router.post("/sessions", response_model=StartSessionResponse)
async def start_session(req: StartSessionRequest) -> StartSessionResponse:
    if not req.intake_form and not req.intake_nl:
        raise HTTPException(
            status_code=400, detail="Provide intake_form, intake_nl, or both."
        )
    orch = get_orchestrator()
    session = await asyncio.to_thread(
        orch.start_session,
        intake_form=req.intake_form,
        intake_nl=req.intake_nl,
    )
    return StartSessionResponse(
        session_id=session.session_id,
        status=session.status.value,
        message=f"Started; current status {session.status.value}.",
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
