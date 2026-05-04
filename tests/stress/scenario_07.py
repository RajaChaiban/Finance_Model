"""Stress scenario #7 — Active manager IWM small-caps bullish post-Russell rebal.

$120M IWM, bullish view, 180d horizon, 150bps premium, no barriers, capped
upside NOT OK. Drives the co-pilot end-to-end (Gates A->B->C) in DEMO_REPLAY
mode, then runs a 50,000-path GBM Monte Carlo overlay against the recommended
candidate's per-leg QL prices.

Run: ``python tests/stress/scenario_07.py``
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

# Windows cp1252 cannot encode Greek letters in our Greeks line / table.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

import numpy as np  # noqa: E402

# Ensure the repo root is on sys.path so `src.*` resolves when run as a script.
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
    "Active equity manager, $120M IWM, bullish small-caps after Russell "
    "rebal. 6mo horizon. 150bps premium acceptable. No barriers."
)

INTAKE_PAYLOAD: dict[str, Any] = {
    "underlying": "IWM",
    "notional_usd": 120_000_000,
    "view": "bullish",
    "horizon_days": 180,
    "budget_bps_notional": 150,
    "premium_tolerance": "medium",
    "capped_upside_ok": False,
    "barrier_appetite": False,
    "constraints": ["no barriers", "post-Russell rebal momentum thesis"],
    "clarifications_needed": [],
}

SPOT = 225.0
VOL_30D = 0.24
VOL_90D = 0.22
DIV_YIELD = 0.011
BUDGET_BPS = 150.0


# ---------------------------------------------------------------------------
# DEMO_REPLAY plumbing (mirrors tests/test_copilot_scenarios.py)
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
# Monte Carlo path generator
# ---------------------------------------------------------------------------


def _simulate_paths(
    *,
    S0: float,
    r: float,
    q: float,
    sigma: float,
    T: float,
    n_paths: int,
    n_steps: int,
    seed: int = 17,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Simulate GBM paths under the risk-neutral measure.

    Returns:
        ST   (n_paths,)         terminal spot
        mins (n_paths,)         path-min over monitored steps
        maxs (n_paths,)         path-max over monitored steps
    """
    dt = T / n_steps
    rng = np.random.default_rng(seed)
    drift = (r - q - 0.5 * sigma * sigma) * dt
    diff = sigma * math.sqrt(dt)

    chunk = 5_000
    ST_full = np.empty(n_paths, dtype=np.float64)
    mins_full = np.empty(n_paths, dtype=np.float64)
    maxs_full = np.empty(n_paths, dtype=np.float64)
    cursor = 0
    while cursor < n_paths:
        n = min(chunk, n_paths - cursor)
        z = rng.standard_normal((n, n_steps))
        log_steps = drift + diff * z
        log_paths = np.cumsum(log_steps, axis=1)
        paths = S0 * np.exp(log_paths)
        ST_full[cursor:cursor + n] = paths[:, -1]
        mins_full[cursor:cursor + n] = paths.min(axis=1)
        maxs_full[cursor:cursor + n] = paths.max(axis=1)
        cursor += n
    return ST_full, mins_full, maxs_full


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
            print(
                f"FAIL: intake stalled — status={session.status} "
                f"err={session.last_error}"
            )
            return 1

        session = orch.decide_gate(session.session_id, Gate.A, approved=True)
        if session.status != SessionStatus.AWAITING_GATE_B:
            print(
                f"FAIL: Gate A -> B transition. status={session.status} "
                f"err={session.last_error}"
            )
            return 1

        session = orch.decide_gate(session.session_id, Gate.B, approved=True)
        if session.status != SessionStatus.AWAITING_GATE_C:
            print(
                f"FAIL: Gate B -> C transition. status={session.status} "
                f"err={session.last_error}"
            )
            return 1

        memo = session.memo
        priced = session.priced
        regime = session.regime

        # ------------------------------------------------------------------
        # Pick recommended candidate
        # ------------------------------------------------------------------
        rec_id = memo.recommended_candidate_id
        rec = next(
            (p for p in priced if p.candidate.candidate_id == rec_id),
            None,
        )
        if rec is None:
            print(f"FAIL: recommended_candidate_id {rec_id!r} not found in priced[]")
            return 1
        cand = rec.candidate

        # ------------------------------------------------------------------
        # MC overlay
        # ------------------------------------------------------------------
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
        n_steps = max(2, int(round(252.0 * T_days / 365.0)))
        n_paths = 50_000

        ST, mins, maxs = _simulate_paths(
            S0=S0, r=r, q=q, sigma=sigma, T=T,
            n_paths=n_paths, n_steps=n_steps, seed=17,
        )
        disc = math.exp(-r * T)

        leg_overlays: list[dict[str, Any]] = []
        for j, leg in enumerate(cand.legs):
            ql_pershare = (
                rec.per_leg_prices[j] / abs(leg.quantity)
                if j < len(rec.per_leg_prices) and leg.quantity
                else float("nan")
            )
            opt = leg.option_type.split("_")[1]  # 'call' | 'put'
            kind_key = "european"
            mc_pershare = float("nan")

            if opt == "put":
                term_payoff = np.maximum(leg.strike - ST, 0.0)
            else:
                term_payoff = np.maximum(ST - leg.strike, 0.0)

            if leg.option_type.startswith("european_"):
                mc_pershare = float(disc * term_payoff.mean())

            elif leg.option_type.startswith(("knockout_", "knockin_")):
                B = leg.barrier_level
                bkind = "out" if leg.option_type.startswith("knockout_") else "in"
                kind_key = "knockout" if bkind == "out" else "knockin"
                if B is None:
                    mc_pershare = float("nan")
                else:
                    is_down = B < S0
                    if is_down:
                        breached = (mins <= B)
                    else:
                        breached = (maxs >= B)
                    if bkind == "out":
                        payoff = np.where(breached, 0.0, term_payoff)
                    else:
                        payoff = np.where(breached, term_payoff, 0.0)
                    mc_pershare = float(disc * payoff.mean())

            elif leg.option_type.startswith("american_"):
                # Lower bound proxy (American >= European for puts, equal for
                # calls without div). Acceptable here for cross-check.
                mc_pershare = float(disc * term_payoff.mean())
                kind_key = "american (Eu proxy)"

            if math.isnan(mc_pershare) or math.isnan(ql_pershare):
                delta_bps_spot = float("nan")
                pct = float("nan")
            else:
                delta_bps_spot = (mc_pershare - ql_pershare) / S0 * 10_000.0
                pct = (
                    (mc_pershare - ql_pershare) / ql_pershare * 100.0
                    if abs(ql_pershare) > 1e-9
                    else float("nan")
                )

            leg_overlays.append({
                "j": j,
                "leg": leg,
                "kind": kind_key,
                "ql": ql_pershare,
                "mc": mc_pershare,
                "delta_bps": delta_bps_spot,
                "pct": pct,
            })

        # ------------------------------------------------------------------
        # Verdict logic
        # ------------------------------------------------------------------
        worst_van = 0.0
        worst_bar = 0.0
        for o in leg_overlays:
            if math.isnan(o["pct"]):
                continue
            if o["kind"] == "european":
                worst_van = max(worst_van, abs(o["pct"]))
            elif o["kind"] in ("knockout", "knockin"):
                worst_bar = max(worst_bar, abs(o["pct"]))

        net_bps = rec.net_premium_bps
        net_usd = rec.net_premium
        delta_budget = net_bps - BUDGET_BPS
        in_budget = net_bps <= BUDGET_BPS + 5  # 5bps tolerance

        engine_ok = (worst_van <= 1.0) and (worst_bar <= 5.0)

        # No-barrier mandate check
        any_barrier = any(
            l.option_type.startswith(("knockout_", "knockin_")) for l in cand.legs
        )
        # capped_upside_ok = False — flag short-call legs (call_spreads cap upside)
        caps_upside = any(
            l.option_type.endswith("_call") and l.quantity < 0 for l in cand.legs
        )

        if engine_ok and in_budget and not any_barrier:
            verdict = "PASS"
        elif engine_ok and not any_barrier:
            verdict = "WARN"
        else:
            verdict = "FAIL"

        # ------------------------------------------------------------------
        # Pretty-print
        # ------------------------------------------------------------------
        print("=" * 80)
        print(
            "STRESS SCENARIO 7 — IWM bullish $120M 6mo 150bps no-barrier"
        )
        print("=" * 80)
        print(
            f"VERDICT: {verdict}   "
            f"worst_vanilla_drift={worst_van:.2f}%  "
            f"worst_barrier_drift={worst_bar:.2f}%  "
            f"in_budget={in_budget}  any_barrier={any_barrier}  "
            f"caps_upside={caps_upside}"
        )
        print(f"Memo title: {memo.title}")
        print()
        print(
            f"Recommended: kind={cand.kind.value}  id={cand.candidate_id}  "
            f"name={cand.name}"
        )
        for j, leg in enumerate(cand.legs):
            barrier_s = ""
            if leg.barrier_level is not None:
                bpct = 100.0 * (leg.barrier_level / S0 - 1.0)
                barrier_s = (
                    f"  B={leg.barrier_level:.2f} ({bpct:+.1f}% spot, "
                    f"{leg.barrier_monitoring})"
                )
            kpct = 100.0 * (leg.strike / S0 - 1.0)
            print(
                f"  leg[{j}] {leg.option_type}  qty={leg.quantity:+,.0f}  "
                f"K={leg.strike:.2f} ({kpct:+.1f}% spot)  "
                f"T={leg.expiry_days}d{barrier_s}"
            )
        print()
        print(
            f"Net premium: ${net_usd/1e6:,.2f}M  ({net_bps:+.2f} bps)  "
            f"vs {BUDGET_BPS:.0f}bps budget — "
            f"{'OVER' if delta_budget > 0 else 'UNDER'} by "
            f"{abs(delta_budget):.2f} bps"
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
            "--- MC OVERLAY (50,000 GBM paths, "
            f"daily monitoring={n_steps} steps over {T_days}d) ---"
        )
        print(
            f"  spot={S0:.2f}  r={r:.4f}  q={q:.4f}  sigma={sigma:.4f}  T={T:.4f}"
        )
        print(
            f"  {'#':>2} {'option_type':<16} {'kind':<14} "
            f"{'QL/sh':>10} {'MC/sh':>10} {'Δ(bps spot)':>12} {'%diff':>8}"
        )
        for o in leg_overlays:
            print(
                f"  {o['j']:>2} {o['leg'].option_type:<16} {o['kind']:<14} "
                f"{o['ql']:>10.4f} {o['mc']:>10.4f} "
                f"{o['delta_bps']:>12.2f} {o['pct']:>7.2f}%"
            )
        print()
        # 1-sigma band sanity-check for the structurer's eye
        sd_T = sigma * math.sqrt(T) * S0
        print(
            f"  1-sigma band (σ·sqrt(T)·S0): ±${sd_T:.2f}  -> "
            f"[{S0-sd_T:.2f}, {S0+sd_T:.2f}]"
        )
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
        # All 3 candidates — needed to assess what strategist offered
        print("--- ALL 3 PRICED CANDIDATES ---")
        for p in priced:
            c = p.candidate
            print(
                f"  [{c.candidate_id}] {c.kind.value:<22} "
                f"premium={p.net_premium_bps:+7.2f}bps  "
                f"Δ={p.greeks.delta:+.3f}  vega={p.greeks.vega:+.3f}  "
                f"legs={len(c.legs)}"
            )
            for k, lg in enumerate(c.legs):
                kpct = 100.0 * (lg.strike / S0 - 1.0)
                print(
                    f"     leg[{k}] {lg.option_type:<16} qty={lg.quantity:+,.0f} "
                    f"K={lg.strike:.2f} ({kpct:+.1f}%)"
                )
        print()
        print("=" * 80)

        # Gate C → DONE
        session = orch.decide_gate(session.session_id, Gate.C, approved=True)
        if session.status != SessionStatus.DONE:
            print(f"FAIL: Gate C -> DONE. status={session.status}")
            return 1
        print(f"Final session status: {session.status.value}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
