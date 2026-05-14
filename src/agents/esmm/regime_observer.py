"""Regime observer — feature-based market-state classifier.

Pure function over a snapshot path: feeds the existing FeatureEngine,
reads the rolling RV / momentum / signed-flow stack, and applies
threshold rules to label the current regime.

Thresholds are tuned to be sensible defaults for a synthetic GBM at
sigma_per_step=0.0005 (~8 bps per step). They're constructor args
so a future LLM agent can recalibrate them per asset class.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.agents.esmm.schemas import Regime, RegimeObservation
from src.esmm.features import FeatureEngine
from src.esmm.schemas import Fill, OrderBookSnapshot


@dataclass(frozen=True)
class RegimeThresholds:
    """All regime-classification cut-offs in one struct, easy to override."""

    # Realised variance bands. Numbers are fast-window RV measured on log returns
    # (so for a 10-snap window with sigma_per_step=0.0005, calm RV ≈ 2.5e-7).
    rv_calm_max: float = 4.0e-7
    rv_volatile_min: float = 1.5e-6
    rv_stress_min: float = 5.0e-6

    # Absolute momentum (sum of recent log returns) above which we call TRENDING.
    momentum_trend_min: float = 0.002  # 20 bps total drift

    # |signed_flow| above which we treat flow as imbalanced enough to escalate.
    signed_flow_imbalance: float = 50.0


class RegimeObserver:
    """Stateless given (engine, thresholds). Replay the snapshots, return one
    RegimeObservation reflecting the *final* state."""

    def __init__(
        self,
        thresholds: Optional[RegimeThresholds] = None,
        fast_window: int = 10,
        slow_window: int = 60,
    ):
        self.thresholds = thresholds or RegimeThresholds()
        self.fast_window = fast_window
        self.slow_window = slow_window

    def observe(
        self,
        snapshots: list[OrderBookSnapshot],
        recent_fills: Optional[list[Fill]] = None,
    ) -> RegimeObservation:
        """Run the FeatureEngine through the path and label the final state."""
        if not snapshots:
            return RegimeObservation(
                regime=Regime.CALM,
                rv_fast=0.0, rv_slow=0.0, momentum=0.0,
                signed_flow=0.0, rv_ratio=1.0, n_snapshots=0,
            )

        engine = FeatureEngine(fast_window=self.fast_window, slow_window=self.slow_window)
        last_features: dict[str, float] = {}
        for snap in snapshots:
            last_features = engine.update(snap, recent_fills=recent_fills)

        rv_fast = last_features.get("rv_fast", 0.0)
        rv_slow = last_features.get("rv_slow", 0.0)
        momentum = last_features.get("momentum", 0.0)
        signed_flow = last_features.get("signed_flow", 0.0)
        rv_ratio = last_features.get("rv_ratio", 1.0)

        regime = self._classify(rv_fast, momentum, signed_flow)

        return RegimeObservation(
            regime=regime,
            rv_fast=rv_fast,
            rv_slow=rv_slow,
            momentum=momentum,
            signed_flow=signed_flow,
            rv_ratio=rv_ratio,
            n_snapshots=len(snapshots),
        )

    def _classify(self, rv_fast: float, momentum: float, signed_flow: float) -> Regime:
        """Apply threshold rules in escalation order: stress > volatile > trending > calm."""
        t = self.thresholds

        if rv_fast >= t.rv_stress_min:
            return Regime.STRESS
        if rv_fast >= t.rv_volatile_min or abs(signed_flow) >= t.signed_flow_imbalance * 4:
            return Regime.VOLATILE
        if abs(momentum) >= t.momentum_trend_min:
            return Regime.TRENDING
        if rv_fast <= t.rv_calm_max:
            return Regime.CALM
        # Mild vol with no trend or stress signal — treat as calm.
        return Regime.CALM


def classify_regime(
    snapshots: list[OrderBookSnapshot],
    recent_fills: Optional[list[Fill]] = None,
    thresholds: Optional[RegimeThresholds] = None,
) -> RegimeObservation:
    """Convenience wrapper for callers who don't want to instantiate."""
    return RegimeObserver(thresholds=thresholds).observe(snapshots, recent_fills)
