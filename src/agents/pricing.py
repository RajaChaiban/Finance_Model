"""PricingAgent — turns Candidates into PricedCandidates.

Thin wrapper around `src/engines/router.route()`. No LLM. For each leg of each
candidate it calls the right pricer + greeks function with the regime's scalar
σ (Phase 1; vol-surface lands in Phase 2). Aggregates per-leg numbers into a
structure-level summary and computes max-loss / max-gain / breakeven for
analytically tractable structures.

Quantity convention: leg.quantity = +1 long / -1 short (per "unit"). The
candidate's notional_usd carries the size. Aggregated Greeks are kept in
per-share units (matching the existing engines); USD scaling happens at
memo time.
"""

from __future__ import annotations

import logging
from typing import Optional

from src.engines import router

from .base import AgentError, BaseAgent
from .state import (
    Candidate,
    GreeksSnapshot,
    Leg,
    MarketRegime,
    PricedCandidate,
    StructureKind,
    StructuringSession,
)

logger = logging.getLogger(__name__)


class PricingAgent(BaseAgent):
    name = "PricingAgent"

    def _run(self, session: StructuringSession) -> StructuringSession:
        if not session.candidates:
            raise AgentError("No candidates to price.")
        if session.regime is None:
            raise AgentError("Cannot price without a MarketRegime.")

        priced: list[PricedCandidate] = []
        for cand in session.candidates:
            priced.append(self._price_candidate(cand, session.regime))

        session.priced = priced
        return session

    # ------------------------------------------------------------------
    # Per-candidate
    # ------------------------------------------------------------------

    def _price_candidate(self, cand: Candidate, regime: MarketRegime) -> PricedCandidate:
        sigma = self._pick_sigma(regime)

        per_leg_prices: list[float] = []
        net_price_per_unit = 0.0  # signed sum across legs, per-share units
        net_greeks = GreeksSnapshot()
        method_label = ""
        feasibility_notes: list[str] = []
        feasible = True

        for leg in cand.legs:
            try:
                price, greeks, method = self._price_leg(leg, regime, sigma)
            except Exception as exc:  # noqa: BLE001 — engine boundary
                feasible = False
                feasibility_notes.append(
                    f"Engine failure on {leg.option_type} K={leg.strike}: {exc}"
                )
                continue

            per_leg_prices.append(price)
            net_price_per_unit += leg.quantity * price
            net_greeks.delta += leg.quantity * float(greeks.get("delta", 0.0))
            net_greeks.gamma += leg.quantity * float(greeks.get("gamma", 0.0))
            net_greeks.vega += leg.quantity * float(greeks.get("vega", 0.0))
            net_greeks.theta += leg.quantity * float(greeks.get("theta", 0.0))
            rho = float(greeks.get("rho", 0.0))
            net_greeks.rho += leg.quantity * rho
            method_label = method  # last engine wins; usually all legs share

        # Apply hedging-cost premium (bps of notional, signed only on debit side).
        if cand.hedging_cost_premium_bps and net_price_per_unit > 0:
            adj = (cand.hedging_cost_premium_bps / 10000.0) * regime.spot
            net_price_per_unit += adj

        # USD net premium = net_price_per_unit * (notional / spot)
        scale = cand.notional_usd / regime.spot if regime.spot > 0 else 0.0
        net_premium_usd = net_price_per_unit * scale
        net_premium_bps = (net_price_per_unit / regime.spot * 10000.0) if regime.spot > 0 else 0.0

        # DV01 = rho per 1bp (rho is per 1% by convention -> /100).
        net_greeks.dv01 = net_greeks.rho / 100.0

        max_loss, max_gain, breakeven = self._summary_pnl(
            cand, regime, net_price_per_unit
        )

        return PricedCandidate(
            candidate=cand,
            net_premium=net_premium_usd,
            net_premium_bps=net_premium_bps,
            greeks=net_greeks,
            per_leg_prices=per_leg_prices,
            method_label=method_label,
            max_loss_usd=max_loss * scale if max_loss is not None else None,
            max_gain_usd=max_gain * scale if max_gain is not None else None,
            breakeven=breakeven,
            feasible=feasible,
            feasibility_notes=feasibility_notes,
        )

    # ------------------------------------------------------------------
    # Per-leg
    # ------------------------------------------------------------------

    @staticmethod
    def _price_leg(
        leg: Leg, regime: MarketRegime, sigma: float
    ) -> tuple[float, dict, str]:
        pricer, greeks_fn, method = router.route(leg.option_type)
        T = leg.expiry_days / 365.0

        kwargs = dict(
            S=regime.spot,
            K=leg.strike,
            r=regime.risk_free_rate,
            sigma=sigma,
            T=T,
            q=regime.dividend_yield,
        )
        if leg.option_type.startswith(("knockout_", "knockin_")):
            kwargs["barrier_level"] = leg.barrier_level
            kwargs["monitoring"] = leg.barrier_monitoring

        price, _std_err, _paths = pricer(**kwargs)
        greeks = greeks_fn(**kwargs)
        return float(price), greeks, method

    # ------------------------------------------------------------------
    # Summary statistics
    # ------------------------------------------------------------------

    @staticmethod
    def _pick_sigma(regime: MarketRegime) -> float:
        if regime.atm_iv:
            return regime.atm_iv
        if regime.realised_vol_30d:
            return regime.realised_vol_30d
        if regime.realised_vol_90d:
            return regime.realised_vol_90d
        return 0.20  # last-ditch sane default

    @staticmethod
    def _summary_pnl(
        cand: Candidate, regime: MarketRegime, net_price_per_unit: float
    ) -> tuple[Optional[float], Optional[float], Optional[list[float]]]:
        """Return (max_loss_per_unit, max_gain_per_unit, breakevens) at expiry,
        ignoring path-dependence. None for analytically intractable structures.
        Loss expressed as a positive number (i.e. abs value of the worst case).
        """
        spot = regime.spot
        kind = cand.kind
        legs = cand.legs

        if kind == StructureKind.LONG_PUT and len(legs) == 1:
            K = legs[0].strike
            return net_price_per_unit, K - net_price_per_unit, [K - net_price_per_unit]

        if kind == StructureKind.LONG_CALL and len(legs) == 1:
            K = legs[0].strike
            return net_price_per_unit, None, [K + net_price_per_unit]  # unbounded gain

        if kind == StructureKind.PUT_SPREAD and len(legs) == 2:
            longs = [l for l in legs if l.quantity > 0]
            shorts = [l for l in legs if l.quantity < 0]
            if len(longs) == 1 and len(shorts) == 1:
                K_long = longs[0].strike
                K_short = shorts[0].strike
                width = K_long - K_short
                return net_price_per_unit, width - net_price_per_unit, [
                    K_long - net_price_per_unit,
                ]

        if kind in (StructureKind.COLLAR, StructureKind.ZERO_COST_COLLAR) and len(legs) == 2:
            put_leg = next((l for l in legs if "put" in l.option_type), None)
            call_leg = next((l for l in legs if "call" in l.option_type), None)
            if put_leg and call_leg:
                K_put = put_leg.strike
                K_call = call_leg.strike
                # Loss vs. the underlying long stock if S → K_put (worst protected).
                # Bounded gain if S → K_call (capped).
                max_loss = (spot - K_put) + net_price_per_unit
                max_gain = (K_call - spot) - net_price_per_unit
                return max_loss, max_gain, None

        if kind == StructureKind.COVERED_CALL and len(legs) == 1:
            K = legs[0].strike
            # Loss is the long stock loss less the premium received (cap not on loss).
            # Gain capped at K + premium.
            return None, (K - spot) - net_price_per_unit, None

        return None, None, None
