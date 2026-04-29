"""LLM provider abstraction so we can swap Gemini -> Anthropic -> OpenAI -> Bedrock.

Each provider exposes a single `complete(prompt, *, model, temperature, max_tokens)`
method. Selection happens once at startup via the LLM_PROVIDER env var.

SDKs are imported lazily (inside __init__) so that importing this module does
not crash when only some SDKs are installed.

Replay / DEMO_REPLAY handling:
  get_provider() returns MockProvider when DEMO_REPLAY=1.
  However, the full replay logic (fixture-backed canned responses keyed by
  agent_name) lives inside LLMClient.complete(). MockProvider is only used by
  tests that call get_provider() directly and by any future non-LLMClient usage.
"""
from __future__ import annotations

import os
from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def complete(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> str:
        ...


# ---------------------------------------------------------------------------
# Mock provider — deterministic, no SDK required
# ---------------------------------------------------------------------------

class MockProvider:
    name = "mock"

    def __init__(self, canned: Optional[dict[str, str]] = None) -> None:
        self.canned = canned or {}

    def complete(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> str:
        return self.canned.get(prompt, "[mock-response]")


# ---------------------------------------------------------------------------
# Gemini provider — uses google-genai (new unified SDK)
# ---------------------------------------------------------------------------

class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str) -> None:
        # Lazy import so the module is importable without the SDK installed.
        # Uses the new google-genai unified SDK (same as llm_client.py).
        from google import genai  # type: ignore[import-not-found]

        self._client = genai.Client(api_key=api_key)
        self._genai = genai

    def complete(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> str:
        from google.genai import types as genai_types  # type: ignore[import-not-found]

        config = genai_types.GenerateContentConfig(
            max_output_tokens=max_tokens,
        )
        resp = self._client.models.generate_content(
            model=model,
            contents=prompt,
            config=config,
        )
        return resp.text or ""


# ---------------------------------------------------------------------------
# Anthropic provider — uses anthropic SDK
# ---------------------------------------------------------------------------

class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str) -> None:
        import anthropic  # type: ignore[import-not-found]

        self._client = anthropic.Anthropic(api_key=api_key)

    def complete(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> str:
        msg = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text


# ---------------------------------------------------------------------------
# OpenAI provider — uses openai SDK
# ---------------------------------------------------------------------------

class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str) -> None:
        from openai import OpenAI  # type: ignore[import-not-found]

        self._client = OpenAI(api_key=api_key)

    def complete(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> str:
        resp = self._client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_provider() -> LLMProvider:
    """Return the configured LLM provider.

    Priority:
      1. DEMO_REPLAY=1  -> MockProvider (no API calls at all)
      2. LLM_PROVIDER   -> the named provider (default: gemini)
      3. LLM_PROVIDER=mock -> MockProvider (useful for local dev)
    """
    if os.getenv("DEMO_REPLAY", "").strip() in {"1", "true", "True"}:
        return MockProvider()

    name = os.getenv("LLM_PROVIDER", "gemini").lower().strip()

    if name == "gemini":
        return GeminiProvider(api_key=os.environ["GEMINI_API_KEY"])

    if name == "anthropic":
        return AnthropicProvider(api_key=os.environ["ANTHROPIC_API_KEY"])

    if name == "openai":
        return OpenAIProvider(api_key=os.environ["OPENAI_API_KEY"])

    if name == "mock":
        return MockProvider()

    raise ValueError(
        f"Unknown LLM_PROVIDER: {name!r}. Valid values: gemini | anthropic | openai | mock"
    )
