"""ESMM simulation kernel.

This sub-package powers two related capabilities:

- **Sandbox** — fully synthetic LOB populated with scripted/stochastic
  participant archetypes. Used to stress-test our MM strategies and the
  Layer-C agentic loop under controlled, reproducible conditions.
- **Replay** — same LOB engine seeded with real historical tape via the
  existing :mod:`src.esmm.adapters` protocol. Used for honest P&L
  attribution and walk-forward validation.

Design lives in ``docs/superpowers/specs/2026-05-17-esmm-simulation-design.md``.

Module layout::

    sim/
    ├── lob.py            price-time-priority limit order book
    ├── matching.py       match engine (FIFO at each price level)
    ├── latency.py        configurable submit/cancel latency
    ├── kernel.py         deterministic event-loop driver
    ├── arena.py          N-strategy bake-off on identical flow
    ├── risk.py           pre/post-trade limits + kill-switch
    ├── attribution.py    P&L decomposition + counterfactual
    ├── participants/     informed/momentum/mean-rev/noise/news/replay
    ├── scenarios/        curated YAML stress library + loader
    └── reporters/        walk_forward + monte_carlo
"""

from src.esmm.sim import (
    arena,
    attribution,
    kernel,
    latency,
    lob,
    matching,
    risk,
    participants,
    reporters,
    scenarios,
)

__all__ = [
    "arena",
    "attribution",
    "kernel",
    "latency",
    "lob",
    "matching",
    "participants",
    "reporters",
    "risk",
    "scenarios",
]
