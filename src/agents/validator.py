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

Phase 1A objective-fit invariants (post-stress-test 2026-05-03):

  * Net premium ≤ budget + 10bps tolerance                                  [block]
  * Δ sign matches the client's view direction                              [block]
  * No short call leg when capped_upside_ok=False                           [block]
  * Neutral yield brief should not pick long-vol/short-theta structures     [warn]

Phase 3 will broaden coverage and add LLM-written remediation messages. For
now, a static remediation template per rule is enough.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Optional

from .base import BaseAgent
from .state import (
    Candidate,
    ClientObjective,
    Leg,
    MarketRegime,
    PricedCandidate,
    Severity,
    StructureKind,
    StructuringSession,
    ValidatorFinding,
    ValidatorReport,
)

# View → expected Δ sign. Maps the ClientObjective.view enum-values onto the
# Greeks the recommended structure should carry. `neutral` is the only view
# without a directional Δ constraint.
_BULLISH_VIEWS = {"bullish", "mildly_bullish"}
_BEARISH_VIEWS = {
    "bearish",
    "mildly_bearish",
    "protect_gains",
    "crash_hedge",
    "earnings_hedge",
}

# Slop tolerance on Δ — anything inside |0.05| is treated as flat for sign
# purposes. (Real bullish structures will have Δ well above 0.05; real bearish
# ones well below -0.05.)
_DELTA_SIGN_SLOP = 0.05

# Budget breach tolerance: 10bps of headroom over the stated budget so a
# 95bps recommended vs 90bps budget reads as "OK" but a 100bps+ breach blocks.
_BUDGET_TOLERANCE_BPS = 10.0

# Greeks tolerances for the neutral-yield consistency rule. A neutral
# yield-collecting brief should not be net long-vol (vega>>0) or net
# short-theta (theta<<0). The thresholds are deliberately conservative so we
# only fire when the structure is clearly directional in vol/theta space.
_NEUTRAL_YIELD_VEGA_MAX = 0.05
_NEUTRAL_YIELD_THETA_MIN = -0.005

logger = logging.getLogger(__name__)


# Tunables.
MAX_STRIKE_OVER_SPOT = 5.0
MAX_EXPIRY_DAYS = 5 * 365

# Phrases in a deal-analysis answer that indicate the corpus found no clean
# precedent for the proposed structure. Tuned conservatively — false positives
# are cheap (a Gate C caveat); false negatives are the failure mode we care
# about (an unusual structure shipping without a senior eye on it).
_NO_PRECEDENT_PATTERNS = [
    re.compile(r"\bno (?:clean |direct |close )?precedent\b", re.IGNORECASE),
    re.compile(r"\bno comparable\b", re.IGNORECASE),
    re.compile(r"\bunusual\b", re.IGNORECASE),
    re.compile(r"\batypical\b", re.IGNORECASE),
    re.compile(r"\boutlier\b", re.IGNORECASE),
    re.compile(r"\bnot seen recently\b", re.IGNORECASE),
]


class ValidatorAgent(BaseAgent):
    name = "ValidatorAgent"

    def __init__(self, mi: Optional[Any] = None) -> None:
        self.mi = mi

    def _run(self, session: StructuringSession) -> StructuringSession:
        report = ValidatorReport()
        if not session.priced or session.regime is None:
            session.validator = report
            return session

        for pc in session.priced:
            self._validate_candidate(pc.candidate, pc, session.regime, report)

        # Objective-fit invariants. These need session.objective and so live
        # outside the per-candidate rule list. Each rule emits findings tagged
        # with candidate_id so the Narrator's recommendation pass (which
        # filters validator findings by candidate_id) sees them on whichever
        # candidate(s) it ends up picking.
        if session.objective is not None:
            for pc in session.priced:
                self._validate_against_objective(
                    pc, session.objective, report
                )

        # Market-comparable check (RAG). Adds WARN findings for outlier
        # structures so they surface to the user at Gate C, but never BLOCK —
        # absence of precedent isn't an arbitrage failure.
        self._check_against_precedents(session, report)

        session.validator = report
        return session

    # ------------------------------------------------------------------
    # Objective-fit invariants (Phase 1A — stress-test 2026-05-03 fixes)
    # ------------------------------------------------------------------

    def _validate_against_objective(
        self,
        priced: PricedCandidate,
        objective: ClientObjective,
        report: ValidatorReport,
    ) -> None:
        for rule in (
            _rule_budget_breach,
            _rule_delta_sign_vs_view,
            _rule_capped_upside_contradiction,
            _rule_neutral_yield_consistency,
        ):
            try:
                rule(priced, objective, report)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Validator objective-fit rule crash on %s: %s",
                    priced.candidate.name,
                    exc,
                )
                report.findings.append(
                    ValidatorFinding(
                        name=getattr(rule, "__name__", "objective_rule_crash"),
                        severity=Severity.WARN,
                        message=f"Validator objective rule crashed: {exc}",
                        candidate_id=priced.candidate.candidate_id,
                    ),
                )

    # ------------------------------------------------------------------
    # Market intelligence (precedent comparison)
    # ------------------------------------------------------------------

    def _check_against_precedents(
        self,
        session: StructuringSession,
        report: ValidatorReport,
    ) -> None:
        if self.mi is None or session.objective is None or not session.priced:
            return
        underlying = session.objective.underlying

        def _query(pc: PricedCandidate):
            deal_summary = {
                "structure": pc.candidate.name,
                "kind": pc.candidate.kind.value,
                "underlying": underlying,
                "notional_usd": pc.candidate.notional_usd,
                "horizon_days": (
                    pc.candidate.legs[0].expiry_days if pc.candidate.legs else None
                ),
                "net_premium_bps": round(pc.net_premium_bps, 1),
                "delta": round(pc.greeks.delta, 3),
            }
            return pc, self.mi.query_deal_analysis(
                deal_summary=deal_summary,
                asset_class=underlying,
            )

        # Run all 3 candidates' deal-analysis MI calls in parallel.
        # Each is an LLM round-trip (1-3s typical, occasionally 30s+ on
        # retries). Sequential = ~10s; parallel = max(longest single call).
        results: dict[str, Any] = {}  # candidate_id -> qr
        with ThreadPoolExecutor(max_workers=min(len(session.priced), 3)) as pool:
            futures = {pool.submit(_query, pc): pc for pc in session.priced}
            for fut in as_completed(futures):
                pc = futures[fut]
                try:
                    _, qr = fut.result()
                    results[pc.candidate.candidate_id] = qr
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Validator MI query_deal_analysis failed for %s: %s",
                        pc.candidate.candidate_id,
                        exc,
                    )

        # Process results in priced[] order so memo citations and findings
        # appear in canonical order regardless of which thread finished first.
        for pc in session.priced:
            qr = results.get(pc.candidate.candidate_id)
            if qr is None:
                continue
            self._record_market_context(session, intent="deal_analysis", qr=qr)
            answer = getattr(qr, "answer", "") or ""
            if any(p.search(answer) for p in _NO_PRECEDENT_PATTERNS):
                report.findings.append(
                    ValidatorFinding(
                        name="market_precedent_outlier",
                        severity=Severity.WARN,
                        message=(
                            "Corpus shows no close precedent for this structure / underlier. "
                            "Senior structurer review recommended before quoting."
                        ),
                        candidate_id=pc.candidate.candidate_id,
                        remediation="Surface at Gate C; consider a more conventional shape.",
                    ),
                )

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


# ---------------------------------------------------------------------------
# Objective-fit invariants — operate on (PricedCandidate, ClientObjective).
# Tagged with candidate_id so the Narrator's recommendation pass and the
# validator-status column in the comparison table both see them on the
# correct candidate.
# ---------------------------------------------------------------------------


def _rule_budget_breach(
    priced: PricedCandidate,
    objective: ClientObjective,
    report: ValidatorReport,
) -> None:
    """Block recommendations whose net premium exceeds the stated budget by
    more than 10bps of tolerance.

    Sign convention: ``net_premium_bps > 0`` is a debit (client pays), < 0 is
    a credit (client receives). For debits we compare directly. For credits
    we only compare against budget when ``premium_tolerance == zero_cost_only``
    — in every other tolerance band a credit is welcome.
    """
    budget = getattr(objective, "budget_bps_notional", None)
    tolerance = getattr(objective, "premium_tolerance", None)
    # Skip the check if the objective doesn't carry a premium tolerance.
    if tolerance is None:
        return
    if budget is None:
        return

    bps = priced.net_premium_bps
    cap = float(budget) + _BUDGET_TOLERANCE_BPS

    if bps >= 0:
        # Debit structure — straightforward budget check.
        if bps > cap:
            _add(
                report,
                name="budget_breach",
                severity=Severity.BLOCK,
                message=(
                    f"Recommended structure premium {bps:.1f}bps exceeds budget "
                    f"{float(budget):.1f}bps by {bps - float(budget):.1f}bps "
                    f"(tolerance {_BUDGET_TOLERANCE_BPS:.0f}bps)."
                ),
                candidate_id=priced.candidate.candidate_id,
                remediation=(
                    "Re-strike, switch to a barrier variant, or pick the cheaper "
                    "sibling candidate."
                ),
            )
        return

    # Credit case (bps < 0). Only relevant under zero_cost_only — otherwise a
    # net credit is always within budget.
    if tolerance == "zero_cost_only":
        if abs(bps) > cap:
            _add(
                report,
                name="budget_breach",
                severity=Severity.BLOCK,
                message=(
                    f"Zero-cost mandate but structure shows {bps:.1f}bps net "
                    f"credit (|premium| {abs(bps):.1f}bps > {cap:.1f}bps cap)."
                ),
                candidate_id=priced.candidate.candidate_id,
                remediation=(
                    "Re-strike legs to balance to ~0bps; do not return a credit "
                    "when the brief is strictly zero-cost."
                ),
            )


def _rule_delta_sign_vs_view(
    priced: PricedCandidate,
    objective: ClientObjective,
    report: ValidatorReport,
) -> None:
    """Block recommendations whose net Δ contradicts the stated view.

    A bullish view requires Δ ≥ -slop; a bearish-family view requires
    Δ ≤ +slop. Anything inside |slop| is treated as flat for sign purposes.
    Neutral views have no directional Δ constraint.
    """
    view = getattr(objective, "view", None)
    if not view:
        return

    delta = priced.greeks.delta
    is_bullish = view in _BULLISH_VIEWS
    is_bearish = view in _BEARISH_VIEWS

    if is_bullish and delta < -_DELTA_SIGN_SLOP:
        _add(
            report,
            name="delta_sign_vs_view",
            severity=Severity.BLOCK,
            message=(
                f"Recommended structure has Δ={delta:.2f} which contradicts "
                f"{view} view."
            ),
            candidate_id=priced.candidate.candidate_id,
            remediation=(
                "Pick a long-call / call-spread / risk-reversal style instead "
                "of an upside-capping or short-Δ structure."
            ),
        )
    elif is_bearish and delta > _DELTA_SIGN_SLOP:
        _add(
            report,
            name="delta_sign_vs_view",
            severity=Severity.BLOCK,
            message=(
                f"Recommended structure has Δ={delta:.2f} which contradicts "
                f"{view} view."
            ),
            candidate_id=priced.candidate.candidate_id,
            remediation=(
                "Pick a long-put / put-spread / collar style instead of a "
                "long-Δ structure."
            ),
        )


def _rule_capped_upside_contradiction(
    priced: PricedCandidate,
    objective: ClientObjective,
    report: ValidatorReport,
) -> None:
    """Block recommendations that cap upside via a short call leg when the
    client refused upside caps (``capped_upside_ok == False``).
    """
    if getattr(objective, "capped_upside_ok", True):
        # Either True or unset/None — no constraint.
        return

    for leg in priced.candidate.legs:
        if leg.quantity < 0 and leg.option_type.endswith("_call"):
            _add(
                report,
                name="capped_upside_contradiction",
                severity=Severity.BLOCK,
                message=(
                    f"Recommended structure caps upside via short {leg.strike} "
                    f"call but client refused upside cap."
                ),
                candidate_id=priced.candidate.candidate_id,
                remediation=(
                    "Drop the short-call leg or replace the structure with a "
                    "long-only / long-spread variant."
                ),
            )
            return  # one finding per candidate is enough


def _rule_neutral_yield_consistency(
    priced: PricedCandidate,
    objective: ClientObjective,
    report: ValidatorReport,
) -> None:
    """Warn when a neutral yield-collecting brief gets a long-vol /
    short-theta recommendation. The intent of ``view=neutral`` plus
    medium/low premium tolerance is yield collection (covered call, short
    strangle, iron condor) — long-vol/short-theta structures are the opposite.
    """
    view = getattr(objective, "view", None)
    if view != "neutral":
        return
    tolerance = getattr(objective, "premium_tolerance", None)
    if tolerance not in {"medium", "low"}:
        return

    vega = priced.greeks.vega
    theta = priced.greeks.theta
    if vega > _NEUTRAL_YIELD_VEGA_MAX or theta < _NEUTRAL_YIELD_THETA_MIN:
        _add(
            report,
            name="neutral_yield_inconsistent",
            severity=Severity.WARN,
            message=(
                f"Neutral yield brief but recommended is long-vol "
                f"(vega={vega:.2f}) / short-theta (theta={theta:.4f}) — "
                f"opposite of yield-collecting structure."
            ),
            candidate_id=priced.candidate.candidate_id,
            remediation=(
                "Switch to a yield-collecting structure (covered call, short "
                "strangle, iron condor) — net short-vol, net long-theta."
            ),
        )
