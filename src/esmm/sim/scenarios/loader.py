"""Scenario YAML loader.

Phase-4 implementation. Stub today.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Scenario:
    scenario_id: str
    description: str
    duration_sec: float
    participants: list[dict[str, Any]]
    events: list[dict[str, Any]]
    regime_label: str


def load_library(path: Path | None = None) -> dict[str, Scenario]:
    """Phase 4."""
    raise NotImplementedError("Phase 4")


__all__ = ["Scenario", "load_library"]
