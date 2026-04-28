"""BaseAgent — common contract for every agent in the pipeline.

Every agent:
  * Has a `name` (string identifier shown in the audit log).
  * Has a `run(session) -> session` method that consumes and produces a
    StructuringSession.
  * Auto-emits enter/exit AuditEntries via `_run_with_audit`.
  * Surfaces errors as `error` audit events and sets session.last_error;
    the orchestrator decides whether to abort or recover.

Subclasses override `_run`, not `run`. That ensures audit logging and error
handling are uniform across the pipeline.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Optional

from .state import AuditEntry, StructuringSession

logger = logging.getLogger(__name__)


class AgentError(RuntimeError):
    """Raised by agents to signal a recoverable failure that the orchestrator
    should surface to the human (or retry, depending on the agent)."""


class BaseAgent(ABC):
    """Abstract base. Subclasses implement `_run`."""

    name: str = "BaseAgent"

    def run(self, session: StructuringSession) -> StructuringSession:
        return self._run_with_audit(session)

    @abstractmethod
    def _run(self, session: StructuringSession) -> StructuringSession:
        """Subclass-specific work. Mutate and return the session."""

    # ------------------------------------------------------------------
    # Audit wrapping
    # ------------------------------------------------------------------

    def _run_with_audit(self, session: StructuringSession) -> StructuringSession:
        session.append_audit(
            AuditEntry(agent=self.name, event="enter", message=f"{self.name} starting"),
        )
        start = time.time()
        try:
            session = self._run(session)
        except AgentError as exc:
            duration = time.time() - start
            logger.warning("%s soft failure: %s", self.name, exc)
            session.last_error = f"{self.name}: {exc}"
            session.append_audit(
                AuditEntry(
                    agent=self.name,
                    event="error",
                    message=str(exc),
                    duration_s=duration,
                ),
            )
            raise
        except Exception as exc:  # noqa: BLE001 — agent boundary
            duration = time.time() - start
            logger.exception("%s hard failure: %s", self.name, exc)
            session.last_error = f"{self.name}: {exc}"
            session.append_audit(
                AuditEntry(
                    agent=self.name,
                    event="error",
                    message=f"unhandled: {exc}",
                    duration_s=duration,
                ),
            )
            raise

        duration = time.time() - start
        session.append_audit(
            AuditEntry(
                agent=self.name,
                event="exit",
                message=f"{self.name} done",
                duration_s=duration,
            ),
        )
        return session

    # ------------------------------------------------------------------
    # Helpers for subclasses
    # ------------------------------------------------------------------

    def _record_llm_usage(
        self,
        session: StructuringSession,
        *,
        message: str,
        tokens_input: int,
        tokens_output: int,
        tokens_cache_read: int = 0,
        tokens_cache_create: int = 0,
        cost_usd: float = 0.0,
        latency_s: Optional[float] = None,
        payload: Optional[dict] = None,
    ) -> None:
        session.append_audit(
            AuditEntry(
                agent=self.name,
                event="llm_call",
                message=message,
                duration_s=latency_s,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                tokens_cache_read=tokens_cache_read,
                tokens_cache_create=tokens_cache_create,
                cost_usd=cost_usd,
                payload=payload,
            ),
        )
