"""Agentic decision layer on top of src/esmm/.

Mirrors the structuring co-pilot's layering: deterministic engines
underneath, an agent loop on top that observes, proposes, evaluates,
and recommends.

Pipeline:
    snapshots
        │
        ▼
    RegimeObserver  → RegimeObservation (calm / trending / volatile / stress)
        │
        ▼
    ConfigStrategist → ConfigProposal (MarketMakingConfig + rationale)
        │
        ▼
    BacktestRunner  → BacktestResult  (uses src/esmm/backtest.run_backtest)
        │
        ▼
    TCACritic       → TCAScore (0-100 + per-bucket diagnostics + recs)
        │
        ▼
    Orchestrator loops: if score below threshold, feed recs back to
    strategist and re-propose, up to max_iterations.

This is fully deterministic v1. An LLM-decorated v2 can wrap each step
without changing the contracts.
"""

from src.agents.esmm import (
    config_strategist,
    orchestrator,
    regime_observer,
    schemas,
    tca_critic,
)

__all__ = [
    "config_strategist",
    "orchestrator",
    "regime_observer",
    "schemas",
    "tca_critic",
]
