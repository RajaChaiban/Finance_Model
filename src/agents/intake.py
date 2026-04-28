"""IntakeAgent — RFQ (form or NL) → typed ClientObjective.

Two paths:
    * Structured form (production) — coerce a dict into a ClientObjective.
      No LLM. Pydantic validates.
    * Natural-language RFQ (demo) — Sonnet 4.6 extracts structured fields with
      strict JSON output. If a load-bearing field is missing, we return a
      best-effort objective and surface a clarification list; the orchestrator
      can re-prompt at Gate A.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.config.agent_config import get_agent_config

from .base import AgentError, BaseAgent
from .llm_client import get_llm_client
from .state import ClientObjective, StructuringSession

logger = logging.getLogger(__name__)


_INTAKE_SYSTEM = """You are an Intake Agent at an institutional derivatives structuring desk.

Your job is to parse an RFQ (request for quote) — written by a junior structurer in their own words about a client situation — into a strict JSON object that downstream agents will consume.

You MUST return a single JSON object matching this schema (no prose, no fences):

{
  "underlying": "<single ticker; uppercase>",
  "notional_usd": <positive number, total client exposure in USD>,
  "shares": <number or null>,
  "avg_cost": <number or null>,
  "view": "<one of: bearish | mildly_bearish | neutral | mildly_bullish | bullish | protect_gains | crash_hedge | earnings_hedge>",
  "horizon_days": <integer days, 1..1825>,
  "budget_bps_notional": <number, 0..2000; 0 means zero-cost only>,
  "premium_tolerance": "<one of: very_low | low | medium | high | zero_cost_only | credit>",
  "capped_upside_ok": <true|false>,
  "barrier_appetite": <true|false>,
  "hedge_target_loss_pct": <number 0..1 or null>,
  "constraints": [<list of short strings>],
  "clarifications_needed": [<list of short questions you would ask the junior to fill missing info>]
}

Rules:
  * If shares are not given, infer from notional_usd and current spot — but DO NOT invent spot. If you cannot infer, return null.
  * "view" is your read of the client's directional bias. "protect_gains" applies when they hold a winner and want to lock in.
  * "budget_bps_notional" of 0 means the client wants zero-cost only.
  * If the RFQ does not specify a horizon, default to 90 days.
  * Be conservative on barrier_appetite: only true if the RFQ says explicit barrier-OK words ("comfortable with knockout", "barrier hedge", etc.).
  * "clarifications_needed" should contain at most 2 entries; only ask about fields that are truly load-bearing and missing.
  * NEVER include any text outside the JSON object.
"""


class IntakeAgent(BaseAgent):
    name = "IntakeAgent"

    def _run(self, session: StructuringSession) -> StructuringSession:
        if session.intake_form:
            session.objective = self._from_form(session.intake_form, session.intake_nl)
            return session
        if session.intake_nl:
            session.objective = self._from_nl(session.intake_nl)
            return session
        raise AgentError("Intake requires either intake_form or intake_nl.")

    # ------------------------------------------------------------------
    # Form path (deterministic)
    # ------------------------------------------------------------------

    @staticmethod
    def _from_form(form: dict[str, Any], raw_rfq: str | None) -> ClientObjective:
        data = dict(form)
        if "raw_rfq" not in data and raw_rfq:
            data["raw_rfq"] = raw_rfq
        try:
            return ClientObjective(**data)
        except Exception as exc:  # noqa: BLE001 — pydantic raises a custom type
            raise AgentError(f"Form intake failed validation: {exc}") from exc

    # ------------------------------------------------------------------
    # Natural-language path (LLM)
    # ------------------------------------------------------------------

    def _from_nl(self, rfq: str) -> ClientObjective:
        cfg = get_agent_config()
        client = get_llm_client()
        result = client.complete(
            agent_name=self.name,
            model=cfg.model_intake,
            system=_INTAKE_SYSTEM,
            messages=[{"role": "user", "content": rfq.strip()}],
            json_mode=True,
            replay_key="IntakeAgent:nl",
        )

        parsed = result.parsed_json
        if not isinstance(parsed, dict):
            # Try one more time on raw text in case JSON-mode parser missed.
            try:
                parsed = json.loads(result.text)
            except (ValueError, TypeError):
                raise AgentError(
                    f"Intake LLM returned non-JSON output (model={result.model})."
                )

        clarifications = parsed.pop("clarifications_needed", []) or []
        parsed["raw_rfq"] = rfq
        if clarifications:
            parsed["clarifications"] = [str(c) for c in clarifications]

        try:
            obj = ClientObjective(**parsed)
        except Exception as exc:
            raise AgentError(f"Intake JSON failed validation: {exc}") from exc

        return obj
