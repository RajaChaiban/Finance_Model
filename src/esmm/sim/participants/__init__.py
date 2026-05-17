"""Participant archetypes for the simulator.

Each participant implements :class:`~src.esmm.sim.participants.base.Participant`
and is dropped into the kernel. The kernel routes book updates and fills
to them and asks them for orders.

Archetypes (built in phases 2 and 4):

  * ``noise``           — Poisson uninformed retail flow (phase 2)
  * ``informed``        — sees future mid Δt ahead, generates adverse selection (phase 2)
  * ``replay_taker``    — replays historical aggressor flow for Track D (phase 2)
  * ``momentum``        — EMA-crossover taker (phase 4)
  * ``mean_reverter``   — z-score taker against momentum (phase 4)
  * ``news_shock``      — scripted regime breaks: gaps, halts, vol explosions (phase 4)
"""

from src.esmm.sim.participants import base

__all__ = ["base"]
