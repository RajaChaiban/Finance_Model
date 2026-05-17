"""Latency model for the simulator.

Every submit / cancel / modify request goes through a configurable
distribution before it hits the LOB. Default is log-normal with
mean 15 ms and σ 8 ms — within typical equity-exchange round-trip
bounds. Distributions are seeded so runs are reproducible.

Phase-2 implementation. Stub today.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import random


@dataclass
class LatencyConfig:
    """Configurable latency parameters in seconds."""

    submit_mean_ms: float = 15.0
    submit_sigma_ms: float = 8.0
    cancel_mean_ms: float = 12.0
    cancel_sigma_ms: float = 6.0
    seed: int | None = None


class LatencyModel:
    """Samples per-event latency from log-normal distributions."""

    def __init__(self, config: LatencyConfig) -> None:
        self.config = config
        self._rng = random.Random(config.seed)

    def sample_submit_ms(self) -> float:
        """Phase 2."""
        raise NotImplementedError("Phase 2")

    def sample_cancel_ms(self) -> float:
        """Phase 2."""
        raise NotImplementedError("Phase 2")


__all__ = ["LatencyConfig", "LatencyModel"]
