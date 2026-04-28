"""ValidatorAgent — deterministic invariants on the priced candidates.

Phase 1 invariants (block = stop the pipeline; warn = surface at Gate C):

  * Strikes and barriers strictly positive and ≤ 5×spot                      [block]
  * Expiry days > 0 and ≤ 5y                                                  [block]
  * Put spread: K_long_put > K_short_put                                      [block]
  * Call spread: K_long_call < K_short_call                                   [block]
  * Barrier direction: down-and-out put → B < S; up-and-out call → B > S
                       down-and-in put  → B < S; up-and-in call  → B > S    [block]
  * Collar: long-put strike ≤ short-call strike                              [block]
  * Net premium > 0 for debit structures; ≈ 0 for ZCC                       [warn]
  * Greeks sign sanity (long put Δ ≤ 0, long call Δ ≥ 0, etc.)              [warn]

Phase 3 will broaden coverage and add LLM-written remediation messages. For
now, a static remediation template per rule is enough.
"""

from __future__ import annotations

import logging
from typing import Callable

from .base import BaseAgent
from .state import (
    Candidate,
    Leg,
    MarketRegime,
    PricedCandidate,
    Severity,
    StructureKind,
    StructuringSession,
    ValidatorFinding,
    ValidatorReport,
)

logger = logging.getLogger(__name__)


# Tunables.
MAX_STRIKE_OVER_SPOT = 5.0
MAX_EXPIRY_DAYS = 5 * 365


class ValidatorAgent(BaseAgent):
    name = "ValidatorAgent"

    def _run(self, session: StructuringSession) -> StructuringSession:
        report = ValidatorReport()
        if not session.priced or session.regime is None:
            session.validator = report
            return session

        for pc in session.priced:
            self._validate_candidate(pc.candidate, pc, session.regime, report)

        session.validator = report
        return session

    # ------------------------------------------------------------------
    # Per-candidate rule application
    # ------------------------------------------------------------------

    def _validate_candidate(
        self,
        cand: Candidate,
        priced: PricedCandidate,
        regime: MarketRegime,
        report: ValidatorReport,
    ) -> None:
        for rule in self._rules():
            try:
                rule(cand, priced, regime, report)
            except Exception as exc:  # noqa: BLE001 — never fail the validator on a rule bug
                logger.exception("Validator rule crash on %s: %s", cand.name, exc)
                report.findings.append(
                    ValidatorFinding(
                        name=getattr(rule, "__name__", "rule_crash"),
                        severity=Severity.WARN,
                        message=f"Validator rule crashed: {exc}",
                        candidate_id=cand.candidate_id,
                    ),
                )

    @staticmethod
    def _rules() -> list[Callable[[Candidate, PricedCandidate, MarketRegime, ValidatorReport], None]]:
        return [
            _rule_strike_bounds,
            _rule_expiry_bounds,
            _rule_put_spread_strikes,
            _rule_call_spread_strikes,
            _rule_barrier_direction,
            _rule_collar_strikes,
            _rule_premium_sign,
            _rule_greeks_signs,
            _rule_feasible,
        ]


# ---------------------------------------------------------------------------
# Individual rules — pure functions, side-effect on `report.findings`
# ---------------------------------------------------------------------------


def _add(
    report: ValidatorReport,
    *,
    name: str,
    severity: Severity,
    message: str,
    candidate_id: str,
    remediation: str | None = None,
) -> None:
    report.findings.append(
        ValidatorFinding(
            name=name,
            severity=severity,
            message=message,
            candidate_id=candidate_id,
            remediation=remediation,
        ),
    )


def _rule_strike_bounds(
    cand: Candidate, priced: PricedCandidate, regime: MarketRegime, report: ValidatorReport
) -> None:
    cap = MAX_STRIKE_OVER_SPOT * regime.spot
    for leg in cand.legs:
        if leg.strike <= 0:
            _add(report, name="strike_positive", severity=Severity.BLOCK,
                 message=f"Leg strike {leg.strike} not strictly positive.",
                 candidate_id=cand.candidate_id,
                 remediation="Strikes must be > 0.")
        if leg.strike > cap:
            _add(report, name="strike_sane_bound", severity=Severity.BLOCK,
                 message=f"Leg strike {leg.strike} > 5×spot ({cap:.2f}).",
                 candidate_id=cand.candidate_id,
                 remediation="Strike unrealistic given spot.")
        if leg.barrier_level is not None:
            if leg.barrier_level <= 0:
                _add(report, name="barrier_positive", severity=Severity.BLOCK,
                     message=f"Barrier {leg.barrier_level} not strictly positive.",
                     candidate_id=cand.candidate_id,
                     remediation="Barriers must be > 0.")
            if leg.barrier_level > cap:
                _add(report, name="barrier_sane_bound", severity=Severity.BLOCK,
                     message=f"Barrier {leg.barrier_level} > 5×spot.",
                     candidate_id=cand.candidate_id,
                     remediation="Barrier unrealistic given spot.")


def _rule_expiry_bounds(
    cand: Candidate, priced: PricedCandidate, regime: MarketRegime, report: ValidatorReport
) -> None:
    for leg in cand.legs:
        if leg.expiry_days <= 0:
            _add(report, name="expiry_positive", severity=Severity.BLOCK,
                 message="Expiry must be > 0 days.",
                 candidate_id=cand.candidate_id,
                 remediation="Re-set the leg expiry.")
        if leg.expiry_days > MAX_EXPIRY_DAYS:
            _add(report, name="expiry_sane_bound", severity=Severity.WARN,
                 message=f"Expiry {leg.expiry_days}d exceeds 5y horizon.",
                 candidate_id=cand.candidate_id,
                 remediation="Confirm the term is intended.")


def _rule_put_spread_strikes(
    cand: Candidate, priced: PricedCandidate, regime: MarketRegime, report: ValidatorReport
) -> None:
    if cand.kind != StructureKind.PUT_SPREAD:
        return
    longs = [l for l in cand.legs if "put" in l.option_type and l.quantity > 0]
    shorts = [l for l in cand.legs if "put" in l.option_type and l.quantity < 0]
    if not longs or not shorts:
        return
    K_long = max(l.strike for l in longs)
    K_short = max(l.strike for l in shorts)
    if K_long <= K_short:
        _add(report, name="put_spread_strike_order", severity=Severity.BLOCK,
             message=f"Put spread requires long-strike > short-strike, got {K_long} vs {K_short}.",
             candidate_id=cand.candidate_id,
             remediation="Swap the strikes or the long/short legs.")


def _rule_call_spread_strikes(
    cand: Candidate, priced: PricedCandidate, regime: MarketRegime, report: ValidatorReport
) -> None:
    if cand.kind != StructureKind.CALL_SPREAD:
        return
    longs = [l for l in cand.legs if "call" in l.option_type and l.quantity > 0]
    shorts = [l for l in cand.legs if "call" in l.option_type and l.quantity < 0]
    if not longs or not shorts:
        return
    K_long = min(l.strike for l in longs)
    K_short = min(l.strike for l in shorts)
    if K_long >= K_short:
        _add(report, name="call_spread_strike_order", severity=Severity.BLOCK,
             message=f"Call spread requires long-strike < short-strike, got {K_long} vs {K_short}.",
             candidate_id=cand.candidate_id,
             remediation="Swap the strikes or the long/short legs.")


def _rule_barrier_direction(
    cand: Candidate, priced: PricedCandidate, regime: MarketRegime, report: ValidatorReport
) -> None:
    spot = regime.spot
    for leg in cand.legs:
        if not leg.option_type.startswith(("knockout_", "knockin_")):
            continue
        if leg.barrier_level is None:
            _add(report, name="barrier_required", severity=Severity.BLOCK,
                 message=f"{leg.option_type} requires a barrier_level.",
                 candidate_id=cand.candidate_id,
                 remediation="Set the leg's barrier_level.")
            continue
        # Convention: put → barrier below spot (down-and-out / down-and-in).
        #             call → barrier above spot (up-and-out / up-and-in).
        is_put = leg.option_type.endswith("_put")
        if is_put and leg.barrier_level >= spot:
            _add(report, name="barrier_direction_put", severity=Severity.BLOCK,
                 message=(
                     f"Put barrier ({leg.barrier_level}) must be < spot ({spot}); "
                     "down-and-out/in only."
                 ),
                 candidate_id=cand.candidate_id,
                 remediation="Move the barrier below spot.")
        if not is_put and leg.barrier_level <= spot:
            _add(report, name="barrier_direction_call", severity=Severity.BLOCK,
                 message=(
                     f"Call barrier ({leg.barrier_level}) must be > spot ({spot}); "
                     "up-and-out/in only."
                 ),
                 candidate_id=cand.candidate_id,
                 remediation="Move the barrier above spot.")


def _rule_collar_strikes(
    cand: Candidate, priced: PricedCandidate, regime: MarketRegime, report: ValidatorReport
) -> None:
    if cand.kind not in (StructureKind.COLLAR, StructureKind.ZERO_COST_COLLAR):
        return
    put_leg = next((l for l in cand.legs if "put" in l.option_type and l.quantity > 0), None)
    call_leg = next((l for l in cand.legs if "call" in l.option_type and l.quantity < 0), None)
    if not put_leg or not call_leg:
        return
    if put_leg.strike > call_leg.strike:
        _add(report, name="collar_strike_order", severity=Severity.BLOCK,
             message=(
                 f"Collar long-put strike ({put_leg.strike}) cannot exceed "
                 f"short-call strike ({call_leg.strike})."
             ),
             candidate_id=cand.candidate_id,
             remediation="Tighten the put strike or widen the call strike.")


def _rule_premium_sign(
    cand: Candidate, priced: PricedCandidate, regime: MarketRegime, report: ValidatorReport
) -> None:
    debit_kinds = {
        StructureKind.LONG_PUT, StructureKind.LONG_CALL, StructureKind.PUT_SPREAD,
        StructureKind.CALL_SPREAD, StructureKind.KO_PUT, StructureKind.KI_PUT,
        StructureKind.KO_CALL, StructureKind.KI_CALL, StructureKind.COLLAR,
        StructureKind.PUT_SPREAD_COLLAR,
    }
    credit_kinds = {StructureKind.COVERED_CALL}
    zero_cost_kinds = {StructureKind.ZERO_COST_COLLAR}

    bps = priced.net_premium_bps
    if cand.kind in zero_cost_kinds:
        if abs(bps) > 25:  # 25bps tolerance for "zero cost"
            _add(report, name="zcc_premium", severity=Severity.WARN,
                 message=f"Zero-cost collar net premium = {bps:.1f}bps (target ~0).",
                 candidate_id=cand.candidate_id,
                 remediation="Re-strike one leg to balance the premium.")
    elif cand.kind in debit_kinds:
        if bps <= 0:
            _add(report, name="debit_premium", severity=Severity.WARN,
                 message=f"Debit structure shows non-positive premium ({bps:.1f}bps).",
                 candidate_id=cand.candidate_id,
                 remediation="Re-check leg quantities and strikes.")
    elif cand.kind in credit_kinds:
        if bps >= 0:
            _add(report, name="credit_premium", severity=Severity.WARN,
                 message=f"Credit structure shows non-negative premium ({bps:.1f}bps).",
                 candidate_id=cand.candidate_id,
                 remediation="Confirm short-leg quantity is signed correctly.")


def _rule_greeks_signs(
    cand: Candidate, priced: PricedCandidate, regime: MarketRegime, report: ValidatorReport
) -> None:
    g = priced.greeks
    # Sanity envelopes (per-share-equivalent, post-quantity aggregation).
    # Long put expressions should have non-positive net delta if the put is dominant.
    if cand.kind == StructureKind.LONG_PUT and g.delta > 0.05:
        _add(report, name="long_put_delta_sign", severity=Severity.WARN,
             message=f"Long put net delta is +{g.delta:.3f} (expected ≤ 0).",
             candidate_id=cand.candidate_id,
             remediation="Verify leg quantities; long put should have negative delta.")
    if cand.kind == StructureKind.LONG_CALL and g.delta < -0.05:
        _add(report, name="long_call_delta_sign", severity=Severity.WARN,
             message=f"Long call net delta is {g.delta:.3f} (expected ≥ 0).",
             candidate_id=cand.candidate_id,
             remediation="Verify leg quantities.")


def _rule_feasible(
    cand: Candidate, priced: PricedCandidate, regime: MarketRegime, report: ValidatorReport
) -> None:
    if priced.feasible:
        return
    msg = "; ".join(priced.feasibility_notes) or "Engine could not price this structure."
    _add(report, name="engine_feasibility", severity=Severity.BLOCK,
         message=msg,
         candidate_id=cand.candidate_id,
         remediation="Re-shape the structure or pick a different candidate.")
