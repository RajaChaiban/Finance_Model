"""Deterministic event-loop kernel.

Maintains a min-heap of ``(timestamp, event)`` tuples and drains them in
order. Each tick, the kernel:

  1. pops all events with ``ts <= now``
  2. routes ``MarketDataEvent`` → participants' ``on_book``
  3. routes ``FillEvent`` → participants' ``on_fill``
  4. asks each participant for ``decide(now)`` → list[Order]
  5. routes new orders through :class:`~src.esmm.sim.latency.LatencyModel`
  6. delivers orders to :class:`~src.esmm.sim.matching.MatchEngine`
  7. emits snapshots at configurable cadence

Determinism: given the same seed + participant set + scenario, every
fill, every cancel, every snapshot timestamp is reproducible.

Phase-2 implementation. Stub today.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from heapq import heappush, heappop
from typing import Any


@dataclass(order=True)
class _ScheduledEvent:
    """Internal heap node — ordered by ts then by insertion order."""

    ts: float
    seq: int = field(compare=True)
    event: Any = field(compare=False)


@dataclass
class KernelConfig:
    """Runtime knobs for one simulator run."""

    duration_sec: float
    snapshot_interval_sec: float = 0.05  # 20 Hz default
    seed: int | None = None
    enable_latency: bool = True


class Kernel:
    """Phase-2 implementation lands here."""

    def __init__(self, config: KernelConfig) -> None:
        self.config = config
        self._heap: list[_ScheduledEvent] = []
        self._seq: int = 0
        self._now: float = 0.0

    def schedule(self, ts: float, event: Any) -> None:
        self._seq += 1
        heappush(self._heap, _ScheduledEvent(ts, self._seq, event))

    def run(self) -> dict[str, Any]:
        """Phase 2."""
        raise NotImplementedError("Phase 2")


__all__ = ["Kernel", "KernelConfig"]
