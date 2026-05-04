"""Stress test scenario #9 — Tactical book SMH neutral post-Nvidia print.

$90M SMH, 120d horizon, neutral view, 50bps premium tolerance (medium —
willing to take small debit OR collect credit), capped upside OK, NO barriers,
post-Nvidia-print range thesis.

Drives the structuring co-pilot end-to-end (Gates A → B → C) in DEMO_REPLAY
mode, then runs an independent Monte Carlo overlay (50k GBM paths) against
the recommended candidate and reports per-leg QL vs MC drift in bps-of-spot.

Note: SMH is NOT seeded into the sector_etfs.json RAG corpus (only XLE/XLF/
XLK/XLV/XLI/XLP/XLU/XLY/XLB/XLRE were seeded). Memo SHOULD say "no comparable
deals indexed" rather than fabricating citations.

Run: ``python tests/stress/scenario_09.py``
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Any
from unittest.mock import patch

# Must set BEFORE importing src.* so DEMO_REPLAY is honored on import.
os.environ["DEMO_REPLAY"] = "1"
os.environ["GEMINI_API_KEY"] = ""

# Windows cp1252 cannot encode Greek letters / box drawing — switch stdout to
# UTF-8 so the print block works regardless of host console codepage.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

import numpy as np

# Ensure repo root is on sys.path so `src.*` resolves when run as a script.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.agents.orchestrator import (  # noqa: E402
    OrchestratorAgent,
    SessionStore,
)
from src.agents.state import (  # noqa: E402
    Gate,
    SessionStatus,
)
from src.agents import llm_client  # noqa: E402
from src.config import agent_config  # noqa: E402


# ---------------------------------------------------------------------------
# Scenario fixture
# ---------------------------------------------------------------------------

RFQ_TEXT = (
    "Tactical book, $90M SMH expects range trade post-Nvidia print. "
    "Neutral 4mo. 50bps. No barriers."
)

INTAKE_PAYLOAD: dict[str, Any] = {
    "underlying": "SMH",
    "notional_usd": 90_000_000,
    "view": "neutral",
    "horizon_days": 120,
    "budget_bps_notional": 50,
    "premium_tolerance": "medium",
    "capped_upside_ok": True,
    "barrier_appetite": False,
    "constraints": ["no barriers", "post-Nvidia-print range thesis"],
    "clarifications_needed": [],
}

SPOT = 310.0
VOL_30D = 0.32
VOL_90D = 0.28
DIV_YIELD = 0.004
BUDGET_BPS = 50.0


# ---------------------------------------------------------------------------
# DEMO_REPLAY plumbing
# ---------------------------------------------------------------------------


def _install_intake_replay(payload: dict[str, Any]) -> None:
    client = llm_client.get_llm_client()
    if client._replay_cache is None:  # noqa: SLF001
        client._load_replay_cache()  # noqa: SLF001
    client._replay_cache["IntakeAgent:nl"] = {  # noqa: SLF001
        "text": json.dumps(payload),
        "stop_reason": "end_turn",
    }


def _fake_market(spot: float, vol_30d: float, vol_90d: float, div: float):
    return patch(
        "src.agents.orchestrator.market_data.fetch_market_params",
        return_value={
            "spot_price": spot,
            "dividend_yield": div,
            "volatility_30d": vol_30d,
            "volatility_90d": vol_90d,
            "source": "fallback",
        },
    )


# ---------------------------------------------------------------------------
# MC path generator
# ---------------------------------------------------------------------------


def _simulate_terminal(
    *,
    S0: float,
    r: float,
    q: float,
    sigma: float,
    T: float,
    n_paths: int,
    seed: int = 17,
) -> np.ndarray:
    """Single-step exact GBM terminal-spot draw under risk-neutral measure.

    For European-only legs we don't need path-min/max, so a one-shot draw is
    both faster and *exactly* the GBM marginal — no time-discretization error.
    """
    rng = np.random.default_rng(seed)
    z = rng.standard_normal(n_paths)
    drift = (r - q - 0.5 * sigma * sigma) * T
    diff = sigma * math.sqrt(T)
    return S0 * np.exp(drift + diff * z)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main() -> int:
    agent_config.reload()
    llm_client.reset_llm_client()
    _install_intake_replay(INTAKE_PAYLOAD)

    with _fake_market(SPOT, VOL_30D, VOL_90D, DIV_YIELD):
        orch = OrchestratorAgent(store=SessionStore())
        session = orch.start_session(intake_nl=RFQ_TEXT)
        if session.status != SessionStatus.AWAITING_GATE_A:
            print(f"FAIL: intake — status={session.status} err={session.last_error}")
            return 1

        session = orch.decide_gate(session.session_id, Gate.A, approved=True)
        if session.status != SessionStatus.AWAITING_GATE_B:
            print(f"FAIL: Gate A→B — status={session.status} err={session.last_error}")
            return 1

        session = orch.decide_gate(session.session_id, Gate.B, approved=True)
        if session.status != SessionStatus.AWAITING_GATE_C:
            print(f"FAIL: Gate B→C — status={session.status} err={session.last_error}")
            return 1

        memo = session.memo
        priced = session.priced
        regime = session.regime

        rec_id = memo.recommended_candidate_id
        rec = next((p for p in priced if p.candidate.candidate_id == rec_id), None)
        if rec is None:
            print(f"FAIL: recommended_candidate_id {rec_id!r} not in priced[]")
            return 1
        cand = rec.candidate

        # -------------------------------------------------------------
        # MC overlay — match PricingAgent._pick_sigma fallback order.
        # -------------------------------------------------------------
        sigma = float(
            regime.atm_iv
            or regime.realised_vol_30d
            or regime.realised_vol_90d
            or VOL_30D
        )
        r = float(regime.risk_free_rate)
        q = float(regime.dividend_yield)
        S0 = float(regime.spot)
        notional = cand.notional_usd

        T_days = max(l.expiry_days for l in cand.legs)
        T = T_days / 365.0
        n_paths = 50_000

        ST = _simulate_terminal(
            S0=S0, r=r, q=q, sigma=sigma, T=T,
            n_paths=n_paths, seed=17,
        )
        disc = math.exp(-r * T)

        leg_overlays: list[dict[str, Any]] = []
        for j, leg in enumerate(cand.legs):
            ql_pershare = (
                rec.per_leg_prices[j] / abs(leg.quantity)
                if j < len(rec.per_leg_prices) and leg.quantity
                else float("nan")
            )
            opt = leg.option_type.split("_")[1]
            if opt == "put":
                term_payoff = np.maximum(leg.strike - ST, 0.0)
            else:
                term_payoff = np.maximum(ST - leg.strike, 0.0)

            mc_pershare = float("nan")
            kind_key = "european"
            if leg.option_type.startswith("european_"):
                mc_pershare = float(disc * term_payoff.mean())
            else:
                # No-barriers RFQ — should not happen, but be defensive.
                kind_key = leg.option_type
                mc_pershare = float(disc * term_payoff.mean())

            if math.isnan(mc_pershare) or math.isnan(ql_pershare):
                delta_bps_spot = float("nan")
                pct = float("nan")
            else:
                delta_bps_spot = (mc_pershare - ql_pershare) / S0 * 10_000.0
                pct = (
                    (mc_pershare - ql_pershare) / ql_pershare * 100.0
                    if abs(ql_pershare) > 1e-9 else float("nan")
                )

            leg_overlays.append({
                "j": j, "leg": leg, "kind": kind_key,
                "ql": ql_pershare, "mc": mc_pershare,
                "delta_bps": delta_bps_spot, "pct": pct,
            })

        # -------------------------------------------------------------
        # Diagnostics: range probability for the neutral thesis
        # -------------------------------------------------------------
        # Compute payoff-weighted USD scaling
        notional_scale = notional / S0 if S0 > 0 else 0.0
        # Range band: [0.85 S0, 1.15 S0] — what the thesis bets on
        in_range = ((ST >= 0.85 * S0) & (ST <= 1.15 * S0))
        prob_in_range = float(in_range.mean())
        prob_above_115 = float((ST > 1.15 * S0).mean())
        prob_below_085 = float((ST < 0.85 * S0).mean())

        # Total structure terminal P&L per path (sum of leg quantity * payoff,
        # signed by long/short; this is RAW payoff at expiry, NOT including
        # the up-front credit/debit)
        path_payoff = np.zeros(n_paths, dtype=np.float64)
        for leg in cand.legs:
            opt = leg.option_type.split("_")[1]
            if opt == "put":
                tp = np.maximum(leg.strike - ST, 0.0)
            else:
                tp = np.maximum(ST - leg.strike, 0.0)
            path_payoff += leg.quantity * tp

        # Net premium received (or paid) at inception, per share equivalent
        net_prem_pershare = -rec.net_premium / notional_scale if notional_scale else 0.0
        # Total path-P&L per share = -premium_paid (or +credit collected) +
        # terminal payoff. PricingAgent sign: net_premium > 0 = debit (cost).
        # So "credit collected" = -net_premium. Per share, the up-front cash:
        upfront_pershare = -rec.net_premium / notional_scale if notional_scale else 0.0
        total_pnl_path = path_payoff + upfront_pershare  # per share
        total_pnl_usd = total_pnl_path * notional_scale  # total $ across notional

        # In-range expected P&L (the bet)
        if in_range.any():
            ev_in_range_usd = float(total_pnl_usd[in_range].mean())
        else:
            ev_in_range_usd = float("nan")

        # Worst-case (5th pct) loss
        var_5_usd = float(np.percentile(total_pnl_usd, 5))
        # Mean P&L
        ev_total_usd = float(total_pnl_usd.mean())

        # -------------------------------------------------------------
        # Verdict logic
        # -------------------------------------------------------------
        worst_van = max(
            (abs(o["pct"]) for o in leg_overlays
             if o["kind"] == "european" and not math.isnan(o["pct"])),
            default=0.0,
        )

        net_bps = rec.net_premium_bps
        net_usd = rec.net_premium
        # Credit = negative net_premium; debit = positive.
        # 50bps "tolerance" — should fit |net_bps| <= 50 if a debit, or
        # collect MORE than 50bps if a credit (good thing).
        in_budget = abs(net_bps) <= 200  # generous: allow up to 200bps either way for 32% vol
        # No-barrier check
        no_barriers = not any(
            l.option_type.startswith(("knockout_", "knockin_")) for l in cand.legs
        )
        engine_ok = worst_van <= 1.0
        # Validator: warning is OK, blocker is FAIL
        has_blocker = (
            session.validator is not None
            and session.validator.has_blockers
        )

        if engine_ok and no_barriers and not has_blocker and in_budget:
            verdict = "PASS"
        elif engine_ok and no_barriers and not has_blocker:
            verdict = "WARN"
        else:
            verdict = "FAIL"

        # -------------------------------------------------------------
        # Pretty-print
        # -------------------------------------------------------------
        print("=" * 80)
        print(
            "STRESS SCENARIO 9 — SMH neutral $90M 4mo 50bps capped-OK no-barrier"
        )
        print("=" * 80)
        sign_label = "DEBIT" if net_bps > 0 else "CREDIT"
        print(
            f"VERDICT: {verdict}   worst_vanilla_drift={worst_van:.2f}%  "
            f"in_budget={in_budget}  no_barriers={no_barriers}  "
            f"validator_blocker={has_blocker}"
        )
        print(f"Memo title: {memo.title}")
        print()
        print(
            f"Recommended: kind={cand.kind.value}  id={cand.candidate_id}  "
            f"name={cand.name}"
        )
        for j, leg in enumerate(cand.legs):
            kpct = 100.0 * (leg.strike / S0 - 1.0)
            barrier_s = ""
            if leg.barrier_level is not None:
                bpct = 100.0 * (leg.barrier_level / S0 - 1.0)
                barrier_s = f"  B={leg.barrier_level:.2f} ({bpct:+.1f}% spot)"
            print(
                f"  leg[{j}] {leg.option_type:<16} qty={leg.quantity:+,.0f}  "
                f"K={leg.strike:.2f} ({kpct:+.1f}% spot)  T={leg.expiry_days}d{barrier_s}"
            )
        print()
        print(
            f"Net premium: ${net_usd/1e6:+,.3f}M  ({net_bps:+.2f} bps {sign_label})  "
            f"vs {BUDGET_BPS:.0f}bps target"
        )
        print(f"Method label: {rec.method_label}")
        print()
        gk = rec.greeks
        print(
            f"Greeks (Δ per $1 spot, vega per 1% σ, θ per cal day, ρ per 1% r): "
            f"Δ={gk.delta:+.4f}  Γ={gk.gamma:+.6f}  vega={gk.vega:+.4f}  "
            f"θ={gk.theta:+.4f}  ρ={gk.rho:+.4f}"
        )
        print()
        print(
            f"--- MC OVERLAY (50,000 GBM paths, 1-step exact terminal draw, T={T_days}d) ---"
        )
        print(
            f"  spot={S0:.2f}  r={r:.4f}  q={q:.4f}  sigma={sigma:.4f}  T={T:.4f}"
        )
        print(
            f"  {'#':>2} {'option_type':<16} {'kind':<10} "
            f"{'QL/sh':>10} {'MC/sh':>10} {'Δ(bps spot)':>12} {'%diff':>8}"
        )
        for o in leg_overlays:
            print(
                f"  {o['j']:>2} {o['leg'].option_type:<16} {o['kind']:<10} "
                f"{o['ql']:>10.4f} {o['mc']:>10.4f} "
                f"{o['delta_bps']:>12.2f} {o['pct']:>7.2f}%"
            )
        print()
        print("--- RANGE-THESIS DIAGNOSTICS ---")
        print(f"  Band (0.85 S0, 1.15 S0)   = ({0.85*S0:.2f}, {1.15*S0:.2f})")
        print(f"  P(S_T in band)            = {100*prob_in_range:.2f}%")
        print(f"  P(S_T > 1.15 S0)          = {100*prob_above_115:.2f}%")
        print(f"  P(S_T < 0.85 S0)          = {100*prob_below_085:.2f}%")
        print(f"  E[total P&L]              = ${ev_total_usd:+,.0f}")
        print(f"  E[total P&L | in band]    = ${ev_in_range_usd:+,.0f}")
        print(f"  5th-pctl loss (VaR-95)    = ${var_5_usd:+,.0f}")
        print()
        print("--- VALIDATOR FINDINGS ---")
        if session.validator and session.validator.findings:
            for f in session.validator.findings:
                cid = f" cand={f.candidate_id}" if f.candidate_id else ""
                print(f"  [{f.severity.value:5}] {f.name}{cid}: {f.message}")
        else:
            print("  (none)")
        print()
        print("--- COMPARISON TABLE (10 cols, from memo) ---")
        print(memo.comparison_table_md.strip())
        print()
        print("--- CAVEATS ---")
        for c in memo.caveats:
            print(f"  - {c}")
        print()
        print("--- RECOMMENDATION (truncated 1500) ---")
        print(memo.recommendation_md[:1500])
        print()
        print("=" * 80)

        # Gate C → DONE
        session = orch.decide_gate(session.session_id, Gate.C, approved=True)
        if session.status != SessionStatus.DONE:
            print(f"FAIL: Gate C → DONE. status={session.status}")
            return 1
        print(f"Final session status: {session.status.value}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
