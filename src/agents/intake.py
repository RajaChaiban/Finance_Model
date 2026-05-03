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
from typing import Any, Optional

from pydantic import ValidationError

from src.config.agent_config import get_agent_config

from .base import AgentError, BaseAgent
from .llm_client import get_llm_client
from .prompts import load_prompt
from .state import ClientObjective, StructuringSession

logger = logging.getLogger(__name__)


def _summarize_validation_error(exc: Exception, kind: str) -> str:
    """Turn a pydantic ValidationError into a single human-readable line.

    The default ``str(ValidationError)`` is multi-line, includes a pydantic.dev
    URL per error, and embeds raw input values — fine for a server log,
    hostile in a UI banner. The structurer needs to know "what's missing"
    in plain English so they can re-prompt.

    Returns a single-line summary listing the first three offending fields
    by location + message, with a "(+N more)" suffix if truncated.
    """
    if isinstance(exc, ValidationError):
        errs = exc.errors()
        bits = []
        for e in errs[:3]:
            loc = ".".join(str(p) for p in e.get("loc", ())) or "(root)"
            msg = e.get("msg", "invalid value")
            bits.append(f"{loc}: {msg}")
        joined = "; ".join(bits)
        more = f" (+{len(errs) - 3} more)" if len(errs) > 3 else ""
        return f"Could not parse {kind} — please check: {joined}{more}"
    return f"Could not parse {kind}: {exc}"


_INTAKE_SYSTEM = load_prompt("intake/system.md")


class IntakeAgent(BaseAgent):
    name = "IntakeAgent"

    def __init__(self, mi: Optional[Any] = None) -> None:
        # `mi` is `Optional[MarketIntelligence]`; typed as Any so importing
        # this module doesn't pull the heavy chromadb/sentence-transformers
        # stack until MI is actually used.
        self.mi = mi

    def _run(self, session: StructuringSession) -> StructuringSession:
        if session.intake_form:
            session.objective = self._from_form(session.intake_form, session.intake_nl)
        elif session.intake_nl:
            session.objective = self._from_nl(session.intake_nl)
        else:
            raise AgentError("Intake requires either intake_form or intake_nl.")

        # Enrich with grounded market context so Gate A reflects what the
        # corpus knows about this underlying *before* the strategist runs.
        self._enrich_with_market_context(session)
        return session

    # ------------------------------------------------------------------
    # Market intelligence (free-form Q&A on the RFQ + ticker)
    # ------------------------------------------------------------------

    def _enrich_with_market_context(self, session: StructuringSession) -> None:
        if self.mi is None or session.objective is None:
            return
        obj = session.objective
        rfq = (obj.raw_rfq or session.intake_nl or "").strip()
        # Compose a query that gives semantic search something to bite on.
        query = (
            f"{obj.underlying} {obj.view} {obj.horizon_days}d horizon. "
            f"{rfq}"
        ).strip()[:1000]
        try:
            qr = self.mi.general_query(query=query, asset_class=obj.underlying)
        except Exception as exc:  # noqa: BLE001 — never fail intake on MI errors
            logger.warning("Intake MI general_query failed: %s", exc)
            return
        self._record_market_context(session, intent="general", qr=qr)

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
        except ValidationError as exc:
            # Sanitize: the raw pydantic dump is a 1-2 KB blob that the FE
            # banner displays verbatim. Summarize to a single line listing
            # the first 3 missing/bad fields.
            raise AgentError(_summarize_validation_error(exc, "form intake")) from exc
        except Exception as exc:  # noqa: BLE001 — non-validation surprises
            raise AgentError(f"Form intake failed: {exc}") from exc

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
        except ValidationError as exc:
            # Same sanitization as the form path — the LLM occasionally
            # returns JSON that pydantic rejects (wrong types on an enum
            # field, missing required key). Show the structurer the first
            # 3 issues, not the full pydantic.dev URL dump.
            raise AgentError(_summarize_validation_error(exc, "natural-language RFQ")) from exc
        except Exception as exc:  # noqa: BLE001
            raise AgentError(f"Natural-language RFQ failed: {exc}") from exc

        return obj
