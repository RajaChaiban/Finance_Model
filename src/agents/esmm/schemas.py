"""Pydantic types for the agentic eSMM layer.

Every step boundary uses one of these. Same discipline as src/esmm/schemas.py
— no untyped dicts cross module boundaries.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from src.esmm.schemas import MarketMakingConfig, TCABreakdown


class Regime(str, Enum):
    """Coarse market regime label.

    Boundaries are deliberate, threshold-based, and tunable in the observer:
    - CALM: low realised vol, low drift, balanced flow
    - TRENDING: elevated momentum but not chaotic; vol still controlled
    - VOLATILE: realised vol elevated; flow imbalance
    - STRESS: realised vol very high; severe imbalance — pull width hard
    """

    CALM = "calm"
    TRENDING = "trending"
    VOLATILE = "volatile"
    STRESS = "stress"


class RegimeObservation(BaseModel):
    """RegimeObserver output — the regime label plus the features that justified it."""

    model_config = ConfigDict(frozen=True)
    regime: Regime
    rv_fast: float = Field(..., description="Rolling realised variance, short window")
    rv_slow: float = Field(..., description="Rolling realised variance, long window")
    momentum: float = Field(..., description="Sum of fast-window log returns")
    signed_flow: float = Field(..., description="Mean signed trade size in slow window")
    rv_ratio: float = Field(..., description="rv_fast / rv_slow; >1 = vol expanding")
    n_snapshots: int


class ConfigProposal(BaseModel):
    """ConfigStrategist output — a config plus rationale + parent regime."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    config: MarketMakingConfig
    parent_regime: Regime
    rationale: str
    iteration: int = 0  # bumps each time the loop re-proposes


class TCAScore(BaseModel):
    """TCACritic output — overall score 0-100 + per-bucket diagnostics."""

    model_config = ConfigDict(frozen=True)
    score: float = Field(..., ge=0.0, le=100.0)
    spread_capture_ratio: float = Field(..., description="spread_capture / gross_pnl_magnitude — 0 to 1, higher = healthier")
    adverse_selection_ratio: float = Field(..., description="|adv_sel| / max(|spread|, 1e-9) — lower = healthier")
    hedge_drag_ratio: float = Field(..., description="|hedge| / max(|spread|, 1e-9) — lower = healthier")
    inventory_volatility: float = Field(..., description="|inventory_pnl| / max(|spread|, 1e-9)")
    recommendations: list[str] = Field(default_factory=list)


class AgenticDecision(BaseModel):
    """One full pass of the loop."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    iteration: int
    observation: RegimeObservation
    proposal: ConfigProposal
    tca: TCABreakdown
    score: TCAScore
    accepted: bool = False  # True when score >= acceptance threshold


class AgenticRunResult(BaseModel):
    """Full orchestrator output — every iteration plus the winning decision."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    history: list[AgenticDecision]
    best_decision: Optional[AgenticDecision] = None
    converged: bool
    stopped_reason: str
