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
    openrouter_api_key: str = ""

    # Demo / safety knobs.
    demo_replay: bool = False
    cost_ceiling_usd: float = 0.50
    # Phase 7 — tenant-level cost ceiling. Per-process accumulator across
    # ALL sessions in the current process. ``0.0`` disables the global cap;
    # any positive value triggers a hard error when sum(cost across all
    # sessions in the store) exceeds it. Useful for shared dev tenants
    # where a runaway loop on one client must not exhaust the budget for
    # the whole desk.
    tenant_cost_ceiling_usd: float = 0.0

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

    # ------------------------------------------------------------------
    # Market-intelligence (RAG) layer — see src/agents/market_intelligence.py
    # ------------------------------------------------------------------
    market_intel_enabled: bool = True
    market_intel_persist_dir: str = "./data/market_intel"
    market_intel_collection: str = "market-intelligence"
    market_intel_embeddings_model: str = "BAAI/bge-base-en-v1.5"
    # Which Gemini tier the MI prompts call. Free-form Q&A is short-context
    # and structured — fast tier is the right default.
    market_intel_model: str = DEFAULT_MODEL_FAST

    # MI synthesis-LLM provider. "gemini" (default) routes through the same
    # LLMClient the agents use (cost-tracking, retries, DEMO_REPLAY).
    # "openrouter" routes via openrouter_adapter — useful when Gemini is
    # quota-blocked or you want to swap models per session.
    market_intel_llm_provider: str = "gemini"
    # OpenRouter model id when provider="openrouter". Provider/model form.
    market_intel_openrouter_model: str = "anthropic/claude-haiku-4-5"
    # Optional headers OpenRouter uses for rankings/leaderboard attribution.
    market_intel_openrouter_referer: str = ""
    market_intel_openrouter_title: str = ""

    @property
    def has_gemini(self) -> bool:
        return bool(self.gemini_api_key) and not self.demo_replay

    @property
    def has_openrouter(self) -> bool:
        return bool(self.openrouter_api_key) and not self.demo_replay

    @property
    def market_intel_active(self) -> bool:
        """MI runs when the flag is on AND we have a usable LLM (live or replay)."""
        if not self.market_intel_enabled:
            return False
        if self.demo_replay:
            return True
        if self.market_intel_llm_provider == "openrouter":
            return self.has_openrouter
        return self.has_gemini


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
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
        demo_replay=os.getenv("DEMO_REPLAY", "0").strip() in {"1", "true", "True"},
        cost_ceiling_usd=float(os.getenv("AGENT_COST_CEILING_USD", "0.50")),
        tenant_cost_ceiling_usd=float(os.getenv("AGENT_TENANT_COST_CEILING_USD", "0.0")),
        model_smart=smart,
        model_fast=fast,
        model_strategist=_per_agent("AGENT_MODEL_STRATEGIST", smart),
        model_narrator=_per_agent("AGENT_MODEL_NARRATOR", smart),
        model_intake=_per_agent("AGENT_MODEL_INTAKE", fast),
        model_validator=_per_agent("AGENT_MODEL_VALIDATOR", fast),
        model_scenario=_per_agent("AGENT_MODEL_SCENARIO", fast),
        market_intel_enabled=os.getenv("MARKET_INTEL_ENABLED", "1").strip()
            in {"1", "true", "True"},
        market_intel_persist_dir=os.getenv(
            "MARKET_INTEL_PERSIST_DIR", "./data/market_intel"
        ).strip() or "./data/market_intel",
        market_intel_collection=os.getenv(
            "MARKET_INTEL_COLLECTION", "market-intelligence"
        ).strip() or "market-intelligence",
        market_intel_embeddings_model=os.getenv(
            "MARKET_INTEL_EMBEDDINGS_MODEL",
            "BAAI/bge-base-en-v1.5",
        ).strip() or "BAAI/bge-base-en-v1.5",
        market_intel_model=_per_agent("AGENT_MODEL_MARKET_INTEL", fast),
        market_intel_llm_provider=os.getenv(
            "MARKET_INTEL_LLM_PROVIDER", "gemini"
        ).strip().lower() or "gemini",
        market_intel_openrouter_model=os.getenv(
            "MARKET_INTEL_OPENROUTER_MODEL", "anthropic/claude-haiku-4-5"
        ).strip() or "anthropic/claude-haiku-4-5",
        market_intel_openrouter_referer=os.getenv(
            "MARKET_INTEL_OPENROUTER_REFERER", ""
        ).strip(),
        market_intel_openrouter_title=os.getenv(
            "MARKET_INTEL_OPENROUTER_TITLE", ""
        ).strip(),
    )


@lru_cache(maxsize=1)
def get_agent_config() -> AgentConfig:
    """Lazy singleton. Call reload() in tests to bypass the cache."""
    return _from_env()


def reload() -> AgentConfig:
    """Reload from environment. Used by tests that mutate env vars."""
    get_agent_config.cache_clear()
    return get_agent_config()
