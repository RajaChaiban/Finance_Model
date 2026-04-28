"""Configuration for the LLM agent layer.

Loads secrets from .env (via python-dotenv if present) and exposes them via a
single AgentConfig instance. Keep this separate from PricingConfig — pricing is
deterministic and YAML-driven; agents need API keys and tunables that live in
the environment, not in committed config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:
    from dotenv import load_dotenv
    _DOTENV_AVAILABLE = True
except ImportError:
    _DOTENV_AVAILABLE = False


def _load_env() -> None:
    """Load .env from repo root if dotenv is installed and the file exists."""
    if not _DOTENV_AVAILABLE:
        return
    repo_root = Path(__file__).resolve().parents[2]
    env_path = repo_root / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


# Gemini model identifiers. Keep in one place so swapping a tier is one edit.
# Gemini 2.5 family (2025+):
#   * Pro      — top quality, best reasoning. Use for the IP-bearing agents.
#   * Flash    — balanced. Default for parsers / validators.
#   * Flash-Lite — cheapest, for trivial calls.
MODEL_PRO = "gemini-2.5-pro"
MODEL_FLASH = "gemini-2.5-flash"
MODEL_FLASH_LITE = "gemini-2.5-flash-lite"


@dataclass(frozen=True)
class AgentConfig:
    """Loaded once at startup. Immutable; replace via reload() for tests."""

    gemini_api_key: str = ""
    fred_api_key: str = ""
    polygon_api_key: str = ""

    # Demo / safety knobs.
    demo_replay: bool = False
    cost_ceiling_usd: float = 0.50

    # Model tier routing — agents that need creative reasoning get Pro,
    # parsers / validators get Flash for cost.
    model_strategist: str = MODEL_PRO
    model_narrator: str = MODEL_PRO
    model_intake: str = MODEL_FLASH
    model_validator: str = MODEL_FLASH
    model_scenario: str = MODEL_FLASH

    # Sane defaults for the SDK call.
    max_output_tokens: int = 4096
    request_timeout_s: float = 60.0
    max_retries: int = 2

    @property
    def has_gemini(self) -> bool:
        return bool(self.gemini_api_key) and not self.demo_replay


def _from_env() -> AgentConfig:
    _load_env()
    # Accept GEMINI_API_KEY (preferred) or GOOGLE_API_KEY (fallback — the SDK
    # picks up either, and many Google-stack devs already export GOOGLE_API_KEY).
    gemini_key = (
        os.getenv("GEMINI_API_KEY", "").strip()
        or os.getenv("GOOGLE_API_KEY", "").strip()
    )
    return AgentConfig(
        gemini_api_key=gemini_key,
        fred_api_key=os.getenv("FRED_API_KEY", "").strip(),
        polygon_api_key=os.getenv("POLYGON_API_KEY", "").strip(),
        demo_replay=os.getenv("DEMO_REPLAY", "0").strip() in {"1", "true", "True"},
        cost_ceiling_usd=float(os.getenv("AGENT_COST_CEILING_USD", "0.50")),
    )


@lru_cache(maxsize=1)
def get_agent_config() -> AgentConfig:
    """Lazy singleton. Call reload() in tests to bypass the cache."""
    return _from_env()


def reload() -> AgentConfig:
    """Reload from environment. Used by tests that mutate env vars."""
    get_agent_config.cache_clear()
    return get_agent_config()
