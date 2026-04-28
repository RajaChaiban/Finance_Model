"""Strategist's product-selection rules table.

Each row maps a `(view, horizon_band, budget_band, vol_regime, barrier_appetite)`
fingerprint to a ranked top-3 list of structures plus a rationale template.

Structure factories at the bottom of the file build concrete `Candidate`s
from `(objective, regime)` by sizing strikes/barriers off the spot price.
The factories are deliberately simple — Phase 2's LLM-driven Strategist
adapts them; Phase 1 ships exactly what they produce.

This is the IP of the platform. Senior structurers should be able to read
this file and recognise the desk's rulebook.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from ..state import Candidate, ClientObjective, Leg, MarketRegime, StructureKind


# ---------------------------------------------------------------------------
# Rule row definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleRow:
    """One row of the desk rulebook."""

    view: str                              # bearish | mildly_bearish | neutral | ...
    horizon_band: str                      # short (<=30d) | mid (1-6m) | long (>6m)
    budget_band: str                       # zero | low (<50bps) | mid (50-150) | high (>150) | credit
    vol_regime: str                        # any | low | normal | high | very_high
    barrier_appetite: str                  # any | yes | no
    structures: tuple[StructureKind, ...]  # ranked top-3
    rationale: str                         # template; %{slot}s allowed


# ---------------------------------------------------------------------------
# THE RULES TABLE — 10 rows covering the demo space
# ---------------------------------------------------------------------------


RULES: tuple[RuleRow, ...] = (
    RuleRow(
        view="bearish",
        horizon_band="mid",
        budget_band="mid",
        vol_regime="normal",
        barrier_appetite="no",
        structures=(StructureKind.PUT_SPREAD, StructureKind.ZERO_COST_COLLAR, StructureKind.LONG_PUT),
        rationale=(
            "Bearish view with a mid-horizon and a normal vol regime: a put spread is "
            "cost-efficient downside without paying full premium for tail risk; ZCC "
            "is a strong alternative if the client will give up upside above {short_call_pct:.0%}; "
            "outright long put is the simplest expression but the most expensive."
        ),
    ),
    RuleRow(
        view="protect_gains",
        horizon_band="mid",
        budget_band="zero",
        vol_regime="any",
        barrier_appetite="no",
        structures=(StructureKind.ZERO_COST_COLLAR, StructureKind.COLLAR, StructureKind.COVERED_CALL),
        rationale=(
            "Locking in gains zero-cost: ZCC funds a {put_pct:.0%}-OTM put by selling a "
            "{call_pct:.0%}-OTM call, capping upside but eliminating premium outlay. Standard "
            "collar is the same with a small net debit/credit if strikes don't perfectly "
            "balance. Covered call is a half-hedge if the client cares more about yield."
        ),
    ),
    RuleRow(
        view="bearish",
        horizon_band="mid",
        budget_band="zero",
        vol_regime="any",
        barrier_appetite="yes",
        structures=(StructureKind.ZERO_COST_COLLAR, StructureKind.PUT_SPREAD_COLLAR, StructureKind.COLLAR),
        rationale=(
            "Zero-cost bearish hedge with barrier appetite: ZCC is the workhorse; the "
            "put-spread collar narrows the protection window in exchange for a higher "
            "long-put strike; standard collar is the conservative fallback."
        ),
    ),
    RuleRow(
        view="bearish",
        horizon_band="short",
        budget_band="low",
        vol_regime="high",
        barrier_appetite="no",
        structures=(StructureKind.PUT_SPREAD, StructureKind.KO_PUT, StructureKind.LONG_PUT),
        rationale=(
            "Short-horizon bearish in high vol — long premium is expensive, so a put "
            "spread caps cost. KO put cheapens further if the client doesn't expect a "
            "violent overshoot; outright long put is the safest but most expensive."
        ),
    ),
    RuleRow(
        view="bearish",
        horizon_band="long",
        budget_band="high",
        vol_regime="low",
        barrier_appetite="no",
        structures=(StructureKind.LONG_PUT, StructureKind.PUT_SPREAD, StructureKind.COLLAR),
        rationale=(
            "Long-horizon bearish in low vol: vol is cheap, so outright long puts are "
            "the cleanest expression; put spread is the cost-control alternative; "
            "collar caps upside but funds the put."
        ),
    ),
    RuleRow(
        view="mildly_bearish",
        horizon_band="mid",
        budget_band="low",
        vol_regime="normal",
        barrier_appetite="yes",
        structures=(StructureKind.KO_PUT, StructureKind.PUT_SPREAD, StructureKind.ZERO_COST_COLLAR),
        rationale=(
            "Mild bearish bias and barrier-tolerant: a KO put is materially cheaper "
            "than vanilla because the barrier knocks out the worst tail; put spread is "
            "the no-barrier alternative; ZCC if zero-cost is required."
        ),
    ),
    RuleRow(
        view="neutral",
        horizon_band="short",
        budget_band="credit",
        vol_regime="high",
        barrier_appetite="no",
        structures=(StructureKind.COVERED_CALL, StructureKind.PUT_SPREAD, StructureKind.RISK_REVERSAL),
        rationale=(
            "Neutral / income view in high vol — sell premium. Covered call is the "
            "obvious income trade; a credit put spread monetises elevated put-wing IV; "
            "risk reversal expresses a mildly bullish skew view."
        ),
    ),
    RuleRow(
        view="bullish",
        horizon_band="mid",
        budget_band="credit",
        vol_regime="normal",
        barrier_appetite="no",
        structures=(StructureKind.RISK_REVERSAL, StructureKind.COVERED_CALL, StructureKind.LONG_CALL),
        rationale=(
            "Bullish with appetite to sell premium: risk reversal (long call / short "
            "put) expresses directional view financed by put sale; covered call yields "
            "but caps upside; outright long call is the unleveraged alternative."
        ),
    ),
    RuleRow(
        view="crash_hedge",
        horizon_band="short",
        budget_band="low",
        vol_regime="high",
        barrier_appetite="yes",
        structures=(StructureKind.KO_PUT, StructureKind.LONG_PUT, StructureKind.PUT_SPREAD),
        rationale=(
            "Tail-risk hedge: KO put is the cheapest insurance if the barrier can be "
            "set wide enough to survive a real crash; outright long put is the safer "
            "tail hedge but more expensive; put spread is the bounded-cost compromise."
        ),
    ),
    RuleRow(
        view="earnings_hedge",
        horizon_band="short",
        budget_band="low",
        vol_regime="very_high",
        barrier_appetite="no",
        structures=(StructureKind.PUT_SPREAD, StructureKind.LONG_PUT, StructureKind.KI_PUT),
        rationale=(
            "Earnings hedge in very-high IV: outright long puts are too expensive — a "
            "tight put spread caps the cost and IV crush after print kills the long "
            "alone; KI put is the cheaper alternative if the client believes a real "
            "drawdown is needed before protection kicks in."
        ),
    ),
)


# ---------------------------------------------------------------------------
# Banding helpers — translate ClientObjective into rule-row dimensions
# ---------------------------------------------------------------------------


def _horizon_band(days: int) -> str:
    if days <= 30:
        return "short"
    if days <= 180:
        return "mid"
    return "long"


def _budget_band(bps: float, premium_tol: str) -> str:
    if premium_tol == "zero_cost_only" or bps <= 0:
        return "zero"
    if premium_tol == "credit" or bps < 0:
        return "credit"
    if bps < 50:
        return "low"
    if bps <= 150:
        return "mid"
    return "high"


def _matches(rule: RuleRow, obj: ClientObjective, regime: MarketRegime) -> bool:
    if rule.view != obj.view:
        return False
    if rule.horizon_band != _horizon_band(obj.horizon_days):
        return False
    if rule.budget_band != _budget_band(obj.budget_bps_notional, obj.premium_tolerance):
        return False
    if rule.vol_regime != "any" and rule.vol_regime != regime.vol_regime:
        return False
    if rule.barrier_appetite != "any":
        wants_barrier = rule.barrier_appetite == "yes"
        if obj.barrier_appetite != wants_barrier:
            return False
    return True


def match_rules(obj: ClientObjective, regime: MarketRegime) -> RuleRow:
    """Find the best-matching rule. Falls back through progressively looser
    matches so the strategist always returns something.
    """

    # 1. Strict match on all dimensions.
    for rule in RULES:
        if _matches(rule, obj, regime):
            return rule

    # 2. Drop barrier_appetite — accept the row's recommendation either way.
    for rule in RULES:
        if (
            rule.view == obj.view
            and rule.horizon_band == _horizon_band(obj.horizon_days)
            and rule.budget_band == _budget_band(obj.budget_bps_notional, obj.premium_tolerance)
            and (rule.vol_regime == "any" or rule.vol_regime == regime.vol_regime)
        ):
            return rule

    # 3. Drop vol_regime.
    for rule in RULES:
        if (
            rule.view == obj.view
            and rule.horizon_band == _horizon_band(obj.horizon_days)
            and rule.budget_band == _budget_band(obj.budget_bps_notional, obj.premium_tolerance)
        ):
            return rule

    # 4. Drop budget — match view + horizon.
    for rule in RULES:
        if rule.view == obj.view and rule.horizon_band == _horizon_band(obj.horizon_days):
            return rule

    # 5. Drop horizon — view-only match.
    for rule in RULES:
        if rule.view == obj.view:
            return rule

    # 6. Last resort — protective default.
    return RULES[0]


# ---------------------------------------------------------------------------
# Structure factories
# ---------------------------------------------------------------------------


# Default OTM-ness for protective structures. Senior structurer judgement.
_PUT_OTM_PCT_DEFAULT = 0.05      # 5% OTM put
_CALL_OTM_PCT_DEFAULT = 0.08     # 8% OTM short call (collars / covered calls)
_KO_BARRIER_PCT_DEFAULT = 0.20   # 20% below spot for down-and-out put
_PUT_SPREAD_WIDTH_DEFAULT = 0.10 # 10% of spot wide
_KI_BARRIER_PCT_DEFAULT = 0.15   # 15% below spot for KI activation


def _round_strike(x: float) -> float:
    """Round to a sensible listed-strike grid: $1 increments under $50, $5 above."""
    if x < 50:
        return round(x, 1)
    if x < 200:
        return round(x)
    return float(round(x / 5) * 5)


def _build_long_put(obj: ClientObjective, regime: MarketRegime) -> Candidate:
    K = _round_strike(regime.spot * (1 - _PUT_OTM_PCT_DEFAULT))
    leg = Leg(
        option_type="european_put",
        strike=K,
        expiry_days=obj.horizon_days,
        quantity=+1.0,
        role="long_put_protective",
    )
    return Candidate(
        kind=StructureKind.LONG_PUT,
        name=f"Long {_PUT_OTM_PCT_DEFAULT:.0%}-OTM European Put",
        legs=[leg],
        rationale="Outright protection. Highest premium, simplest payoff, no caps.",
        notional_usd=obj.notional_usd,
        hedging_cost_premium_bps=2.0,
    )


def _build_long_call(obj: ClientObjective, regime: MarketRegime) -> Candidate:
    K = _round_strike(regime.spot * (1 + 0.03))
    leg = Leg(
        option_type="european_call",
        strike=K,
        expiry_days=obj.horizon_days,
        quantity=+1.0,
        role="long_call_directional",
    )
    return Candidate(
        kind=StructureKind.LONG_CALL,
        name="Long 3%-OTM European Call",
        legs=[leg],
        rationale="Outright bullish expression. No leverage from premium financing.",
        notional_usd=obj.notional_usd,
        hedging_cost_premium_bps=2.0,
    )


def _build_put_spread(obj: ClientObjective, regime: MarketRegime, *, debit: bool = True) -> Candidate:
    K_long = _round_strike(regime.spot * (1 - _PUT_OTM_PCT_DEFAULT))
    K_short = _round_strike(K_long - regime.spot * _PUT_SPREAD_WIDTH_DEFAULT)
    long_qty = +1.0 if debit else -1.0
    short_qty = -1.0 if debit else +1.0
    legs = [
        Leg(option_type="european_put", strike=K_long, expiry_days=obj.horizon_days,
            quantity=long_qty, role="long_put_protective"),
        Leg(option_type="european_put", strike=K_short, expiry_days=obj.horizon_days,
            quantity=short_qty, role="short_put_finance"),
    ]
    return Candidate(
        kind=StructureKind.PUT_SPREAD,
        name=f"Put Spread {K_long:.0f}/{K_short:.0f}",
        legs=legs,
        rationale=(
            f"Protection from {K_long:.0f} down to {K_short:.0f}; below "
            f"{K_short:.0f} the client is on their own. Material premium "
            "saving vs. an outright long put."
        ),
        notional_usd=obj.notional_usd,
        hedging_cost_premium_bps=3.0,
    )


def _build_collar(obj: ClientObjective, regime: MarketRegime, *, zero_cost: bool = False) -> Candidate:
    K_put = _round_strike(regime.spot * (1 - _PUT_OTM_PCT_DEFAULT))
    K_call = _round_strike(regime.spot * (1 + _CALL_OTM_PCT_DEFAULT))
    legs = [
        Leg(option_type="european_put", strike=K_put, expiry_days=obj.horizon_days,
            quantity=+1.0, role="long_put_protective"),
        Leg(option_type="european_call", strike=K_call, expiry_days=obj.horizon_days,
            quantity=-1.0, role="short_call_finance"),
    ]
    kind = StructureKind.ZERO_COST_COLLAR if zero_cost else StructureKind.COLLAR
    name = "Zero-Cost Collar" if zero_cost else "Standard Collar"
    rationale = (
        f"Long {K_put:.0f} put financed by short {K_call:.0f} call. Caps upside "
        f"above {K_call:.0f}; protected below {K_put:.0f}. "
        f"{'Sized for ~zero net premium' if zero_cost else 'Net debit/credit per market.'}"
    )
    return Candidate(
        kind=kind,
        name=name,
        legs=legs,
        rationale=rationale,
        notional_usd=obj.notional_usd,
        hedging_cost_premium_bps=4.0,
    )


def _build_ko_put(obj: ClientObjective, regime: MarketRegime) -> Candidate:
    K = _round_strike(regime.spot * (1 - _PUT_OTM_PCT_DEFAULT))
    B = _round_strike(regime.spot * (1 - _KO_BARRIER_PCT_DEFAULT))
    leg = Leg(
        option_type="knockout_put",
        strike=K,
        expiry_days=obj.horizon_days,
        quantity=+1.0,
        barrier_level=B,
        barrier_monitoring="continuous",
        role="long_ko_put",
    )
    return Candidate(
        kind=StructureKind.KO_PUT,
        name=f"KO Put K={K:.0f} B={B:.0f}",
        legs=[leg],
        rationale=(
            f"Down-and-out put: protection from {K:.0f} down to {B:.0f}, knocks out below. "
            "Materially cheaper than a vanilla long put but client carries the gap risk "
            "if spot pierces the barrier and rebounds."
        ),
        notional_usd=obj.notional_usd,
        hedging_cost_premium_bps=8.0,  # barriers are harder to hedge — bake it in
    )


def _build_ki_put(obj: ClientObjective, regime: MarketRegime) -> Candidate:
    K = _round_strike(regime.spot * (1 - _PUT_OTM_PCT_DEFAULT))
    B = _round_strike(regime.spot * (1 - _KI_BARRIER_PCT_DEFAULT))
    leg = Leg(
        option_type="knockin_put",
        strike=K,
        expiry_days=obj.horizon_days,
        quantity=+1.0,
        barrier_level=B,
        barrier_monitoring="continuous",
        role="long_ki_put",
    )
    return Candidate(
        kind=StructureKind.KI_PUT,
        name=f"KI Put K={K:.0f} B={B:.0f}",
        legs=[leg],
        rationale=(
            f"Down-and-in put: only activates if spot trades through {B:.0f}. "
            "Cheap insurance for a real drawdown scenario, useless against shallow drift."
        ),
        notional_usd=obj.notional_usd,
        hedging_cost_premium_bps=6.0,
    )


def _build_covered_call(obj: ClientObjective, regime: MarketRegime) -> Candidate:
    K = _round_strike(regime.spot * (1 + _CALL_OTM_PCT_DEFAULT))
    leg = Leg(
        option_type="european_call",
        strike=K,
        expiry_days=obj.horizon_days,
        quantity=-1.0,
        role="short_call_yield",
    )
    return Candidate(
        kind=StructureKind.COVERED_CALL,
        name=f"Covered Call short {K:.0f}",
        legs=[leg],
        rationale=(
            f"Generate yield by selling {_CALL_OTM_PCT_DEFAULT:.0%}-OTM calls against the "
            "long stock. Caps upside; no downside protection beyond the premium received."
        ),
        notional_usd=obj.notional_usd,
        hedging_cost_premium_bps=2.0,
    )


def _build_risk_reversal(obj: ClientObjective, regime: MarketRegime) -> Candidate:
    K_call = _round_strike(regime.spot * (1 + 0.05))
    K_put = _round_strike(regime.spot * (1 - 0.05))
    legs = [
        Leg(option_type="european_call", strike=K_call, expiry_days=obj.horizon_days,
            quantity=+1.0, role="long_call_directional"),
        Leg(option_type="european_put", strike=K_put, expiry_days=obj.horizon_days,
            quantity=-1.0, role="short_put_finance"),
    ]
    return Candidate(
        kind=StructureKind.RISK_REVERSAL,
        name=f"Risk Reversal long {K_call:.0f}C / short {K_put:.0f}P",
        legs=legs,
        rationale=(
            "Bullish synthetic. Long call financed by short put. Premium near zero in "
            "balanced skew; client takes assignment risk below the short put strike."
        ),
        notional_usd=obj.notional_usd,
        hedging_cost_premium_bps=3.0,
    )


def _build_put_spread_collar(obj: ClientObjective, regime: MarketRegime) -> Candidate:
    """A put spread funded by a short OTM call. Three legs."""
    K_long_put = _round_strike(regime.spot * (1 - _PUT_OTM_PCT_DEFAULT))
    K_short_put = _round_strike(K_long_put - regime.spot * _PUT_SPREAD_WIDTH_DEFAULT)
    K_short_call = _round_strike(regime.spot * (1 + _CALL_OTM_PCT_DEFAULT))
    legs = [
        Leg(option_type="european_put", strike=K_long_put, expiry_days=obj.horizon_days,
            quantity=+1.0, role="long_put_protective"),
        Leg(option_type="european_put", strike=K_short_put, expiry_days=obj.horizon_days,
            quantity=-1.0, role="short_put_finance"),
        Leg(option_type="european_call", strike=K_short_call, expiry_days=obj.horizon_days,
            quantity=-1.0, role="short_call_finance"),
    ]
    return Candidate(
        kind=StructureKind.PUT_SPREAD_COLLAR,
        name=f"Put-Spread Collar {K_long_put:.0f}/{K_short_put:.0f} vs short {K_short_call:.0f}",
        legs=legs,
        rationale=(
            f"Narrow downside protection {K_long_put:.0f}–{K_short_put:.0f} financed by "
            f"short {K_short_call:.0f} call. Cheaper than a full collar but the protection "
            "window is bounded."
        ),
        notional_usd=obj.notional_usd,
        hedging_cost_premium_bps=5.0,
    )


_FACTORIES: dict[StructureKind, Callable[[ClientObjective, MarketRegime], Candidate]] = {
    StructureKind.LONG_PUT: _build_long_put,
    StructureKind.LONG_CALL: _build_long_call,
    StructureKind.PUT_SPREAD: _build_put_spread,
    StructureKind.COLLAR: lambda obj, reg: _build_collar(obj, reg, zero_cost=False),
    StructureKind.ZERO_COST_COLLAR: lambda obj, reg: _build_collar(obj, reg, zero_cost=True),
    StructureKind.KO_PUT: _build_ko_put,
    StructureKind.KI_PUT: _build_ki_put,
    StructureKind.COVERED_CALL: _build_covered_call,
    StructureKind.RISK_REVERSAL: _build_risk_reversal,
    StructureKind.PUT_SPREAD_COLLAR: _build_put_spread_collar,
}


def build_candidates(rule: RuleRow, obj: ClientObjective, regime: MarketRegime) -> list[Candidate]:
    """Build the 3 (or fewer) candidates from a rule row."""
    out: list[Candidate] = []
    for kind in rule.structures:
        factory = _FACTORIES.get(kind)
        if factory is None:
            continue
        try:
            out.append(factory(obj, regime))
        except Exception:  # noqa: BLE001 — never fail a whole session on one factory
            continue
    return out
