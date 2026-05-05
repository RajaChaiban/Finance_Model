"""ScenarioAgent — client-outcome scenarios per priced candidate.

Phase 1: re-prices each candidate under three deterministic shocks (rally,
crash, vol-spike) and reports the *client's total P&L* — i.e. the change in
the underlying position plus the change in the structure value. Senior
structurers want to see the protection in dollars: "if SPY drops 20%, the
client is down $2.4M after the collar kicks in" is the artifact.

This is NOT a hedge plan. We do not size delta/gamma/vega hedges. The scope
is narrow on purpose: structuring + trading work, not the linear desk.

Phase 3 will add: earnings-gap scenario, time-decay scenario, 5-year backtest,
LLM-written commentary, and a richer hedgeability flag.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any, Optional

from .base import BaseAgent
from .pricing import PricingAgent
from .state import (
    Candidate,
    MarketRegime,
    PricedCandidate,
    ScenarioReport,
    ScenarioRow,
    StructuringSession,
)

logger = logging.getLogger(__name__)


# Phase 1 scenario library — small, deterministic, demoable. These are the
# default trio every session sees.
_SCENARIOS = [
    {
        "name": "Rally +15%",
        "description": "Sustained 15% rally with vol crush.",
        "spot_shock_pct": 0.15,
        "vol_shock_pct": -0.30,
        "rate_shock_abs": 0.0,
    },
    {
        "name": "Crash -20%",
        "description": "Sharp 20% drawdown with vol doubling.",
        "spot_shock_pct": -0.20,
        "vol_shock_pct": 1.00,
        "rate_shock_abs": -0.005,
    },
    {
        "name": "Vol Spike +50%",
        "description": "Vol jumps 50% with no spot move.",
        "spot_shock_pct": 0.0,
        "vol_shock_pct": 0.50,
        "rate_shock_abs": 0.0,
    },
]


def _historical_scenarios() -> list[dict]:
    """Pull the historical-crisis scenarios from `src.scenarios.engine`.

    Wired in Phase 7 so a single source of truth exists across the CLI
    scenario report and the agent's per-candidate scenarios. Lazy import
    keeps the agents module independent of the CLI scenarios package at
    import time.
    """
    try:
        from src.scenarios.engine import ScenarioLibrary
    except Exception:  # noqa: BLE001
        return []
    out = []
    for key in ("crisis_2008", "covid_crash", "flash_crash"):
        try:
            sc = ScenarioLibrary.get(key)
        except ValueError:
            continue
        out.append({
            "name": sc.name,
            "description": sc.description,
            "spot_shock_pct": float(sc.spot_shock),
            # ScenarioLibrary expresses vol shocks as multiplicative ratios
            # (1.0 = vol doubles); ScenarioAgent expects fractional change
            # (0.5 = vol +50%, -0.3 = vol −30%). Same convention shift.
            "vol_shock_pct": float(sc.vol_shock),
            "rate_shock_abs": float(sc.rate_shock),
        })
    return out


class ScenarioAgent(BaseAgent):
    name = "ScenarioAgent"

    def __init__(self, mi: Optional[Any] = None, include_historical: bool = True) -> None:
        # Internal pricer doesn't need MI — scenario shocks aren't a place
        # to call out to the corpus on every shocked re-price.
        self._pricer = PricingAgent()
        self.mi = mi
        # Phase 7: when True, append the historical-crisis library to every
        # session's scenarios. Default True; tests can disable via flag.
        self._include_historical = include_historical

    @property
    def _scenarios(self) -> list[dict]:
        if self._include_historical:
            return _SCENARIOS + _historical_scenarios()
        return list(_SCENARIOS)

    def _run(self, session: StructuringSession) -> StructuringSession:
        if session.regime is None or not session.priced:
            session.scenarios = []
            return session

        reports: list[ScenarioReport] = []
        for pc in session.priced:
            reports.append(self._scenario_report(pc, session.regime))
        session.scenarios = reports

        # One MI call per session asking the corpus how comparable structures
        # behaved historically under the shock regime we just modelled.
        self._enrich_with_history(session, reports)
        return session

    # ------------------------------------------------------------------
    # Market intelligence (free-form historical scenario context)
    # ------------------------------------------------------------------

    def _enrich_with_history(
        self,
        session: StructuringSession,
        reports: list[ScenarioReport],
    ) -> None:
        if self.mi is None or session.objective is None or not reports:
            return
        underlying = session.objective.underlying
        # Take the worst scenario across all candidates as the framing question.
        worst_pnl = 0.0
        worst_label = ""
        for rep in reports:
            for row in rep.scenarios:
                if row.pnl_usd < worst_pnl:
                    worst_pnl = row.pnl_usd
                    worst_label = row.name
        question = (
            f"Under a {worst_label or 'stress'} scenario for {underlying}, "
            "how have comparable hedged structures historically performed? "
            "What hedging-cost slippage is typical when vol spikes alongside spot moves?"
        )
        try:
            qr = self.mi.general_query(query=question, asset_class=underlying)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Scenario MI general_query failed: %s", exc)
            return
        self._record_market_context(session, intent="general", qr=qr)

    # ------------------------------------------------------------------
    # Per-candidate
    # ------------------------------------------------------------------

    def _scenario_report(
        self, priced: PricedCandidate, regime: MarketRegime
    ) -> ScenarioReport:
        rows: list[ScenarioRow] = []
        original = priced.net_premium  # USD

        for sc in self._scenarios:
            shocked = self._shock_regime(regime, sc)
            try:
                stressed = self._pricer._price_candidate(  # noqa: SLF001 — re-use
                    priced.candidate, shocked
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Scenario %s failed for %s: %s", sc["name"], priced.candidate.name, exc
                )
                continue

            structure_pnl = stressed.net_premium - original
            underlying_pnl = priced.candidate.notional_usd * sc["spot_shock_pct"]
            total_pnl = underlying_pnl + structure_pnl

            rows.append(
                ScenarioRow(
                    name=sc["name"],
                    description=sc["description"],
                    spot_shock_pct=sc["spot_shock_pct"],
                    vol_shock_pct=sc["vol_shock_pct"],
                    rate_shock_abs=sc["rate_shock_abs"],
                    pnl_usd=total_pnl,
                    pnl_pct_notional=total_pnl / priced.candidate.notional_usd if priced.candidate.notional_usd else 0.0,
                ),
            )

        ok, reason = self._hedgeability(priced, regime)

        return ScenarioReport(
            candidate_id=priced.candidate.candidate_id,
            scenarios=rows,
            hedgeability_ok=ok,
            hedgeability_reason=reason,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _shock_regime(regime: MarketRegime, sc: dict) -> MarketRegime:
        shocked = deepcopy(regime)
        shocked.spot = regime.spot * (1.0 + sc["spot_shock_pct"])
        # vol_shock is multiplicative (e.g. +1.0 = 2x)
        if regime.realised_vol_30d:
            shocked.realised_vol_30d = max(
                0.05, regime.realised_vol_30d * (1.0 + sc["vol_shock_pct"])
            )
        if regime.realised_vol_90d:
            shocked.realised_vol_90d = max(
                0.05, regime.realised_vol_90d * (1.0 + sc["vol_shock_pct"])
            )
        if regime.atm_iv:
            shocked.atm_iv = max(0.05, regime.atm_iv * (1.0 + sc["vol_shock_pct"]))
        shocked.risk_free_rate = max(0.001, regime.risk_free_rate + sc["rate_shock_abs"])
        return shocked

    @staticmethod
    def _hedgeability(priced: PricedCandidate, regime: MarketRegime) -> tuple[bool, str]:
        """Phase 1 heuristic: barriers too close to spot are tough to hedge,
        and an infeasible structure is by definition un-hedgeable.
        """
        if not priced.feasible:
            return False, "Engine could not price one or more legs."

        for leg in priced.candidate.legs:
            if leg.barrier_level is None:
                continue
            distance_pct = abs(regime.spot - leg.barrier_level) / regime.spot
            if distance_pct < 0.05:
                return (
                    False,
                    f"Barrier within 5% of spot ({distance_pct:.1%}); pin risk too high to quote.",
                )
        return True, "Listed strikes deep, barriers wide. Standard hedging cost assumed."
