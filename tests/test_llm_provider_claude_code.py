"""Unit tests for ClaudeCodeProvider — runs without the real CLI by mocking subprocess."""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from src.agents.llm_provider import ClaudeCodeProvider, get_provider


def _fake_proc(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    p = MagicMock()
    p.stdout = stdout
    p.stderr = stderr
    p.returncode = returncode
    return p


def test_claude_code_provider_parses_result():
    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "hello world",
        "total_cost_usd": 0.001,
        "usage": {"input_tokens": 5, "output_tokens": 2},
    }
    with patch(
        "subprocess.run",
        return_value=_fake_proc(stdout=json.dumps(payload)),
    ):
        provider = ClaudeCodeProvider()
        out = provider.complete("hi", model="claude-opus-4-7")
        assert out == "hello world"


def test_claude_code_provider_raises_on_is_error_true():
    payload = {
        "type": "result",
        "subtype": "error",
        "is_error": True,
        "result": "boom",
    }
    with patch(
        "subprocess.run",
        return_value=_fake_proc(stdout=json.dumps(payload)),
    ):
        provider = ClaudeCodeProvider()
        with pytest.raises(RuntimeError, match="boom"):
            provider.complete("hi", model="claude-opus-4-7")


def test_claude_code_provider_raises_on_nonzero_exit():
    with patch(
        "subprocess.run",
        return_value=_fake_proc(stdout="", stderr="oops", returncode=1),
    ):
        provider = ClaudeCodeProvider()
        with pytest.raises(RuntimeError, match="oops"):
            provider.complete("hi", model="claude-opus-4-7")


def test_claude_code_provider_raises_when_cli_missing():
    with patch("subprocess.run", side_effect=FileNotFoundError("no claude")):
        provider = ClaudeCodeProvider(executable="claude_does_not_exist")
        with pytest.raises(RuntimeError, match="not found"):
            provider.complete("hi", model="claude-opus-4-7")


def test_factory_returns_claude_code_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "claude_code")
    p = get_provider()
    assert isinstance(p, ClaudeCodeProvider)


def test_factory_accepts_aliases(monkeypatch):
    for alias in ("claude-code", "ClaudeCode", "CLAUDECODE"):
        monkeypatch.setenv("LLM_PROVIDER", alias)
        assert isinstance(get_provider(), ClaudeCodeProvider)
