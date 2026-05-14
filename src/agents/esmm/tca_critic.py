"""TCA critic — turns a TCABreakdown into a 0-100 score + recommendations.

Scoring philosophy (deliberate, defensible to interview):

  1. Spread capture is the *only* fundamentally good P&L bucket on a MM book.
     Inventory + adverse-selection + hedge-drag are all costs to manage.

  2. A clean book has spread capture dominating, with adverse-selection and
     hedge drag both small fractions of spread.

  3. Inventory P&L is regime-driven, not skill — large absolute inventory P&L
     (positive OR negative) is a signal that the book is over-leveraged.

Score recipe (0–100, higher is better):

    base = 50  (neutral)
    + 30 * (spread_capture_ratio)
    - 20 * adverse_selection_ratio
    - 15 * hedge_drag_ratio
    - 10 * inventory_volatility
    + clip to [0, 100]

Where each ratio is normalised against |spread_capture| so a book with no
captured spread can't game the metric (ratios become huge → score crashes).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.agents.esmm.schemas import TCAScore
from src.esmm.schemas import TCABreakdown


@dataclass(frozen=True)
class CriticThresholds:
    """Which ratio levels trigger which textual recommendations."""
    adverse_selection_warn: float = 0.3
    adverse_selection_critical: float = 0.6
    hedge_drag_warn: float = 0.4
    inventory_volatility_warn: float = 0.8
    spread_capture_min_healthy: float = 0.5  # spread should be ≥ 50% of |total|


class TCACritic:
    """Stateless scorer. Given a TCABreakdown, return a TCAScore."""

    def __init__(self, thresholds: Optional[CriticThresholds] = None):
        self.thresholds = thresholds or CriticThresholds()

    def score(self, tca: TCABreakdown) -> TCAScore:
        spread = tca.spread_capture_pnl
        adverse = abs(tca.adverse_selection_pnl)
        hedge = abs(tca.hedge_pnl)
        inventory = abs(tca.inventory_pnl)

        # Gross activity = the absolute magnitude of every bucket. This is the
        # right denominator because it CAN'T shrink as costs grow — the metric
        # then reflects "what share of the book's P&L motion came from spread
        # capture vs costs". Using |total_pnl| was the old (buggy) formula and
        # gave perverse credit when costs cancelled spread.
        gross = abs(spread) + adverse + hedge + inventory
        gross = max(gross, 1e-9)

        # Negative spread means we paid edge away — give it zero credit, never
        # negative (the cost penalties below already cover the loss).
        spread_capture_ratio = max(spread, 0.0) / gross
        # Cost ratios stay relative to spread itself, so a critic recommending
        # "widen because adv-sel ratio is 0.8" reads naturally.
        denom = max(abs(spread), 1e-9)
        adverse_selection_ratio = adverse / denom
        hedge_drag_ratio = hedge / denom
        inventory_volatility = inventory / denom

        # Compute the score
        score = 50.0
        score += 30.0 * max(0.0, min(1.0, spread_capture_ratio))
        score -= 20.0 * min(1.5, adverse_selection_ratio)
        score -= 15.0 * min(1.5, hedge_drag_ratio)
        score -= 10.0 * min(1.5, inventory_volatility)
        score = max(0.0, min(100.0, score))

        # Build recommendations
        recs: list[str] = []
        t = self.thresholds

        if spread_capture_ratio < t.spread_capture_min_healthy:
            recs.append(
                "Spread capture is dominated by other costs — widen base spread or rethink universe."
            )
        if adverse_selection_ratio > t.adverse_selection_critical:
            recs.append(
                "Critical adverse selection: post-fill markouts are eating spread. "
                "Strongly consider widening or pulling quote when OBI signals informed flow."
            )
        elif adverse_selection_ratio > t.adverse_selection_warn:
            recs.append(
                "Adverse selection elevated — widen by ~20% on next iteration."
            )
        if hedge_drag_ratio > t.hedge_drag_warn:
            recs.append(
                "Hedge drag heavy — loosen hedge band so we hedge less often / less aggressively."
            )
        if inventory_volatility > t.inventory_volatility_warn:
            recs.append(
                "Inventory P&L volatile — tighten max inventory and increase skew so quotes "
                "lean harder against position."
            )
        if not recs:
            recs.append("Book healthy — no adjustment recommended.")

        return TCAScore(
            score=score,
            spread_capture_ratio=spread_capture_ratio,
            adverse_selection_ratio=adverse_selection_ratio,
            hedge_drag_ratio=hedge_drag_ratio,
            inventory_volatility=inventory_volatility,
            recommendations=recs,
        )
