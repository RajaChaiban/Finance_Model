"""Tests for pluggable LLM provider abstraction (Phase 5)."""
import os
from unittest.mock import patch
from src.agents.llm_provider import (
    LLMProvider, MockProvider, GeminiProvider, AnthropicProvider, get_provider,
)


def test_mock_provider_deterministic():
    p = MockProvider(canned={"hi": "hello"})
    assert p.complete("hi", model="any") == "hello"


def test_mock_provider_fallback():
    p = MockProvider()
    assert p.complete("unknown_prompt", model="any") == "[mock-response]"


def test_provider_factory_default_is_gemini(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    # Clear DEMO_REPLAY so we don't accidentally short-circuit.
    monkeypatch.delenv("DEMO_REPLAY", raising=False)
    p = get_provider()
    assert isinstance(p, GeminiProvider)


def test_provider_factory_anthropic(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.delenv("DEMO_REPLAY", raising=False)
    p = get_provider()
    assert isinstance(p, AnthropicProvider)


def test_provider_factory_mock_in_replay(monkeypatch):
    monkeypatch.setenv("DEMO_REPLAY", "1")
    p = get_provider()
    assert isinstance(p, MockProvider)


def test_provider_factory_explicit_mock(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("DEMO_REPLAY", raising=False)
    p = get_provider()
    assert isinstance(p, MockProvider)
