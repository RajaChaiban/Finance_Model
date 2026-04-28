"""Configuration for the LLM agent layer.

Loads secrets from .env (via python-dotenv if present) and exposes them via a
single AgentConfig instance. Keep this separate from PricingConfig — pricing is
deterministic and YAML-driven; agents need API keys and tunables that live in
the environment, not in committed config.

Model selection
---------------
Two tiers, picked once at startup:

  * AGENT_MODEL_SMART — the IP-bearing agents (Strategist, Narrator). Default:
    gemini-3-pro-preview (the smartest model Google currently offers).
  * AGENT_MODEL_FAST  — the parser / validator agents (Intake, Validator,
    Scenario commentary). Default: gemini-2.5-flash.

Per-agent overrides take precedence over the tier defaults:

  * AGENT_MODEL_STRATEGIST, AGENT_MODEL_NARRATOR
  * AGENT_MODEL_INTAKE, AGENT_MODEL_VALIDATOR, AGENT_MODEL_SCENARIO
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


# ---------------------------------------------------------------------------
# Known Gemini model identifiers — current as of 2026-Q1.
# ---------------------------------------------------------------------------
# Gemini 3 (preview, the smartest tier today):
MODEL_3_PRO_PREVIEW = "gemini-3-pro-preview"        # complex reasoning / IP work
MODEL_3_FLASH_PREVIEW = "gemini-3-flash-preview"    # general text + multimodal

# Gemini 2.5 (GA, stable):
MODEL_2_5_PRO = "gemini-2.5-pro"
MODEL_2_5_FLASH = "gemini-2.5-flash"
MODEL_2_5_FLASH_LITE = "gemini-2.5-flash-lite"

# Default model tiers — overridable via env.
DEFAULT_MODEL_SMART = MODEL_3_PRO_PREVIEW
DEFAULT_MODEL_FAST = MODEL_2_5_FLASH


@dataclass(frozen=True)
class AgentConfig:
    """Loaded once at startup. Immutable; replace via reload() for tests."""

    gemini_api_key: str = ""
    fred_api_key: str = ""
    polygon_api_key: str = ""

    # Demo / safety knobs.
    demo_replay: bool = False
    cost_ceiling_usd: float = 0.50

    # Tier defaults.
    model_smart: str = DEFAULT_MODEL_SMART
    model_fast: str = DEFAULT_MODEL_FAST

    # Per-agent — populated from per-agent env vars OR fall back to tier.
    model_strategist: str = DEFAULT_MODEL_SMART
    model_narrator: str = DEFAULT_MODEL_SMART
    model_intake: str = DEFAULT_MODEL_FAST
    model_validator: str = DEFAULT_MODEL_FAST
    model_scenario: str = DEFAULT_MODEL_FAST

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

    smart = os.getenv("AGENT_MODEL_SMART", DEFAULT_MODEL_SMART).strip() or DEFAULT_MODEL_SMART
    fast = os.getenv("AGENT_MODEL_FAST", DEFAULT_MODEL_FAST).strip() or DEFAULT_MODEL_FAST

    def _per_agent(env_var: str, fallback: str) -> str:
        v = os.getenv(env_var, "").strip()
        return v or fallback

    return AgentConfig(
        gemini_api_key=gemini_key,
        fred_api_key=os.getenv("FRED_API_KEY", "").strip(),
        polygon_api_key=os.getenv("POLYGON_API_KEY", "").strip(),
        demo_replay=os.getenv("DEMO_REPLAY", "0").strip() in {"1", "true", "True"},
        cost_ceiling_usd=float(os.getenv("AGENT_COST_CEILING_USD", "0.50")),
        model_smart=smart,
        model_fast=fast,
        model_strategist=_per_agent("AGENT_MODEL_STRATEGIST", smart),
        model_narrator=_per_agent("AGENT_MODEL_NARRATOR", smart),
        model_intake=_per_agent("AGENT_MODEL_INTAKE", fast),
        model_validator=_per_agent("AGENT_MODEL_VALIDATOR", fast),
        model_scenario=_per_agent("AGENT_MODEL_SCENARIO", fast),
    )


@lru_cache(maxsize=1)
def get_agent_config() -> AgentConfig:
    """Lazy singleton. Call reload() in tests to bypass the cache."""
    return _from_env()


def reload() -> AgentConfig:
    """Reload from environment. Used by tests that mutate env vars."""
    get_agent_config.cache_clear()
    return get_agent_config()
