"""Pydantic request / response models for the agent router.

The session itself is a Pydantic model (src/agents/state.py:StructuringSession)
that we return directly. These thin models cover input shapes and the
response envelope so the React client gets a stable contract.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class StartSessionRequest(BaseModel):
    """Either intake_form or intake_nl must be provided. Both is fine —
    intake_form takes precedence."""

    model_config = ConfigDict(extra="forbid")

    intake_form: Optional[dict[str, Any]] = None
    intake_nl: Optional[str] = Field(None, description="Free-text RFQ blob.")


class StartSessionResponse(BaseModel):
    session_id: str
    status: str
    message: str = ""


class GateDecisionRequest(BaseModel):
    """Gate A/B/C decision payload.

    Gate A `payload.edits` may contain an edited ClientObjective dict.
    Gate B `payload.swap` may contain `{candidate_id_to_drop: replacement_kind}`.
    Gate C `payload.edits` may contain free-text memo edits.
    """

    model_config = ConfigDict(extra="forbid")

    approved: bool
    payload: Optional[dict[str, Any]] = None


class SessionView(BaseModel):
    """Trimmed projection of StructuringSession for the React client.

    The full session can include large payloads in the audit log; this view
    skips the chattier fields. The /sessions/{id} endpoint can still return
    the full session by passing ?full=1 if needed.
    """

    model_config = ConfigDict(extra="ignore")

    session_id: str
    status: str
    last_error: Optional[str] = None
    objective: Optional[dict[str, Any]] = None
    regime: Optional[dict[str, Any]] = None
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    priced: list[dict[str, Any]] = Field(default_factory=list)
    scenarios: list[dict[str, Any]] = Field(default_factory=list)
    validator: Optional[dict[str, Any]] = None
    memo: Optional[dict[str, Any]] = None
    gate_a_decision: Optional[bool] = None
    gate_b_decision: Optional[bool] = None
    gate_c_decision: Optional[bool] = None
    total_cost_usd: float = 0.0
    total_tokens_input: int = 0
    total_tokens_output: int = 0
