"""Stress test scenario #2 — XLE bearish $80M 6mo 60bps barrier-OK.

Drives the structuring co-pilot end-to-end (Gates A → B → C) in DEMO_REPLAY
mode, then runs an independent Monte Carlo overlay against each leg of the
recommended candidate to catch engine drift. Modeled on
``tests/test_copilot_scenarios.py::_run_scenario``.

Run:
    python tests/stress/scenario_02.py
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

# Force UTF-8 stdout on Windows so Greek letters/emojis don't crash cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

import numpy as np

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
    "Long-only commodities desk, $80M long XLE proxy. Concerned WTI "
    "roll-down + DXY strength = 6mo bearish. 60bps premium budget. "
    "Barriers OK as long as one-touch is documented."
)

INTAKE_PAYLOAD: dict[str, Any] = {
    "underlying": "XLE",
    "notional_usd": 80_000_000,
    "view": "bearish",
    "horizon_days": 180,
    "budget_bps_notional": 60,
    "premium_tolerance": "low",
    "capped_upside_ok": False,
    "barrier_appetite": True,
    "constraints": [],
    "clarifications_needed": [],
}

SPOT = 98.0
VOL_30D = 0.28
VOL_90D = 0.26
DIV_YIELD = 0.032


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
# Monte Carlo overlay
# ---------------------------------------------------------------------------


def _mc_european_leg(
    S0: float, K: float, r: float, q: float, sigma: float, T: float,
    opt: str, n_paths: int = 50_000, seed: int = 7,
) -> float:
    """Vanilla European GBM Monte Carlo. Returns per-share price."""
    rng = np.random.default_rng(seed)
    z = rng.standard_normal(n_paths)
    drift = (r - q - 0.5 * sigma * sigma) * T
    diff = sigma * math.sqrt(T)
    ST = S0 * np.exp(drift + diff * z)
    if opt == "call":
        payoff = np.maximum(ST - K, 0.0)
    else:
        payoff = np.maximum(K - ST, 0.0)
    return float(np.exp(-r * T) * payoff.mean())


def _mc_barrier_leg(
    S0: float, K: float, B: float, r: float, q: float, sigma: float, T: float,
    opt: str, kind: str, n_paths: int = 50_000, seed: int = 11,
) -> float:
    """Daily-monitored KO/KI barrier MC. Returns per-share price.

    ``kind`` is 'out' (KO) or 'in' (KI). Barrier direction (Down/Up) is
    inferred from spot vs barrier — matches the engine convention.
    """
    n_steps = max(2, int(round(252 * T * 365.0 / 365.0)))  # ~252*T trading days
    n_steps = max(2, int(round(252 * T)))
    dt = T / n_steps
    rng = np.random.default_rng(seed)
    drift = (r - q - 0.5 * sigma * sigma) * dt
    diff = sigma * math.sqrt(dt)

    is_down = B < S0  # Down barrier inferred from B<S0 (mirrors engine convention)

    # Simulate paths chunk-wise to keep memory in check.
    chunk = 5_000
    total_disc = 0.0
    total_n = 0
    for start in range(0, n_paths, chunk):
        n = min(chunk, n_paths - start)
        z = rng.standard_normal((n, n_steps))
        log_steps = drift + diff * z
        log_paths = np.cumsum(log_steps, axis=1)
        paths = S0 * np.exp(log_paths)
        # Barrier monitor on intermediate + terminal nodes (daily monitoring).
        if is_down:
            breached = (paths.min(axis=1) <= B)
        else:
            breached = (paths.max(axis=1) >= B)
        ST = paths[:, -1]
        if opt == "call":
            payoff = np.maximum(ST - K, 0.0)
        else:
            payoff = np.maximum(K - ST, 0.0)
        if kind == "out":
            payoff = np.where(breached, 0.0, payoff)
        else:  # 'in'
            payoff = np.where(breached, payoff, 0.0)
        total_disc += float(payoff.sum())
        total_n += n
    return math.exp(-r * T) * total_disc / total_n


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
            print(f"FAIL: intake stalled — status={session.status} err={session.last_error}")
            return 1

        session = orch.decide_gate(session.session_id, Gate.A, approved=True)
        if session.status != SessionStatus.AWAITING_GATE_B:
            print(f"FAIL: Gate A → B transition. status={session.status} err={session.last_error}")
            return 1

        session = orch.decide_gate(session.session_id, Gate.B, approved=True)
        if session.status != SessionStatus.AWAITING_GATE_C:
            print(f"FAIL: Gate B → C transition. status={session.status} err={session.last_error}")
            return 1

        memo = session.memo
        priced = session.priced
        regime = session.regime

        # ------------------------------------------------------------------
        # Pick recommended candidate
        # ------------------------------------------------------------------
        rec_id = memo.recommended_candidate_id
        rec = next((p for p in priced if p.candidate.candidate_id == rec_id), None)
        if rec is None:
            print(f"FAIL: recommended_candidate_id {rec_id!r} not found in priced[]")
            return 1

        # ------------------------------------------------------------------
        # MC overlay (per leg)
        # ------------------------------------------------------------------
        sigma = (regime.realised_vol_30d or regime.realised_vol_90d or 0.28)
        r = regime.risk_free_rate
        q = regime.dividend_yield
        S0 = regime.spot

        leg_overlays: list[dict[str, Any]] = []
        for j, leg in enumerate(rec.candidate.legs):
            T = leg.expiry_days / 365.0
            ql_price = rec.per_leg_prices[j] if j < len(rec.per_leg_prices) else float("nan")
            kind_key = "european"
            if leg.option_type.startswith("knockout_"):
                kind_key = "knockout"
            elif leg.option_type.startswith("knockin_"):
                kind_key = "knockin"
            elif leg.option_type.startswith("american_"):
                kind_key = "american"

            if leg.option_type.startswith("european_"):
                opt = leg.option_type.split("_")[1]
                mc_price = _mc_european_leg(S0, leg.strike, r, q, sigma, T, opt)
            elif leg.option_type.startswith(("knockout_", "knockin_")):
                opt = leg.option_type.split("_")[1]
                bkind = "out" if leg.option_type.startswith("knockout_") else "in"
                B = leg.barrier_level
                mc_price = _mc_barrier_leg(S0, leg.strike, B, r, q, sigma, T, opt, bkind)
            elif leg.option_type.startswith("american_"):
                # American MC is overkill here; use European as a lower bound proxy
                # and flag explicitly. (Bearish 6mo XLE is unlikely to use American.)
                opt = leg.option_type.split("_")[1]
                mc_price = _mc_european_leg(S0, leg.strike, r, q, sigma, T, opt)
                kind_key = "american (Eu proxy)"
            else:
                mc_price = float("nan")

            # Delta vs QL: in bps of spot (price per share / spot * 10000)
            if math.isnan(mc_price) or math.isnan(ql_price):
                delta_bps = float("nan")
                pct = float("nan")
            else:
                delta_bps = (mc_price - ql_price) / S0 * 10000.0
                pct = (mc_price - ql_price) / ql_price * 100.0 if abs(ql_price) > 1e-9 else float("nan")
            leg_overlays.append({
                "j": j,
                "leg": leg,
                "kind": kind_key,
                "ql": ql_price,
                "mc": mc_price,
                "delta_bps": delta_bps,
                "pct": pct,
            })

        # ------------------------------------------------------------------
        # Verdict
        # ------------------------------------------------------------------
        # Verdict: PASS if all vanillas <1% drift and barriers <3%; WARN if borderline.
        worst_van = 0.0
        worst_bar = 0.0
        for o in leg_overlays:
            if math.isnan(o["pct"]):
                continue
            if o["kind"] == "european":
                worst_van = max(worst_van, abs(o["pct"]))
            elif o["kind"] in ("knockout", "knockin"):
                worst_bar = max(worst_bar, abs(o["pct"]))

        verdict = "PASS"
        if worst_van > 1.0 or worst_bar > 3.0:
            verdict = "WARN"
        if worst_van > 5.0 or worst_bar > 10.0:
            verdict = "FAIL"

        budget = 60.0
        net_bps = rec.net_premium_bps
        net_usd = rec.net_premium
        delta_budget = net_bps - budget

        # ------------------------------------------------------------------
        # Pretty-print
        # ------------------------------------------------------------------
        print("=" * 78)
        print("STRESS SCENARIO 2 — XLE bearish $80M 6mo 60bps barrier-OK")
        print("=" * 78)
        print(f"VERDICT: {verdict}  (worst_vanilla={worst_van:.2f}%  worst_barrier={worst_bar:.2f}%)")
        print(f"Memo title: {memo.title}")
        print()
        print(
            f"Recommended: {rec.candidate.kind.value}  "
            f"(id={rec.candidate.candidate_id})"
        )
        for j, leg in enumerate(rec.candidate.legs):
            barrier_s = (
                f"  B={leg.barrier_level} ({leg.barrier_monitoring})"
                if leg.barrier_level is not None else ""
            )
            print(
                f"  leg[{j}] {leg.option_type}  qty={leg.quantity:+g}  "
                f"K={leg.strike}  T={leg.expiry_days}d{barrier_s}"
            )
        print()
        print(
            f"Net premium: ${net_usd/1e6:.2f}M  ({net_bps:.1f} bps)  "
            f"vs 60bps budget — {'OVER' if delta_budget > 0 else 'UNDER'} "
            f"by {abs(delta_budget):.1f} bps"
        )
        print()
        gk = rec.greeks
        print(
            f"Greeks (net, per $1 spot / per 1% σ / per cal day): "
            f"Δ={gk.delta:.4f}  Γ={gk.gamma:.5f}  vega={gk.vega:.4f}  "
            f"θ={gk.theta:.4f}  ρ={gk.rho:.4f}"
        )
        print()
        print("MC vs QL leg-by-leg:")
        print(f"  {'#':>2} {'option_type':<16} {'kind':<14} {'QL':>10} {'MC':>10} {'Δ(bps)':>10} {'%diff':>8}")
        for o in leg_overlays:
            print(
                f"  {o['j']:>2} {o['leg'].option_type:<16} {o['kind']:<14} "
                f"{o['ql']:>10.4f} {o['mc']:>10.4f} "
                f"{o['delta_bps']:>10.2f} {o['pct']:>7.2f}%"
            )
        print()
        print("Validator findings:")
        if session.validator and session.validator.findings:
            for f in session.validator.findings:
                cid = f" cand={f.candidate_id}" if f.candidate_id else ""
                print(f"  [{f.severity.value:5}] {f.name}{cid}: {f.message}")
        else:
            print("  (no findings)")
        print()
        print("Caveats:")
        for c in memo.caveats:
            print(f"  - {c}")
        print()
        print("Comparison table (memo):")
        print(memo.comparison_table_md)
        print()
        print("Recommendation MD (truncated 1200):")
        print(memo.recommendation_md[:1200])
        print()
        print("=" * 78)

        # Gate C → DONE (ensure full pipeline closes cleanly)
        session = orch.decide_gate(session.session_id, Gate.C, approved=True)
        print(f"Final session status: {session.status.value}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
