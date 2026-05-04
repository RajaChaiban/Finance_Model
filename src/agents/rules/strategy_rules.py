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

import logging
import math
from dataclasses import dataclass
from typing import Callable, Optional

from scipy.optimize import brentq

from src.engines.black_scholes import price_european

from ..state import Candidate, ClientObjective, Leg, MarketRegime, StructureKind

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Forward-anchored strike helpers — high-dividend underliers (XLP, XLRE)
# ---------------------------------------------------------------------------


def _forward(spot: float, r: float, q: float, T: float) -> float:
    """Continuous-compounding forward: F = S * exp((r - q) * T).

    Carry is what makes a collar par-pricing work; spot-anchored strikes
    silently break the par condition on dividend-heavy underliers.
    """
    return spot * math.exp((r - q) * T)


def _strike_pct_of_forward(spot: float, r: float, q: float, T: float, pct: float) -> float:
    """Strike at ``pct`` of the forward, e.g. ``pct=0.95`` for 5% OTM-on-forward put."""
    return _forward(spot, r, q, T) * pct


def _regime_T(obj: ClientObjective) -> float:
    return obj.horizon_days / 365.0


def _regime_sigma(regime: MarketRegime) -> float:
    """Match PricingAgent._pick_sigma: atm_iv > 30d > 90d > 0.20."""
    if regime.atm_iv:
        return regime.atm_iv
    if regime.realised_vol_30d:
        return regime.realised_vol_30d
    if regime.realised_vol_90d:
        return regime.realised_vol_90d
    return 0.20


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
        view="mildly_bullish",
        horizon_band="mid",
        budget_band="low",
        vol_regime="normal",
        barrier_appetite="no",
        structures=(StructureKind.CALL_SPREAD, StructureKind.LONG_CALL, StructureKind.RISK_REVERSAL),
        rationale=(
            "Mild bullish bias on a tight budget: a debit call spread caps the cost "
            "and gives bounded upside; outright long call is the cleaner expression "
            "if budget allows; risk reversal monetises the put wing for a more leveraged "
            "directional view (assignment risk on the short put)."
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
    # Neutral / yield-enhance row covering normal vol regimes and any budget
    # tolerance. The existing high-vol/credit row was the only neutral row,
    # which left normal-vol neutral RFQs (XLV q=1.4%, SMH ATM σ≈0.22) falling
    # through to wrong-direction picks (long-vol put spreads). Stress test
    # scenarios 4 and 9 are the canonical failures this row repairs.
    RuleRow(
        view="neutral",
        horizon_band="mid",
        budget_band="any",
        vol_regime="any",
        barrier_appetite="any",
        structures=(StructureKind.COVERED_CALL, StructureKind.SHORT_STRANGLE, StructureKind.IRON_CONDOR),
        rationale=(
            "Neutral / yield-enhance brief: covered call is the simplest income trade; "
            "short strangle harvests both wings if the client tolerates uncapped tail "
            "risk; iron condor is the capped-loss alternative — credit is smaller but "
            "max loss is bounded by the wing width."
        ),
    ),
    # Same neutral coverage at the short tenor (sub-30d) so 90d harvest cycles
    # still hit a neutral-friendly row in normal vol.
    RuleRow(
        view="neutral",
        horizon_band="short",
        budget_band="any",
        vol_regime="any",
        barrier_appetite="any",
        structures=(StructureKind.COVERED_CALL, StructureKind.SHORT_STRANGLE, StructureKind.IRON_CONDOR),
        rationale=(
            "Short-tenor neutral / yield-enhance: covered call is the simplest income "
            "trade; short strangle for premium harvest with uncapped tails; iron condor "
            "as the capped-loss alternative."
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
    if rule.budget_band != "any" and rule.budget_band != _budget_band(
        obj.budget_bps_notional, obj.premium_tolerance
    ):
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
        budget_ok = rule.budget_band == "any" or rule.budget_band == _budget_band(
            obj.budget_bps_notional, obj.premium_tolerance
        )
        if (
            rule.view == obj.view
            and rule.horizon_band == _horizon_band(obj.horizon_days)
            and budget_ok
            and (rule.vol_regime == "any" or rule.vol_regime == regime.vol_regime)
        ):
            return rule

    # 3. Drop vol_regime.
    for rule in RULES:
        budget_ok = rule.budget_band == "any" or rule.budget_band == _budget_band(
            obj.budget_bps_notional, obj.premium_tolerance
        )
        if (
            rule.view == obj.view
            and rule.horizon_band == _horizon_band(obj.horizon_days)
            and budget_ok
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
# All percentages are now anchored against the FORWARD F = S * exp((r-q)*T),
# not spot, so dividend-heavy underliers (XLP, XLRE) get correctly placed
# strikes. For a low-div underlier with r ≈ q ≈ 0 this collapses to the
# previous spot-anchored behaviour to within rounding.
_PUT_OTM_PCT_DEFAULT = 0.05      # 5% OTM-on-forward put
_CALL_OTM_PCT_DEFAULT = 0.08     # 8% OTM-on-forward short call (collars / covered calls)
_KO_BARRIER_PCT_DEFAULT = 0.20   # 20% below forward for down-and-out put
_PUT_SPREAD_WIDTH_DEFAULT = 0.10 # 10% of forward wide
_KI_BARRIER_PCT_DEFAULT = 0.15   # 15% below forward for KI activation

# Bullish (call-side) defaults — mirror the put-side conventions.
_CALL_LONG_OTM_PCT_DEFAULT = 0.05    # 5% OTM-on-forward long call leg (call spread)
_CALL_SPREAD_WIDTH_DEFAULT = 0.10    # 10% of forward wide (parallel to put spread)
_KO_CALL_BARRIER_PCT_DEFAULT = 0.20  # 20% above forward for up-and-out call
_KI_CALL_BARRIER_PCT_DEFAULT = 0.15  # 15% above forward for up-and-in call activation

# Strangle / iron-condor defaults — wing widths anchored to forward.
_STRANGLE_PUT_PCT_DEFAULT = 0.10     # short put 10% below forward
_STRANGLE_CALL_PCT_DEFAULT = 0.10    # short call 10% above forward
_CONDOR_INNER_PCT_DEFAULT = 0.10     # inner short legs at +-10% forward
_CONDOR_WING_PCT_DEFAULT = 0.20      # outer long legs at +-20% forward (10% wing width)


def _round_strike(x: float) -> float:
    """Round to a sensible listed-strike grid: $1 increments under $50, $5 above."""
    if x < 50:
        return round(x, 1)
    if x < 200:
        return round(x)
    return float(round(x / 5) * 5)


def _build_long_put(obj: ClientObjective, regime: MarketRegime) -> Candidate:
    T = _regime_T(obj)
    K = _round_strike(_strike_pct_of_forward(
        regime.spot, regime.risk_free_rate, regime.dividend_yield, T,
        1 - _PUT_OTM_PCT_DEFAULT,
    ))
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
    T = _regime_T(obj)
    K = _round_strike(_strike_pct_of_forward(
        regime.spot, regime.risk_free_rate, regime.dividend_yield, T, 1 + 0.03,
    ))
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
    T = _regime_T(obj)
    F = _forward(regime.spot, regime.risk_free_rate, regime.dividend_yield, T)
    K_long = _round_strike(F * (1 - _PUT_OTM_PCT_DEFAULT))
    K_short = _round_strike(K_long - F * _PUT_SPREAD_WIDTH_DEFAULT)
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


def _solve_zero_cost_call_strike(
    *, spot: float, K_put: float, r: float, q: float, sigma: float, T: float,
) -> tuple[float, bool]:
    """Brent root-solve for the call strike that prices the put leg exactly.

    Solves f(K_call) = BS_call(S, K_call, r, q, σ, T) − BS_put(S, K_put, r, q, σ, T) = 0
    over K_call ∈ [forward, 1.30·forward], widening to 1.50·forward on first
    failure. f is monotone-decreasing in K_call (call premium decreases as the
    strike moves further OTM), so Brent converges fast given a sign change in
    the bracket.

    Returns (K_call, solved_ok). When ``solved_ok`` is False the caller should
    fall back to the static OTM grid and let the validator's WARN fire.
    """
    F = _forward(spot, r, q, T)
    put_prem = price_european(spot, K_put, r, sigma, T, q, option_type="put")

    def _f(K_call: float) -> float:
        call_prem = price_european(spot, K_call, r, sigma, T, q, option_type="call")
        return call_prem - put_prem

    for hi_mult in (1.30, 1.50, 2.00):
        lo = F
        hi = F * hi_mult
        try:
            f_lo = _f(lo)
            f_hi = _f(hi)
        except Exception as exc:  # noqa: BLE001 — black-scholes guards inputs
            logger.debug("collar BS failed at bracket {%g, %g}: %s", lo, hi, exc)
            continue
        if f_lo * f_hi > 0:
            # No sign change — call premium > put premium even at hi (very deep
            # OTM put case) or call premium < put premium at lo (very far ITM
            # ATM-vs-forward case). Widen and retry.
            continue
        try:
            K_call = brentq(_f, lo, hi, xtol=1e-3, rtol=1e-6, maxiter=64)
            return float(K_call), True
        except Exception as exc:  # noqa: BLE001
            logger.debug("brentq collar solve failed [%g, %g]: %s", lo, hi, exc)
            continue
    return float(F * (1 + _CALL_OTM_PCT_DEFAULT)), False


def _grid_step_for_strike(K: float) -> float:
    """Mirror the listed-strike grid implied by ``_round_strike``."""
    if K < 50:
        return 0.1
    if K < 200:
        return 1.0
    return 5.0


def _refine_collar_pair_on_grid(
    *, K_put: float, K_call: float, spot: float, r: float, q: float,
    sigma: float, T: float,
) -> tuple[float, float]:
    """After Brent, sweep ±1 grid step on both legs and return the rounded
    pair with the smallest |call_premium − put_premium|. ~9 candidate pairs.
    Pure local search — never moves more than one listed-strike step from the
    Brent solution, so the desk-recognisable strikes (95P/105C grid) stay in
    the same neighborhood.
    """
    put_step = _grid_step_for_strike(K_put)
    call_step = _grid_step_for_strike(K_call)

    best = (K_put, K_call)
    try:
        best_diff = abs(
            price_european(spot, K_call, r, sigma, T, q, option_type="call")
            - price_european(spot, K_put, r, sigma, T, q, option_type="put")
        )
    except Exception:  # noqa: BLE001
        return best

    for dput in (-put_step, 0.0, +put_step):
        for dcall in (-call_step, 0.0, +call_step):
            kp = K_put + dput
            kc = K_call + dcall
            if kp <= 0 or kc <= 0 or kc <= kp:
                continue
            try:
                diff = abs(
                    price_european(spot, kc, r, sigma, T, q, option_type="call")
                    - price_european(spot, kp, r, sigma, T, q, option_type="put")
                )
            except Exception:  # noqa: BLE001
                continue
            if diff < best_diff:
                best_diff = diff
                best = (kp, kc)
    return best


def _build_collar(obj: ClientObjective, regime: MarketRegime, *, zero_cost: bool = False) -> Candidate:
    """Build a collar.

    For ``zero_cost=True``: hold the put at 5% OTM-on-forward and Brent
    root-solve for the call strike that drives net premium to true zero
    (closed-form Black-Scholes on both legs). The previous implementation
    used a static spot-anchored grid (95P / 105C) that landed 38–80 bps off
    zero on dividend-heavy underliers because the forward sits meaningfully
    above spot — see stress_test_2026_05_03/consolidated_report.md scenarios
    3, 8, 10. The Brent solve removes that bias to <5 bps for all tested
    underliers.

    For ``zero_cost=False``: use static 5% OTM-on-forward put / 8% OTM-on-
    forward call (small net debit/credit accepted).
    """
    T = _regime_T(obj)
    F = _forward(regime.spot, regime.risk_free_rate, regime.dividend_yield, T)
    K_put = _round_strike(F * (1 - _PUT_OTM_PCT_DEFAULT))

    solved_ok = True
    if zero_cost:
        sigma = _regime_sigma(regime)
        # Solve continuous K_call, then snap onto the same listed-strike grid
        # the rest of the desk uses. After rounding we sweep ±1 grid step on
        # both legs and pick the (K_put, K_call) pair that minimises |Δ premium|
        # — this absorbs the rounding bias that otherwise leaves XLF / XLP /
        # XLRE collars at 8–14 bps despite a continuous-zero solve.
        K_call_raw, solved_ok = _solve_zero_cost_call_strike(
            spot=regime.spot,
            K_put=K_put,
            r=regime.risk_free_rate,
            q=regime.dividend_yield,
            sigma=sigma,
            T=T,
        )
        K_call = _round_strike(K_call_raw)
        if solved_ok:
            K_put, K_call = _refine_collar_pair_on_grid(
                K_put=K_put,
                K_call=K_call,
                spot=regime.spot,
                r=regime.risk_free_rate,
                q=regime.dividend_yield,
                sigma=sigma,
                T=T,
            )
    else:
        K_call = _round_strike(F * (1 + _CALL_OTM_PCT_DEFAULT))

    legs = [
        Leg(option_type="european_put", strike=K_put, expiry_days=obj.horizon_days,
            quantity=+1.0, role="long_put_protective"),
        Leg(option_type="european_call", strike=K_call, expiry_days=obj.horizon_days,
            quantity=-1.0, role="short_call_finance"),
    ]
    kind = StructureKind.ZERO_COST_COLLAR if zero_cost else StructureKind.COLLAR
    name = "Zero-Cost Collar" if zero_cost else "Standard Collar"
    if zero_cost:
        sized = (
            "Strikes solved for zero net premium against the BS forward."
            if solved_ok
            else "Brent solve fell back to static grid — validator may flag residual premium."
        )
    else:
        sized = "Net debit/credit per market."
    rationale = (
        f"Long {K_put:.0f} put financed by short {K_call:.0f} call. Caps upside "
        f"above {K_call:.0f}; protected below {K_put:.0f}. {sized}"
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
    """Down-and-out put for crash hedges.

    Strike is forward-anchored 5% OTM; barrier is forward-anchored 20% below
    forward. The previous default (B = 80%·F, K = 95%·F) holds — but only on
    a forward, not spot, basis. This guarantees ``B < K`` even on a deep ITM
    high-div underlier, which avoids the scenario-5 ``B == K`` pin risk.
    """
    T = _regime_T(obj)
    F = _forward(regime.spot, regime.risk_free_rate, regime.dividend_yield, T)
    K = _round_strike(F * (1 - _PUT_OTM_PCT_DEFAULT))
    B = _round_strike(F * (1 - _KO_BARRIER_PCT_DEFAULT))
    # Belt and braces: if rounding collapsed B to K (deep-OTM put on a low
    # spot underlier where rounding bins are wide), nudge B down by one
    # rounding step. KO put with B == K is degenerate — knock-out always
    # triggers exactly at exercise.
    if B >= K:
        B = _round_strike(min(K * 0.95, K - 1.0))
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
    T = _regime_T(obj)
    F = _forward(regime.spot, regime.risk_free_rate, regime.dividend_yield, T)
    K = _round_strike(F * (1 - _PUT_OTM_PCT_DEFAULT))
    B = _round_strike(F * (1 - _KI_BARRIER_PCT_DEFAULT))
    if B >= K:
        B = _round_strike(min(K * 0.95, K - 1.0))
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
    T = _regime_T(obj)
    K = _round_strike(_strike_pct_of_forward(
        regime.spot, regime.risk_free_rate, regime.dividend_yield, T,
        1 + _CALL_OTM_PCT_DEFAULT,
    ))
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
    T = _regime_T(obj)
    F = _forward(regime.spot, regime.risk_free_rate, regime.dividend_yield, T)
    K_call = _round_strike(F * (1 + 0.05))
    K_put = _round_strike(F * (1 - 0.05))
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
    T = _regime_T(obj)
    F = _forward(regime.spot, regime.risk_free_rate, regime.dividend_yield, T)
    K_long_put = _round_strike(F * (1 - _PUT_OTM_PCT_DEFAULT))
    K_short_put = _round_strike(K_long_put - F * _PUT_SPREAD_WIDTH_DEFAULT)
    K_short_call = _round_strike(F * (1 + _CALL_OTM_PCT_DEFAULT))
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


def _build_call_spread(obj: ClientObjective, regime: MarketRegime, *, debit: bool = True) -> Candidate:
    """Bullish call spread: long lower-strike call + short higher-strike call.

    Mirrors `_build_put_spread`: long leg at `_CALL_LONG_OTM_PCT_DEFAULT` OTM,
    short leg another `_CALL_SPREAD_WIDTH_DEFAULT` of spot above. `debit=True`
    is the standard bullish debit call spread; `debit=False` flips to a credit
    bear-call spread (kept for symmetry with put spread).
    """
    T = _regime_T(obj)
    F = _forward(regime.spot, regime.risk_free_rate, regime.dividend_yield, T)
    K_long = _round_strike(F * (1 + _CALL_LONG_OTM_PCT_DEFAULT))
    K_short = _round_strike(K_long + F * _CALL_SPREAD_WIDTH_DEFAULT)
    long_qty = +1.0 if debit else -1.0
    short_qty = -1.0 if debit else +1.0
    legs = [
        Leg(option_type="european_call", strike=K_long, expiry_days=obj.horizon_days,
            quantity=long_qty, role="long_call_directional"),
        Leg(option_type="european_call", strike=K_short, expiry_days=obj.horizon_days,
            quantity=short_qty, role="short_call_finance"),
    ]
    return Candidate(
        kind=StructureKind.CALL_SPREAD,
        name=f"Call Spread {K_long:.0f}/{K_short:.0f}",
        legs=legs,
        rationale=(
            f"Bullish exposure between {K_long:.0f} and {K_short:.0f}; upside above "
            f"{K_short:.0f} is given up. Materially cheaper than an outright long call "
            "and the natural cost-controlled bullish expression."
        ),
        notional_usd=obj.notional_usd,
        hedging_cost_premium_bps=3.0,
    )


def _build_ko_call(obj: ClientObjective, regime: MarketRegime) -> Candidate:
    """Long up-and-out call: cheap bullish exposure that knocks out on a melt-up."""
    T = _regime_T(obj)
    F = _forward(regime.spot, regime.risk_free_rate, regime.dividend_yield, T)
    K = _round_strike(F * (1 + _CALL_LONG_OTM_PCT_DEFAULT))
    B = _round_strike(F * (1 + _KO_CALL_BARRIER_PCT_DEFAULT))
    if B <= K:
        B = _round_strike(max(K * 1.05, K + 1.0))
    leg = Leg(
        option_type="knockout_call",
        strike=K,
        expiry_days=obj.horizon_days,
        quantity=+1.0,
        barrier_level=B,
        barrier_monitoring="continuous",
        role="long_ko_call",
    )
    return Candidate(
        kind=StructureKind.KO_CALL,
        name=f"KO Call K={K:.0f} B={B:.0f}",
        legs=[leg],
        rationale=(
            f"Up-and-out call: bullish exposure from {K:.0f} up to {B:.0f}, knocks out above. "
            "Materially cheaper than a vanilla long call but the client gives up the "
            "right tail if spot pierces the barrier."
        ),
        notional_usd=obj.notional_usd,
        hedging_cost_premium_bps=8.0,  # barriers are harder to hedge — bake it in
    )


def _build_ki_call(obj: ClientObjective, regime: MarketRegime) -> Candidate:
    """Long up-and-in call: only activates if spot rallies through the barrier."""
    T = _regime_T(obj)
    F = _forward(regime.spot, regime.risk_free_rate, regime.dividend_yield, T)
    K = _round_strike(F * (1 + _CALL_LONG_OTM_PCT_DEFAULT))
    B = _round_strike(F * (1 + _KI_CALL_BARRIER_PCT_DEFAULT))
    if B <= K:
        B = _round_strike(max(K * 1.05, K + 1.0))
    leg = Leg(
        option_type="knockin_call",
        strike=K,
        expiry_days=obj.horizon_days,
        quantity=+1.0,
        barrier_level=B,
        barrier_monitoring="continuous",
        role="long_ki_call",
    )
    return Candidate(
        kind=StructureKind.KI_CALL,
        name=f"KI Call K={K:.0f} B={B:.0f}",
        legs=[leg],
        rationale=(
            f"Up-and-in call: only activates if spot trades through {B:.0f}. "
            "Cheap bullish kicker for a melt-up scenario, useless against a slow grind."
        ),
        notional_usd=obj.notional_usd,
        hedging_cost_premium_bps=6.0,
    )


def _build_short_strangle(obj: ClientObjective, regime: MarketRegime) -> Candidate:
    """Short strangle: short OTM call + short OTM put.

    Tail-risk-uncapped premium harvest. Wing OTM-ness scales with σ — high-vol
    regimes pull the wings tighter (still 1σ-ish) and low-vol regimes push
    them wider so theta accrual stays meaningful. The base width is
    ``_STRANGLE_*_PCT_DEFAULT`` (10% of forward), shifted up to 15% on high
    vol and down to 7% on very-low vol.
    """
    T = _regime_T(obj)
    F = _forward(regime.spot, regime.risk_free_rate, regime.dividend_yield, T)

    # σ-scale the strangle width so the regime drives the wings.
    sigma = _regime_sigma(regime)
    # Approx "1σ move" over T as a forward-relative pct.
    one_sigma_pct = sigma * math.sqrt(max(T, 1e-3))
    # Clamp to a sensible band so very-short-tenor neutral RFQs don't collapse.
    wing_pct = max(0.04, min(0.20, one_sigma_pct))

    K_call = _round_strike(F * (1 + wing_pct))
    K_put = _round_strike(F * (1 - wing_pct))
    legs = [
        Leg(option_type="european_call", strike=K_call, expiry_days=obj.horizon_days,
            quantity=-1.0, role="short_call_yield"),
        Leg(option_type="european_put", strike=K_put, expiry_days=obj.horizon_days,
            quantity=-1.0, role="short_put_yield"),
    ]
    return Candidate(
        kind=StructureKind.SHORT_STRANGLE,
        name=f"Short Strangle {K_put:.0f}P / {K_call:.0f}C",
        legs=legs,
        rationale=(
            f"Sell {K_put:.0f} put and {K_call:.0f} call (~1σ wings on the forward) to "
            f"harvest premium on a range-bound view. Uncapped tail risk on either side — "
            "size to capacity to absorb a 2σ move."
        ),
        notional_usd=obj.notional_usd,
        hedging_cost_premium_bps=4.0,
    )


def _build_iron_condor(obj: ClientObjective, regime: MarketRegime) -> Candidate:
    """Iron condor: short call/put strangle wrapped by long wings.

    Capped-loss alternative to the short strangle. Inner short legs at
    ``±_CONDOR_INNER_PCT_DEFAULT`` of forward; outer long protection legs at
    ``±_CONDOR_WING_PCT_DEFAULT``. Net credit is smaller than the short
    strangle but max loss is bounded by (wing - inner) per side.
    """
    T = _regime_T(obj)
    F = _forward(regime.spot, regime.risk_free_rate, regime.dividend_yield, T)

    K_call_short = _round_strike(F * (1 + _CONDOR_INNER_PCT_DEFAULT))
    K_call_long = _round_strike(F * (1 + _CONDOR_WING_PCT_DEFAULT))
    K_put_short = _round_strike(F * (1 - _CONDOR_INNER_PCT_DEFAULT))
    K_put_long = _round_strike(F * (1 - _CONDOR_WING_PCT_DEFAULT))
    legs = [
        # Short body
        Leg(option_type="european_call", strike=K_call_short, expiry_days=obj.horizon_days,
            quantity=-1.0, role="short_call_yield"),
        Leg(option_type="european_put", strike=K_put_short, expiry_days=obj.horizon_days,
            quantity=-1.0, role="short_put_yield"),
        # Long wings (capped tails)
        Leg(option_type="european_call", strike=K_call_long, expiry_days=obj.horizon_days,
            quantity=+1.0, role="long_call_protective"),
        Leg(option_type="european_put", strike=K_put_long, expiry_days=obj.horizon_days,
            quantity=+1.0, role="long_put_protective"),
    ]
    return Candidate(
        kind=StructureKind.IRON_CONDOR,
        name=f"Iron Condor {K_put_long:.0f}/{K_put_short:.0f}P-{K_call_short:.0f}/{K_call_long:.0f}C",
        legs=legs,
        rationale=(
            f"Capped-loss premium harvest. Short {K_put_short:.0f}P/{K_call_short:.0f}C body "
            f"financed; {K_put_long:.0f}P/{K_call_long:.0f}C wings cap the tails. Smaller "
            "credit than a strangle but loss is bounded by the wing width."
        ),
        notional_usd=obj.notional_usd,
        hedging_cost_premium_bps=5.0,
    )


_FACTORIES: dict[StructureKind, Callable[[ClientObjective, MarketRegime], Candidate]] = {
    StructureKind.LONG_PUT: _build_long_put,
    StructureKind.LONG_CALL: _build_long_call,
    StructureKind.PUT_SPREAD: _build_put_spread,
    StructureKind.CALL_SPREAD: _build_call_spread,
    StructureKind.COLLAR: lambda obj, reg: _build_collar(obj, reg, zero_cost=False),
    StructureKind.ZERO_COST_COLLAR: lambda obj, reg: _build_collar(obj, reg, zero_cost=True),
    StructureKind.KO_PUT: _build_ko_put,
    StructureKind.KI_PUT: _build_ki_put,
    StructureKind.KO_CALL: _build_ko_call,
    StructureKind.KI_CALL: _build_ki_call,
    StructureKind.COVERED_CALL: _build_covered_call,
    StructureKind.RISK_REVERSAL: _build_risk_reversal,
    StructureKind.PUT_SPREAD_COLLAR: _build_put_spread_collar,
    StructureKind.SHORT_STRANGLE: _build_short_strangle,
    StructureKind.IRON_CONDOR: _build_iron_condor,
}


# Views with a "downside" lego that supports a barrier substitution. Maps the
# objective.view to the StructureKind we want to insert as candidate-1 when
# objective.barrier_appetite=True. See stress test scenario 2 (XLE bearish +
# barrier_appetite=True returned zero KO/KI candidates).
_BARRIER_PREFERENCE: dict[str, StructureKind] = {
    "bearish": StructureKind.KI_PUT,
    "mildly_bearish": StructureKind.KI_PUT,
    "protect_gains": StructureKind.KI_PUT,
    "earnings_hedge": StructureKind.KI_PUT,
    "crash_hedge": StructureKind.KO_PUT,
}


# Views with neutral / yield-enhance semantics. Drives the strangle-vs-condor
# preference inside the neutral row (very_low tolerance prefers the capped-loss
# condor; medium / low / high prefer the strangle's larger credit).
_NEUTRAL_VIEWS = {"neutral", "yield_enhance"}


def _ensure_barrier_candidate(
    candidates: list[Candidate], obj: ClientObjective, regime: MarketRegime,
) -> list[Candidate]:
    """When ``barrier_appetite=True`` and the view supports a barrier lego,
    guarantee at least one barrier candidate. If none of the existing 3 are
    barrier-typed, replace the cheapest non-barrier with the preferred barrier
    variant (KI_PUT or KO_PUT depending on view).
    """
    if not obj.barrier_appetite:
        return candidates
    preferred = _BARRIER_PREFERENCE.get(obj.view)
    if preferred is None:
        return candidates
    barrier_kinds = {
        StructureKind.KO_PUT, StructureKind.KI_PUT,
        StructureKind.KO_CALL, StructureKind.KI_CALL,
    }
    if any(c.kind in barrier_kinds for c in candidates):
        return candidates
    factory = _FACTORIES.get(preferred)
    if factory is None:
        return candidates
    try:
        new_cand = factory(obj, regime)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Barrier substitution build failed for %s: %s", preferred, exc)
        return candidates
    if not candidates:
        return [new_cand]
    # Replace the LAST candidate (least-preferred slot) with the barrier one
    # so the top-pick rationale stays intact for the other two slots.
    candidates[-1] = new_cand
    return candidates


def _reorder_neutral_candidates(
    candidates: list[Candidate], obj: ClientObjective,
) -> list[Candidate]:
    """For neutral / yield_enhance views, reorder the strangle/condor pair
    according to premium tolerance:
      - very_low → IRON_CONDOR first (capped-loss preferred)
      - low/medium/high → SHORT_STRANGLE first (larger credit preferred)
    Other candidates retain their relative order.
    """
    if obj.view not in _NEUTRAL_VIEWS:
        return candidates
    has_strangle = any(c.kind == StructureKind.SHORT_STRANGLE for c in candidates)
    has_condor = any(c.kind == StructureKind.IRON_CONDOR for c in candidates)
    if not (has_strangle and has_condor):
        return candidates
    prefer_condor = obj.premium_tolerance == "very_low"
    keys = {StructureKind.SHORT_STRANGLE, StructureKind.IRON_CONDOR}
    pair = [c for c in candidates if c.kind in keys]
    others = [c for c in candidates if c.kind not in keys]
    pair.sort(
        key=lambda c: (
            0 if (c.kind == StructureKind.IRON_CONDOR) == prefer_condor else 1,
            0 if c.kind == StructureKind.IRON_CONDOR else 1,
        )
    )
    # Preserve the pre-existing slot ordering: walk original list, swap as we go.
    out = list(candidates)
    pair_iter = iter(pair)
    for i, c in enumerate(out):
        if c.kind in keys:
            out[i] = next(pair_iter)
    _ = others  # 'others' captured for clarity; not used to reshuffle
    return out


def build_candidates(rule: RuleRow, obj: ClientObjective, regime: MarketRegime) -> list[Candidate]:
    """Build the 3 (or fewer) candidates from a rule row.

    Post-processing:
      - barrier-appetite gating: if ``obj.barrier_appetite=True`` and the view
        is in ``_BARRIER_PREFERENCE``, ensure at least one barrier candidate
        appears in the slate (substituting the bottom-ranked non-barrier).
      - neutral preference: for neutral / yield_enhance views, the strangle vs
        condor ordering follows ``premium_tolerance``.
      - budget rescue: each candidate is quick-priced via closed-form BS; if
        every variant blows the budget, lightweight transforms (tighten spread,
        push long leg further OTM, vanilla→barrier when allowed, tighten
        collar cap) are tried in order and the first in-budget variant is
        adopted. See ``_rescue_for_budget``.
    """
    out: list[Candidate] = []
    for kind in rule.structures:
        factory = _FACTORIES.get(kind)
        if factory is None:
            continue
        try:
            out.append(factory(obj, regime))
        except Exception:  # noqa: BLE001 — never fail a whole session on one factory
            continue
    out = _ensure_barrier_candidate(out, obj, regime)
    out = _reorder_neutral_candidates(out, obj)
    out = _rescue_for_budget(out, obj, regime)
    return out


# ---------------------------------------------------------------------------
# Budget-aware rescue pass
# ---------------------------------------------------------------------------


def _barrier_discount(
    *, kind: str, S: float, K: float, B: float, sigma: float, T: float,
) -> float:
    """Distance-and-tenor-aware KI/KO discount factor in [0, 1].

    The static 0.40 / 0.60 ratios mis-price short-tenor high-vol KI/KO by
    50%+ — when the barrier is close (in σ√T units) the touch probability
    is high and KI ≈ vanilla. We bracket the discount with a piecewise-
    linear interpolation on the standardised log-distance ``λ = |ln(B/S)|
    / (σ√T)``. KI and KO are tracked SEPARATELY (not 1 − KI) and both are
    biased UP slightly so the rescue's quick price runs ~5-15% rich vs
    QL — under-estimating either side causes the rescue to accept a
    candidate the validator will then BLOCK. Anchors calibrated against
    QuantLib RR on SPY 365d / COIN 14d / typical 5-15% OTM cases.
    """
    if S <= 0 or B <= 0 or sigma <= 0 or T <= 0:
        return 0.4
    sqrtT = math.sqrt(T)
    lam = abs(math.log(B / S)) / (sigma * sqrtT)

    # KI anchors: (lam, KI_ratio). Slightly biased above QL.
    ki_anchors = [
        (0.0, 1.00), (0.3, 1.00), (0.6, 0.98),
        (0.9, 0.92), (1.2, 0.82), (1.5, 0.65),
        (1.9, 0.40), (2.3, 0.20), (3.0, 0.08), (5.0, 0.01),
    ]
    # KO anchors: (lam, KO_ratio). Tracked independently — KI+KO≈1 in QL,
    # but biasing both UP keeps the rescue conservative on either side.
    ko_anchors = [
        (0.0, 0.05), (0.3, 0.10), (0.6, 0.18),
        (0.9, 0.27), (1.2, 0.40), (1.5, 0.55),
        (1.9, 0.72), (2.3, 0.85), (3.0, 0.95), (5.0, 0.99),
    ]

    def _interp(anchors: list[tuple[float, float]], x: float) -> float:
        if x <= anchors[0][0]:
            return anchors[0][1]
        if x >= anchors[-1][0]:
            return anchors[-1][1]
        for (l_lo, r_lo), (l_hi, r_hi) in zip(anchors, anchors[1:]):
            if l_lo <= x <= l_hi:
                w = (x - l_lo) / (l_hi - l_lo)
                return r_lo + w * (r_hi - r_lo)
        return anchors[len(anchors) // 2][1]

    if kind.startswith("knockin_"):
        return max(0.0, min(1.0, _interp(ki_anchors, lam)))
    if kind.startswith("knockout_"):
        return max(0.0, min(1.0, _interp(ko_anchors, lam)))
    return 1.0


def _quick_price_bps(
    legs: list[Leg], spot: float, r: float, q: float, sigma: float, T: float,
) -> float:
    """Closed-form BS proxy for ``net_premium_bps`` of a candidate.

    Sums ``leg.quantity * price`` per share, then converts to bps via
    ``per_share / spot * 10_000``. Vanilla legs use ``price_european``
    directly. Barrier legs scale the same-strike vanilla by a distance-
    and-tenor-aware KI/KO discount (``_barrier_discount``) so the rescue
    quick price is calibrated for short-tenor high-vol regimes where the
    barrier is almost certain to be hit. The PricingAgent re-prices
    exactly via the QL barrier engine downstream.
    """
    if spot <= 0 or T <= 0 or sigma <= 0:
        return 0.0
    per_share = 0.0
    for leg in legs:
        opt = leg.option_type.lower()
        if opt.endswith("_call"):
            base = "call"
        elif opt.endswith("_put"):
            base = "put"
        else:
            # Asian / lookback / unknown — approximate as vanilla on strike.
            base = "put"
        try:
            vanilla = price_european(spot, leg.strike, r, sigma, T, q, option_type=base)
        except Exception:  # noqa: BLE001
            continue
        if opt.startswith(("knockout_", "knockin_")) and leg.barrier_level is not None:
            disc = _barrier_discount(
                kind=opt, S=spot, K=leg.strike, B=float(leg.barrier_level),
                sigma=sigma, T=T,
            )
            price = disc * vanilla
        else:
            price = vanilla
        per_share += leg.quantity * price
    return (per_share / spot) * 10_000.0


def _within_tolerance(quick_bps: float, budget_bps: float, mult: float, slack: float) -> bool:
    """Tolerance check used by the rescue: |bps| ≤ budget * mult + slack."""
    return abs(quick_bps) <= budget_bps * mult + slack


def _clone_candidate_with_legs(
    cand: Candidate, legs: list[Leg], *, name: Optional[str] = None,
) -> Candidate:
    """Return a copy of ``cand`` with the given legs (and optional new name).

    Preserves ``kind``, ``rationale``, ``hedging_cost_premium_bps``, and the
    notional. Strategist tests assert on ``kind`` membership, never on a
    specific candidate_id, so re-using the original id keeps memo and gate-B
    swap intent stable through the rescue.
    """
    return Candidate(
        candidate_id=cand.candidate_id,
        kind=cand.kind,
        name=name if name is not None else cand.name,
        legs=legs,
        rationale=cand.rationale,
        hedging_cost_premium_bps=cand.hedging_cost_premium_bps,
        notional_usd=cand.notional_usd,
    )


def _tighten_spread(cand: Candidate) -> Optional[Candidate]:
    """For PUT_SPREAD / CALL_SPREAD / PUT_SPREAD_COLLAR: narrow the long-short
    K distance to 50% of original by moving the SHORT leg toward the long.
    """
    if cand.kind not in (
        StructureKind.PUT_SPREAD, StructureKind.CALL_SPREAD,
        StructureKind.PUT_SPREAD_COLLAR,
    ):
        return None

    is_put_spread = cand.kind in (StructureKind.PUT_SPREAD, StructureKind.PUT_SPREAD_COLLAR)
    leg_filter = "_put" if is_put_spread else "_call"

    long_leg = next(
        (l for l in cand.legs if l.option_type.endswith(leg_filter) and l.quantity > 0),
        None,
    )
    short_leg = next(
        (l for l in cand.legs if l.option_type.endswith(leg_filter) and l.quantity < 0),
        None,
    )
    if long_leg is None or short_leg is None:
        return None

    # Move short leg halfway toward long.
    new_short_K = _round_strike((long_leg.strike + short_leg.strike) / 2.0)
    if new_short_K == short_leg.strike:
        return None  # already as tight as the grid allows
    if is_put_spread and new_short_K >= long_leg.strike:
        return None
    if (not is_put_spread) and new_short_K <= long_leg.strike:
        return None

    new_legs = []
    for leg in cand.legs:
        if leg is short_leg:
            new_legs.append(leg.model_copy(update={"strike": new_short_K}))
        else:
            new_legs.append(leg.model_copy())
    return _clone_candidate_with_legs(cand, new_legs)


def _push_long_otm(cand: Candidate, regime: MarketRegime, T: float) -> Optional[Candidate]:
    """For LONG_PUT / LONG_CALL / PUT_SPREAD / CALL_SPREAD: push the LONG
    leg further out by 50% of its current distance from the forward.
    Reduces premium materially while keeping kind unchanged.
    """
    if cand.kind not in (
        StructureKind.LONG_PUT, StructureKind.LONG_CALL,
        StructureKind.PUT_SPREAD, StructureKind.CALL_SPREAD,
    ):
        return None
    F = _forward(regime.spot, regime.risk_free_rate, regime.dividend_yield, T)
    is_call_side = cand.kind in (StructureKind.LONG_CALL, StructureKind.CALL_SPREAD)
    leg_filter = "_call" if is_call_side else "_put"
    long_leg = next(
        (l for l in cand.legs if l.option_type.endswith(leg_filter) and l.quantity > 0),
        None,
    )
    if long_leg is None:
        return None

    distance = long_leg.strike - F  # signed (negative for OTM puts)
    new_K = _round_strike(long_leg.strike - 0.5 * distance)
    if new_K == long_leg.strike or new_K <= 0:
        return None

    new_legs = []
    for leg in cand.legs:
        if leg is long_leg:
            new_legs.append(leg.model_copy(update={"strike": new_K}))
        else:
            new_legs.append(leg.model_copy())
    return _clone_candidate_with_legs(cand, new_legs)


def _convert_vanilla_to_barrier(
    cand: Candidate, regime: MarketRegime, T: float,
) -> Optional[Candidate]:
    """LONG_PUT → KI_PUT (B at 85% of forward), LONG_CALL → KI_CALL (B at
    115% of forward). Caller guards on ``barrier_appetite=True``.

    NOTE: this is the ONE rescue transform that mutates ``StructureKind``.
    Gated by ``objective.barrier_appetite=True`` — see CLAUDE.md invariants.
    """
    F = _forward(regime.spot, regime.risk_free_rate, regime.dividend_yield, T)

    if cand.kind == StructureKind.LONG_PUT:
        long_leg = next(
            (l for l in cand.legs if l.option_type == "european_put" and l.quantity > 0),
            None,
        )
        if long_leg is None:
            return None
        B = _round_strike(F * 0.85)
        if B >= long_leg.strike:
            B = _round_strike(min(long_leg.strike * 0.95, long_leg.strike - 1.0))
        new_leg = long_leg.model_copy(update={
            "option_type": "knockin_put",
            "barrier_level": B,
            "barrier_monitoring": "continuous",
            "role": "long_ki_put",
        })
        return Candidate(
            candidate_id=cand.candidate_id,
            kind=StructureKind.KI_PUT,
            name=f"KI Put K={long_leg.strike:.0f} B={B:.0f}",
            legs=[new_leg],
            rationale=cand.rationale,
            hedging_cost_premium_bps=6.0,
            notional_usd=cand.notional_usd,
        )

    if cand.kind == StructureKind.LONG_CALL:
        long_leg = next(
            (l for l in cand.legs if l.option_type == "european_call" and l.quantity > 0),
            None,
        )
        if long_leg is None:
            return None
        B = _round_strike(F * 1.15)
        if B <= long_leg.strike:
            B = _round_strike(max(long_leg.strike * 1.05, long_leg.strike + 1.0))
        new_leg = long_leg.model_copy(update={
            "option_type": "knockin_call",
            "barrier_level": B,
            "barrier_monitoring": "continuous",
            "role": "long_ki_call",
        })
        return Candidate(
            candidate_id=cand.candidate_id,
            kind=StructureKind.KI_CALL,
            name=f"KI Call K={long_leg.strike:.0f} B={B:.0f}",
            legs=[new_leg],
            rationale=cand.rationale,
            hedging_cost_premium_bps=6.0,
            notional_usd=cand.notional_usd,
        )

    return None


def _push_barrier_strike_deep(
    cand: Candidate, regime: MarketRegime, T: float,
) -> Optional[Candidate]:
    """Cheapen a barrier or long-vanilla candidate by pushing the strike
    deeper OTM (10% OTM-on-forward instead of 5%) — invoked only when the
    earlier rescue transforms didn't bring the premium under budget.

    Three accepted starting kinds:
      - LONG_PUT  → KI_PUT at 90% F with B at 80% F (caller must have
        ``barrier_appetite=True`` — gated upstream).
      - LONG_CALL → KI_CALL at 110% F with B at 120% F (same gate).
      - KI_PUT / KO_PUT / KI_CALL / KO_CALL → push the existing strike
        and barrier proportionally further OTM.
    """
    F = _forward(regime.spot, regime.risk_free_rate, regime.dividend_yield, T)

    # Existing-barrier candidates: push strike + barrier deeper.
    if cand.kind in (
        StructureKind.KI_PUT, StructureKind.KO_PUT,
        StructureKind.KI_CALL, StructureKind.KO_CALL,
    ):
        leg = cand.legs[0] if cand.legs else None
        if leg is None or leg.barrier_level is None:
            return None
        is_put = leg.option_type.endswith("_put")
        if is_put:
            new_K = _round_strike(F * 0.88)  # 12% OTM-on-forward
            new_B = _round_strike(F * 0.70)  # 30% OTM-on-forward
            if new_B >= new_K:
                new_B = _round_strike(min(new_K * 0.85, new_K - 1.0))
        else:
            new_K = _round_strike(F * 1.12)
            new_B = _round_strike(F * 1.30)
            if new_B <= new_K:
                new_B = _round_strike(max(new_K * 1.15, new_K + 1.0))
        if new_K == leg.strike and new_B == leg.barrier_level:
            return None
        new_leg = leg.model_copy(update={"strike": new_K, "barrier_level": new_B})
        return Candidate(
            candidate_id=cand.candidate_id,
            kind=cand.kind,
            name=f"{cand.kind.value.upper()} K={new_K:.0f} B={new_B:.0f} (deep OTM)",
            legs=[new_leg],
            rationale=cand.rationale,
            hedging_cost_premium_bps=cand.hedging_cost_premium_bps,
            notional_usd=cand.notional_usd,
        )

    # Long-vanilla: convert to barrier at deep-OTM strike (gated externally
    # on barrier_appetite — see ``_rescue_for_budget._transforms``).
    if cand.kind == StructureKind.LONG_PUT:
        long_leg = next(
            (l for l in cand.legs if l.option_type == "european_put" and l.quantity > 0),
            None,
        )
        if long_leg is None:
            return None
        new_K = _round_strike(F * 0.88)
        new_B = _round_strike(F * 0.70)
        if new_B >= new_K:
            new_B = _round_strike(min(new_K * 0.85, new_K - 1.0))
        new_leg = long_leg.model_copy(update={
            "option_type": "knockin_put",
            "strike": new_K,
            "barrier_level": new_B,
            "barrier_monitoring": "continuous",
            "role": "long_ki_put",
        })
        return Candidate(
            candidate_id=cand.candidate_id,
            kind=StructureKind.KI_PUT,
            name=f"KI Put K={new_K:.0f} B={new_B:.0f} (deep OTM)",
            legs=[new_leg],
            rationale=cand.rationale,
            hedging_cost_premium_bps=6.0,
            notional_usd=cand.notional_usd,
        )

    if cand.kind == StructureKind.LONG_CALL:
        long_leg = next(
            (l for l in cand.legs if l.option_type == "european_call" and l.quantity > 0),
            None,
        )
        if long_leg is None:
            return None
        new_K = _round_strike(F * 1.12)
        new_B = _round_strike(F * 1.30)
        if new_B <= new_K:
            new_B = _round_strike(max(new_K * 1.15, new_K + 1.0))
        new_leg = long_leg.model_copy(update={
            "option_type": "knockin_call",
            "strike": new_K,
            "barrier_level": new_B,
            "barrier_monitoring": "continuous",
            "role": "long_ki_call",
        })
        return Candidate(
            candidate_id=cand.candidate_id,
            kind=StructureKind.KI_CALL,
            name=f"KI Call K={new_K:.0f} B={new_B:.0f} (deep OTM)",
            legs=[new_leg],
            rationale=cand.rationale,
            hedging_cost_premium_bps=6.0,
            notional_usd=cand.notional_usd,
        )

    return None


def _tighten_collar_cap(
    cand: Candidate, regime: MarketRegime, T: float,
    *,
    sigma: Optional[float] = None,
    budget_bps: Optional[float] = None,
) -> Optional[Candidate]:
    """For COLLAR or ZERO_COST_COLLAR with a residual debit/credit gap:
    sweep BOTH legs over a small grid neighbourhood and pick the (put, call)
    pair whose quick-priced net sits closest to ``budget_bps`` (default 0).

    The Brent solve in ``_build_collar`` already places K_call optimally for
    the original K_put on a continuous strike — but rounding to a $1 grid
    can leave 5–15 bps of residual that the desk reads as "over zero-cost".
    A bilateral grid sweep (±2 steps on the call, ±1 step on the put,
    bounded by K_put < K_call) finds the discrete pair that minimises the
    residual, mirroring what a sales-trader would do at the desk.
    """
    if cand.kind not in (StructureKind.COLLAR, StructureKind.ZERO_COST_COLLAR):
        return None
    short_call = next(
        (l for l in cand.legs if l.option_type.endswith("_call") and l.quantity < 0),
        None,
    )
    long_put = next(
        (l for l in cand.legs if l.option_type.endswith("_put") and l.quantity > 0),
        None,
    )
    if short_call is None:
        return None

    if sigma is None:
        sigma = _regime_sigma(regime)
    target = float(budget_bps) if budget_bps is not None else 0.0

    call_step = _grid_step_for_strike(short_call.strike)
    # Sweep call: +0 .. -4 grid steps (tighten the cap, never widen).
    call_strikes = [
        _round_strike(short_call.strike - n * call_step)
        for n in range(0, 5)
    ]
    call_strikes = [k for k in call_strikes if k > 0]

    if long_put is not None:
        put_step = _grid_step_for_strike(long_put.strike)
        # Sweep put: -1, 0, +1 grid step (the put leg can move slightly to
        # rebalance — a more-OTM put cheapens, a less-OTM put richens).
        put_strikes = [
            _round_strike(long_put.strike + d * put_step)
            for d in (-1, 0, +1)
        ]
        put_strikes = [k for k in put_strikes if k > 0]
    else:
        put_strikes = [None]

    best_variant: Optional[Candidate] = None
    best_score = float("inf")
    orig_call = short_call.strike
    orig_put = long_put.strike if long_put is not None else None
    for kc in call_strikes:
        for kp in put_strikes:
            # Skip the no-op (== orig).
            if kc == orig_call and kp == orig_put:
                continue
            # Sanity: K_call must remain strictly above K_put.
            if kp is not None and kc <= kp:
                continue
            new_legs = []
            for leg in cand.legs:
                if leg is short_call:
                    new_legs.append(leg.model_copy(update={"strike": kc}))
                elif long_put is not None and leg is long_put:
                    new_legs.append(leg.model_copy(update={"strike": kp}))
                else:
                    new_legs.append(leg.model_copy())
            try:
                v_bps = _quick_price_bps(
                    new_legs, regime.spot, regime.risk_free_rate,
                    regime.dividend_yield, sigma, T,
                )
            except Exception:  # noqa: BLE001
                continue
            score = abs(v_bps - target)
            if score < best_score:
                best_score = score
                best_variant = _clone_candidate_with_legs(cand, new_legs)
    return best_variant


def _rescue_for_budget(
    candidates: list[Candidate], obj: ClientObjective, regime: MarketRegime,
) -> list[Candidate]:
    """Budget-aware post-processing pass.

    For each rule-built candidate:
      1. Quick-price via closed-form BS. If ``|bps| ≤ budget*1.2 + 10``, leave
         alone (within structurer tolerance).
      2. Otherwise, try transforms in order — tighten spread, push long
         further OTM, vanilla→barrier (gated on ``barrier_appetite``),
         tighten collar cap. Re-quick-price after each. Adopt the first
         variant within ``budget*1.1 + 10``.
      3. If all transforms fail (still > ``budget*1.2``), append a budget-
         infeasible note to ``candidate.rationale`` so the memo's per-
         candidate section explains the structurer's action.

    Telemetry: every successful transform appends a one-line note to
    ``rationale``. The PricingAgent re-prices exactly downstream — the
    rescue's role is purely candidate selection, never final pricing.
    """
    if not candidates:
        return candidates

    budget = max(0.0, float(obj.budget_bps_notional))
    T = _regime_T(obj)
    sigma = _regime_sigma(regime)
    spot = regime.spot
    r = regime.risk_free_rate
    q = regime.dividend_yield

    # Rescue acceptance must match (not exceed) the validator's hard cap
    # ``budget + _BUDGET_TOLERANCE_BPS = budget + 10``. If we accept a
    # variant outside that, the validator will BLOCK it on the QL re-price
    # and we've spent rescue effort for nothing. Both the initial "leave
    # alone" threshold and the transform-acceptance threshold are kept at
    # the same ``budget + 10`` line — there's no value in skipping
    # transforms on a candidate the validator will reject.
    accept_mult = 1.0
    accept_slack = 10.0
    initial_mult = 1.0
    initial_slack = 10.0

    # Build the transform pipeline (each returns Optional[Candidate]).
    # Order matters: cheapest structural change first; then progressively
    # more aggressive (deep-OTM, vanilla→barrier, deep-OTM barrier).
    def _transforms(c: Candidate) -> list[tuple[str, Optional[Candidate]]]:
        seq: list[tuple[str, Optional[Candidate]]] = []
        seq.append(("tightened spread", _tighten_spread(c)))
        seq.append(("pushed long leg further OTM", _push_long_otm(c, regime, T)))
        if obj.barrier_appetite:
            seq.append(("converted vanilla to barrier", _convert_vanilla_to_barrier(c, regime, T)))
            # Stacked: push the long leg of an existing barrier candidate
            # further OTM and / or convert + push for vanilla candidates.
            # This handles SPY-365d-style cases where the 5%-OTM barrier
            # variant is still above budget.
            seq.append((
                "pushed barrier strike deep OTM",
                _push_barrier_strike_deep(c, regime, T),
            ))
        seq.append((
            "tightened collar cap",
            _tighten_collar_cap(c, regime, T, sigma=sigma, budget_bps=budget),
        ))
        return seq

    out: list[Candidate] = []
    for cand in candidates:
        try:
            quick_bps = _quick_price_bps(cand.legs, spot, r, q, sigma, T)
        except Exception:  # noqa: BLE001
            out.append(cand)
            continue

        if _within_tolerance(quick_bps, budget, initial_mult, initial_slack):
            out.append(cand)
            continue

        # Try transforms in order until one fits.
        adopted: Optional[Candidate] = None
        adopted_label: str = ""
        adopted_bps: float = quick_bps
        for label, variant in _transforms(cand):
            if variant is None:
                continue
            try:
                v_bps = _quick_price_bps(variant.legs, spot, r, q, sigma, T)
            except Exception:  # noqa: BLE001
                continue
            if _within_tolerance(v_bps, budget, accept_mult, accept_slack):
                adopted = variant
                adopted_label = label
                adopted_bps = v_bps
                break

        if adopted is not None:
            note = (
                f"\n[budget rescue: {adopted_label} "
                f"(quick-priced {adopted_bps:+.0f}bps vs budget {budget:.0f}bps)]"
            )
            adopted.rationale = adopted.rationale + note
            out.append(adopted)
            continue

        # Still infeasible — keep original but append the structurer note.
        excess = max(0.0, abs(quick_bps) - budget)
        note = (
            f"\nBudget-infeasible note: at requested participation, premium is "
            f"~{abs(quick_bps):.0f}bps; consider raising budget by "
            f"~{excess:.0f}bps, accepting barrier risk, or accepting tighter "
            f"participation."
        )
        try:
            cand.rationale = cand.rationale + note
        except Exception:  # noqa: BLE001 — defensive only
            pass
        out.append(cand)

    return out
