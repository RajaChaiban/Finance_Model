"""Thin wrapper around the Google Gen AI SDK (Gemini).

Responsibilities:
  * Model-tier routing (gemini-2.5-pro / flash / flash-lite)
  * Retry with exponential backoff on transient errors
  * JSON-mode via response_mime_type="application/json"
  * Cost/token bookkeeping returned to the caller
  * DEMO_REPLAY=1 mock mode that reads canned responses from a JSON fixture

This module is the *only* place that imports `google.genai`. Agents call into
LLMClient and never touch the SDK directly. That keeps mocking trivial.

Note on caching: Gemini supports server-side context caching via a separate
`client.caches.create()` call. We don't use it in Phase 1 — sessions are short
and prompts modest. Phase 5 (production polish) can wire it in for the
Strategist's rules-table system prompt to cut cost.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from src.config.agent_config import AgentConfig, get_agent_config

logger = logging.getLogger(__name__)


# Approximate per-MTok costs (input / output) in USD for prompts ≤ 200K tokens.
# Source: Google AI for Developers pricing, late 2025. Update when Google
# changes pricing. Cached-input pricing not used in Phase 1.
_PRICING = {
    "gemini-2.5-pro":         {"in": 1.25, "out": 10.00},
    "gemini-2.5-flash":       {"in": 0.30, "out": 2.50},
    "gemini-2.5-flash-lite":  {"in": 0.10, "out": 0.40},
}


@dataclass
class LLMResult:
    """One round-trip's worth of stuff the agent and the audit log need."""

    text: str = ""
    parsed_json: Any = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""
    model: str = ""

    tokens_input: int = 0
    tokens_output: int = 0
    tokens_cache_read: int = 0
    tokens_cache_create: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0


class LLMUnavailableError(RuntimeError):
    """Raised when the SDK isn't installed or no API key is configured AND
    DEMO_REPLAY is not enabled."""


class LLMClient:
    """One client per process is fine. The Google SDK is thread-safe."""

    def __init__(self, cfg: Optional[AgentConfig] = None) -> None:
        self.cfg = cfg or get_agent_config()
        self._genai = None
        self._client = None
        self._types = None
        self._replay_cache: Optional[dict[str, Any]] = None

        if self.cfg.demo_replay:
            self._load_replay_cache()
            return

        if not self.cfg.gemini_api_key:
            logger.warning(
                "GEMINI_API_KEY not set. LLM calls will raise unless DEMO_REPLAY=1."
            )
            return

        try:
            from google import genai  # type: ignore[import-not-found]
            from google.genai import types as genai_types  # type: ignore[import-not-found]

            self._genai = genai
            self._types = genai_types
            self._client = genai.Client(api_key=self.cfg.gemini_api_key)
        except ImportError:
            logger.warning("google-genai SDK not installed. pip install google-genai")

    # ------------------------------------------------------------------
    # Replay mode
    # ------------------------------------------------------------------

    def _load_replay_cache(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        path = repo_root / "tests" / "fixtures" / "demo_replay.json"
        if not path.exists():
            logger.warning(
                "DEMO_REPLAY=1 but %s missing; LLM calls will return empty stubs.",
                path,
            )
            self._replay_cache = {}
            return
        with path.open("r", encoding="utf-8") as fh:
            self._replay_cache = json.load(fh)

    def _replay(self, key: str) -> LLMResult:
        if self._replay_cache is None:
            self._replay_cache = {}
        entry = self._replay_cache.get(key)
        if entry is None:
            logger.debug("DEMO_REPLAY missing key %s; returning empty result.", key)
            return LLMResult(text="", model="replay")
        text = entry.get("text", "")
        parsed = entry.get("parsed_json")
        if parsed is None and text:
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError):
                parsed = None
        return LLMResult(
            text=text,
            parsed_json=parsed,
            tool_calls=entry.get("tool_calls", []),
            stop_reason=entry.get("stop_reason", "STOP"),
            model="replay",
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def complete(
        self,
        *,
        agent_name: str,
        model: str,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        max_tokens: Optional[int] = None,
        cache_system: bool = True,  # accepted for API parity, no-op in Phase 1
        json_mode: bool = False,
        replay_key: Optional[str] = None,
    ) -> LLMResult:
        """Single round-trip to the Gemini API.

        Args:
            agent_name: For logging/audit. e.g. "StrategistAgent".
            model: One of MODEL_PRO / MODEL_FLASH / MODEL_FLASH_LITE.
            system: System instruction. Accepts a string or an Anthropic-style
                list of content blocks (we extract their `text` fields and
                concatenate, for source-parity with prior code).
            messages: Anthropic-style turn list. We flatten user content to a
                single string (Gemini's API supports multi-turn but Phase 1
                always sends one user message).
            tools: Currently ignored; tool-use lands in Phase 2.
            max_tokens: Override default.
            cache_system: Accepted for API parity; Phase 1 doesn't use server
                caching. Phase 5 will swap to `client.caches`.
            json_mode: If True, sets `response_mime_type='application/json'`.
            replay_key: Key into demo_replay.json. If unset, agents pick one.
        """
        if self.cfg.demo_replay:
            key = replay_key or f"{agent_name}:default"
            res = self._replay(key)
            logger.debug("DEMO_REPLAY %s -> %s", key, "hit" if res.text else "miss")
            return res

        if self._client is None or self._types is None:
            raise LLMUnavailableError(
                f"{agent_name} cannot run: google-genai SDK missing or "
                f"GEMINI_API_KEY not set, and DEMO_REPLAY is not enabled."
            )

        system_text = self._coerce_system(system)
        user_text = self._coerce_messages(messages)
        max_tokens = max_tokens or self.cfg.max_output_tokens

        config_kwargs: dict[str, Any] = {
            "max_output_tokens": max_tokens,
            "system_instruction": system_text or None,
        }
        if json_mode:
            config_kwargs["response_mime_type"] = "application/json"

        config = self._types.GenerateContentConfig(
            **{k: v for k, v in config_kwargs.items() if v is not None}
        )

        last_err: Optional[Exception] = None
        for attempt in range(self.cfg.max_retries + 1):
            try:
                start = time.time()
                response = self._client.models.generate_content(
                    model=model,
                    contents=user_text,
                    config=config,
                )
                latency = time.time() - start
                return self._build_result(response, model, latency, json_mode)

            except Exception as exc:  # noqa: BLE001 — SDK exception classes vary
                last_err = exc
                if attempt >= self.cfg.max_retries:
                    break
                wait = 2 ** attempt
                logger.warning(
                    "%s LLM call failed (attempt %d/%d): %s. Retrying in %ds.",
                    agent_name,
                    attempt + 1,
                    self.cfg.max_retries + 1,
                    exc,
                    wait,
                )
                time.sleep(wait)

        raise RuntimeError(
            f"{agent_name} LLM call exhausted retries: {last_err}"
        ) from last_err

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce_system(system: str | list[dict[str, Any]]) -> str:
        if isinstance(system, str):
            return system
        # Accept Anthropic-style block list: [{"type":"text","text":"..."}].
        parts = []
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n\n".join(parts)

    @staticmethod
    def _coerce_messages(messages: list[dict[str, Any]]) -> str:
        # Phase 1 always sends a single user message. Concatenate any user
        # content. Roles other than 'user' are ignored — agents put system
        # instructions in `system`, not `messages`.
        parts = []
        for m in messages:
            role = m.get("role", "user")
            if role != "user":
                continue
            content = m.get("content", "")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and "text" in block:
                        parts.append(str(block["text"]))
            else:
                parts.append(str(content))
        return "\n\n".join(parts)

    def _build_result(
        self, response: Any, model: str, latency: float, json_mode: bool
    ) -> LLMResult:
        text = getattr(response, "text", "") or ""

        # Stop reason: from the first candidate's finish_reason if available.
        stop_reason = ""
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            fr = getattr(candidates[0], "finish_reason", None)
            stop_reason = str(fr) if fr is not None else ""

        usage = getattr(response, "usage_metadata", None)
        tin = int(getattr(usage, "prompt_token_count", 0) or 0) if usage else 0
        tout = int(getattr(usage, "candidates_token_count", 0) or 0) if usage else 0
        tcr = int(getattr(usage, "cached_content_token_count", 0) or 0) if usage else 0

        cost = self._estimate_cost(model, tin, tout)

        parsed = None
        if json_mode and text:
            parsed = self._safe_json(text)

        return LLMResult(
            text=text,
            parsed_json=parsed,
            tool_calls=[],
            stop_reason=stop_reason,
            model=model,
            tokens_input=tin,
            tokens_output=tout,
            tokens_cache_read=tcr,
            tokens_cache_create=0,
            cost_usd=cost,
            latency_s=latency,
        )

    @staticmethod
    def _estimate_cost(model: str, tin: int, tout: int) -> float:
        prices = _PRICING.get(model)
        if not prices:
            return 0.0
        return (tin / 1_000_000) * prices["in"] + (tout / 1_000_000) * prices["out"]

    @staticmethod
    def _safe_json(text: str) -> Any:
        """Tolerate ```json fenced blocks and stray prose around the JSON."""
        candidate = text.strip()
        if candidate.startswith("```"):
            lines = candidate.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            candidate = "\n".join(lines)
        try:
            return json.loads(candidate)
        except ValueError:
            for opener in "{[":
                idx = candidate.find(opener)
                if idx >= 0:
                    try:
                        return json.loads(candidate[idx:])
                    except ValueError:
                        continue
            return None


_GLOBAL_CLIENT: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _GLOBAL_CLIENT
    if _GLOBAL_CLIENT is None:
        _GLOBAL_CLIENT = LLMClient()
    return _GLOBAL_CLIENT


def reset_llm_client() -> None:
    """For tests."""
    global _GLOBAL_CLIENT
    _GLOBAL_CLIENT = None


def is_demo_replay() -> bool:
    return os.getenv("DEMO_REPLAY", "0").strip() in {"1", "true", "True"}
