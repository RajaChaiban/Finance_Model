"""Config strategist — turns a regime label + critic feedback into a
proposed MarketMakingConfig.

v1 is rule-based and fully deterministic. The mapping is:

    regime   →  half-spread bps   skew_bps_per_unit   max_inventory   hedge_thresh
    -------     ---------------   -----------------   -------------   ------------
    CALM        baseline          baseline            baseline        baseline
    TRENDING    baseline + 20%    baseline + 50%      baseline - 30%  baseline - 30%
    VOLATILE    baseline + 60%    baseline * 2        baseline - 50%  baseline - 50%
    STRESS      baseline + 150%   baseline * 4        baseline - 80%  baseline - 70%

Then critic-driven adjustments fine-tune from there:
    - high adverse_selection_ratio  → widen half-spread (+20%)
    - high hedge_drag_ratio         → loosen hedge band (+30%)
    - high inventory_volatility     → tighten max_inventory (-20%) and bump skew (+20%)

A future LLM-decorated v2 can override `propose()` to ask Gemini for
the adjustments instead of using rules. Same return type either way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.agents.esmm.schemas import (
    ConfigProposal,
    Regime,
    RegimeObservation,
    TCAScore,
)
from src.esmm.schemas import MarketMakingConfig


# Multipliers applied to the baseline config per regime. Tuned for sensible
# behaviour on the synthetic GBM; numbers will need re-tuning on live data.
REGIME_MULTIPLIERS: dict[Regime, dict[str, float]] = {
    Regime.CALM: {
        "half_spread": 1.0, "skew": 1.0, "max_inv": 1.0, "hedge_thresh": 1.0,
    },
    Regime.TRENDING: {
        "half_spread": 1.2, "skew": 1.5, "max_inv": 0.7, "hedge_thresh": 0.7,
    },
    Regime.VOLATILE: {
        "half_spread": 1.6, "skew": 2.0, "max_inv": 0.5, "hedge_thresh": 0.5,
    },
    Regime.STRESS: {
        "half_spread": 2.5, "skew": 4.0, "max_inv": 0.2, "hedge_thresh": 0.3,
    },
}


@dataclass(frozen=True)
class CriticAdjustments:
    """Bands above which a critic ratio triggers an adjustment."""
    adverse_selection_high: float = 0.4   # |adv_sel| > 40% of spread → widen
    hedge_drag_high: float = 0.5          # |hedge| > 50% of spread → loosen hedge band
    inventory_volatility_high: float = 1.0  # |inv| > 100% of spread → tighten max_inv


class ConfigStrategist:
    """Maps (regime, critic_feedback) → MarketMakingConfig proposal."""

    def __init__(
        self,
        baseline: Optional[MarketMakingConfig] = None,
        adjustments: Optional[CriticAdjustments] = None,
    ):
        self.baseline = baseline or MarketMakingConfig(symbol="SPY")
        self.adjustments = adjustments or CriticAdjustments()

    def propose(
        self,
        observation: RegimeObservation,
        prior_score: Optional[TCAScore] = None,
        iteration: int = 0,
    ) -> ConfigProposal:
        regime = observation.regime
        mults = REGIME_MULTIPLIERS[regime]

        half_spread = self.baseline.base_half_spread_bps * mults["half_spread"]
        skew = self.baseline.inventory_skew_bps_per_unit * mults["skew"]
        max_inv = max(50.0, self.baseline.max_inventory * mults["max_inv"])
        hedge_thresh = max(20.0, self.baseline.delta_hedge_threshold * mults["hedge_thresh"])
        hedge_band = max(5.0, self.baseline.delta_hedge_band * mults["hedge_thresh"])

        rationale_parts = [f"regime={regime.value}"]

        # Critic-driven fine-tunes (only when we have prior feedback).
        if prior_score is not None:
            adj = self.adjustments
            if prior_score.adverse_selection_ratio > adj.adverse_selection_high:
                half_spread *= 1.2
                rationale_parts.append("widened spread (adverse selection high)")
            if prior_score.hedge_drag_ratio > adj.hedge_drag_high:
                hedge_thresh *= 1.3
                hedge_band *= 1.3
                rationale_parts.append("loosened hedge band (hedge drag high)")
            if prior_score.inventory_volatility > adj.inventory_volatility_high:
                max_inv *= 0.8
                skew *= 1.2
                rationale_parts.append("tightened max inventory + bumped skew (inventory vol high)")

        config = MarketMakingConfig(
            symbol=self.baseline.symbol,
            base_half_spread_bps=half_spread,
            inventory_skew_bps_per_unit=skew,
            max_inventory=max_inv,
            quote_size=self.baseline.quote_size,
            fee_bps=self.baseline.fee_bps,
            delta_hedge_threshold=hedge_thresh,
            delta_hedge_band=hedge_band,
            gamma_hedge_threshold=self.baseline.gamma_hedge_threshold,
            gamma_hedge_band=self.baseline.gamma_hedge_band,
        )

        return ConfigProposal(
            config=config,
            parent_regime=regime,
            rationale="; ".join(rationale_parts),
            iteration=iteration,
        )
