"""LifecycleAgent — post-trade restructuring + re-strike + roll.

The structuring desk's biggest blind spot in v1 is *post-trade*. A real desk
spends half its time on:
- Client wants to monetize gains: "the position is up 8% — do we close,
  re-strike higher, or sell the upside above the new mark?"
- Client wants to roll: original tenor expires soon, refresh into a new
  one with new strikes/barriers/coupons.
- Client wants to switch: original underlier is no longer their view.

LifecycleAgent takes an existing PricedCandidate (the prior trade) plus a
new MarketRegime (today's mark) and produces:
- ``current_mark`` — what the original structure is worth now.
- ``unrealized_pnl`` — current_mark − original_premium.
- ``reshape_options`` — three actions the structurer can offer:
    1. close (sell back at mid, lock in P&L).
    2. roll (push expiry, re-strike at-mark).
    3. enhance (e.g. add a knock-out, sell upside, switch to spread).
- ``attribution`` — Greek-by-Greek decomposition of the P&L from inception.

Phase 1 implements 1+2; the "enhance" path is a placeholder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.analysis.pnl_explain import PnLAttribution, explain_pnl

from .pricing import PricingAgent
from .state import (
    Candidate,
    Leg,
    MarketRegime,
    PricedCandidate,
    StructureKind,
)


@dataclass
class ReshapeOption:
    label: str                  # e.g. "close", "roll_3m", "enhance_short_call"
    description: str             # human-readable trader rationale
    new_premium_estimate: float  # what the new structure will cost / yield
    delta_pnl_to_client: float   # immediate cash to client (close) or 0 (roll)


@dataclass
class LifecycleAssessment:
    candidate_id: str
    structure_name: str
    inception_premium: float
    current_mark: float
    unrealized_pnl: float
    attribution: Optional[PnLAttribution] = None
    reshape_options: list[ReshapeOption] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "structure_name": self.structure_name,
            "inception_premium": self.inception_premium,
            "current_mark": self.current_mark,
            "unrealized_pnl": self.unrealized_pnl,
            "attribution": (
                self.attribution.__dict__ if self.attribution else None
            ),
            "reshape_options": [r.__dict__ for r in self.reshape_options],
            "warnings": list(self.warnings),
        }


class LifecycleAgent:
    name = "LifecycleAgent"

    def __init__(self) -> None:
        self._pricer = PricingAgent()

    def assess(
        self,
        *,
        prior: PricedCandidate,
        prior_regime: MarketRegime,
        current_regime: MarketRegime,
    ) -> LifecycleAssessment:
        """Re-mark the prior trade against today's regime + suggest reshapes.

        Inception premium is taken from prior.net_premium; current mark is
        computed by re-pricing each leg against current_regime via the same
        PricingAgent path the original session used.
        """
        # Re-price by replaying the candidate against current_regime.
        replayed = self._pricer._price_candidate(  # using internal helper for re-mark
            prior.candidate, current_regime, vol_handle=None,
        )
        current_mark = replayed.net_premium
        unrealized = current_mark - prior.net_premium

        # Attribution. Prior Greeks are per-share (small numbers); for a
        # structure-level attribution we keep them in those units and the
        # caller converts to USD.
        attribution = None
        try:
            attribution = explain_pnl(
                prev_price=prior.net_premium,
                prev_greeks={
                    "delta": prior.greeks.delta,
                    "gamma": prior.greeks.gamma,
                    "vega": prior.greeks.vega,
                    "theta": prior.greeks.theta,
                    "rho": prior.greeks.rho,
                },
                prev_S=prior_regime.spot,
                prev_sigma=prior_regime.realised_vol_30d or 0.20,
                prev_r=prior_regime.risk_free_rate,
                curr_price=current_mark,
                curr_S=current_regime.spot,
                curr_sigma=current_regime.realised_vol_30d or 0.20,
                curr_r=current_regime.risk_free_rate,
                dt_days=1,
            )
        except Exception as exc:  # noqa: BLE001
            # Attribution is a nice-to-have — never let it break the assessment.
            attribution = None

        reshapes: list[ReshapeOption] = []
        # Option 1: close the position.
        reshapes.append(ReshapeOption(
            label="close",
            description=(
                f"Sell back at mid (${current_mark:,.0f}). Locks in {unrealized:+,.0f} "
                f"vs. inception. Use when client is satisfied with realised P&L."
            ),
            new_premium_estimate=0.0,
            delta_pnl_to_client=current_mark,
        ))
        # Option 2: roll — same structure, refresh expiry to today + original tenor.
        reshapes.append(ReshapeOption(
            label="roll",
            description=(
                f"Roll into a fresh structure of the same kind, re-struck at today's "
                f"spot of {current_regime.spot:.2f}. Net cash: monetise the existing "
                f"mark and pay the new premium; estimated cost similar to inception."
            ),
            new_premium_estimate=prior.net_premium,
            delta_pnl_to_client=current_mark - prior.net_premium,
        ))
        # Option 3: enhance (placeholder — Phase 2 swap with a Strategist sub-call).
        reshapes.append(ReshapeOption(
            label="enhance",
            description=(
                "Re-shape the structure: e.g. layer a short call to monetise upside, "
                "tighten a barrier, or convert vanilla to KO. Pricing requires re-quote."
            ),
            new_premium_estimate=float("nan"),
            delta_pnl_to_client=0.0,
        ))

        return LifecycleAssessment(
            candidate_id=prior.candidate.candidate_id,
            structure_name=prior.candidate.name,
            inception_premium=prior.net_premium,
            current_mark=current_mark,
            unrealized_pnl=unrealized,
            attribution=attribution,
            reshape_options=reshapes,
        )
