"""Stress test — Scenario 1: XLK mildly_bullish $150M 9mo 90bps no-barrier.

Drives the structuring co-pilot end-to-end through the IntakeAgent →
Strategist → Gates A/B/C pipeline in DEMO_REPLAY mode, then runs an
independent Monte-Carlo overlay against each priced European leg of the
recommended candidate to triangulate the QuantLib analytical price.

Modeled on tests/test_copilot_scenarios.py::_run_scenario.
"""

from __future__ import annotations

import json
import os
import sys
import math
from typing import Any
from unittest.mock import patch

# CRITICAL: env vars before any src.* import.
os.environ["DEMO_REPLAY"] = "1"
os.environ["GEMINI_API_KEY"] = ""

# Allow `python tests/stress/scenario_01.py` from repo root.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import numpy as np  # noqa: E402

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
from src.engines import router  # noqa: E402

# ---------------------------------------------------------------------------
# Replay + market patch helpers (mirrors tests/test_copilot_scenarios.py)
# ---------------------------------------------------------------------------


def _install_intake_replay(intake_payload: dict[str, Any]) -> None:
    client = llm_client.get_llm_client()
    if client._replay_cache is None:  # noqa: SLF001
        client._load_replay_cache()  # noqa: SLF001
    client._replay_cache["IntakeAgent:nl"] = {  # noqa: SLF001
        "text": json.dumps(intake_payload),
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
# MC overlay — vanilla European legs only
# ---------------------------------------------------------------------------


def mc_european(
    S: float, K: float, r: float, q: float, sigma: float, T: float,
    opt: str, n_paths: int = 50_000, seed: int = 1234,
) -> float:
    """50k-path GBM terminal payoff, antithetic. Returns price per share."""
    rng = np.random.default_rng(seed)
    Z = rng.standard_normal(n_paths // 2)
    Z = np.concatenate([Z, -Z])
    drift = (r - q - 0.5 * sigma * sigma) * T
    diffusion = sigma * math.sqrt(T)
    S_T = S * np.exp(drift + diffusion * Z)
    if opt == "call":
        payoff = np.maximum(S_T - K, 0.0)
    else:
        payoff = np.maximum(K - S_T, 0.0)
    return float(math.exp(-r * T) * payoff.mean())


def mc_american_via_router(
    S: float, K: float, r: float, q: float, sigma: float, T: float,
    opt_type: str,
) -> float:
    pricer, _, _ = router.route_with_engine(opt_type, engine="mc")
    price, _, _ = pricer(S, K, r, sigma, T, q, n_paths=20_000, n_steps=120,
                         variance_reduction="antithetic")
    return float(price)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main() -> int:
    agent_config.reload()
    llm_client.reset_llm_client()

    rfq = (
        "Multi-family office, $150M long XLK, mildly bullish on AI/cloud "
        "earnings cycle next 9 months. Want call-side participation but not "
        "unbounded debit. 90bps budget. No barriers."
    )
    intake = {
        "underlying": "XLK",
        "notional_usd": 150_000_000,
        "view": "mildly_bullish",
        "horizon_days": 270,
        "budget_bps_notional": 90,
        "premium_tolerance": "medium",
        "capped_upside_ok": False,
        "barrier_appetite": False,
        "constraints": ["no barriers"],
        "clarifications_needed": [],
    }
    spot = 245.0
    vol_30d = 0.22
    vol_90d = 0.20
    div = 0.008

    _install_intake_replay(intake)
    with _fake_market(spot=spot, vol_30d=vol_30d, vol_90d=vol_90d, div=div):
        orch = OrchestratorAgent(store=SessionStore())
        session = orch.start_session(intake_nl=rfq)
        if session.status != SessionStatus.AWAITING_GATE_A:
            print(f"FAIL: intake landed in {session.status} (err={session.last_error})")
            return 2

        session = orch.decide_gate(session.session_id, Gate.A, approved=True)
        if session.status != SessionStatus.AWAITING_GATE_B:
            print(f"FAIL: Gate A → {session.status} (err={session.last_error})")
            return 2

        session = orch.decide_gate(session.session_id, Gate.B, approved=True)
        if session.status != SessionStatus.AWAITING_GATE_C:
            print(f"FAIL: Gate B → {session.status} (err={session.last_error})")
            return 2

        session = orch.decide_gate(session.session_id, Gate.C, approved=True)
        if session.status != SessionStatus.DONE:
            print(f"WARN: Gate C → {session.status}")

    # ------------------------------------------------------------------
    # Inspect outputs
    # ------------------------------------------------------------------
    regime = session.regime
    memo = session.memo
    priced = session.priced
    validator = session.validator

    # Force UTF-8 stdout so we can print Greeks without cp1252 errors on Win.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    print("=" * 80)
    print("STRESS TEST  Scenario 1 - XLK mildly_bullish $150M 9mo 90bps no-barrier")
    print("=" * 80)
    print(f"\nRegime: spot={regime.spot} sig30={regime.realised_vol_30d} "
          f"sig90={regime.realised_vol_90d} q={regime.dividend_yield:.4f} "
          f"r={regime.risk_free_rate:.4f} regime={regime.vol_regime}")
    print(f"\nVERDICT: {memo.title}")

    # Recommended candidate
    rec_id = memo.recommended_candidate_id
    rec = next((p for p in priced if p.candidate.candidate_id == rec_id), None)
    if rec is None:
        print(f"FAIL: recommended_candidate_id {rec_id} not in priced list")
        return 2

    print(f"\nRecommended kind = {rec.candidate.kind.value}")
    print(f"Legs:")
    for i, leg in enumerate(rec.candidate.legs):
        bar = f" B={leg.barrier_level}" if leg.barrier_level else ""
        print(f"  [{i}] qty={leg.quantity:+.2f} {leg.option_type} K={leg.strike}"
              f" T={leg.expiry_days}d{bar}  per_share=${rec.per_leg_prices[i]:.4f}")
    print(f"  method = {rec.method_label}")
    budget_bps = 90.0
    over_under = rec.net_premium_bps - budget_bps
    print(f"\nNet premium: ${rec.net_premium/1e6:.2f}M "
          f"({rec.net_premium_bps:.1f} bps) vs budget {budget_bps:.0f}bps  "
          f"→ {'OVER' if over_under > 0 else 'UNDER'} by {abs(over_under):.1f} bps")
    print(f"Greeks (per-share, then USD-scaled):")
    g = rec.greeks
    scale = rec.candidate.notional_usd / regime.spot
    print(f"  Delta = {g.delta:+.4f}/sh  -> ${g.delta*scale/1e6:+.2f}M / $1 spot")
    print(f"  Gamma = {g.gamma:+.6f}/sh")
    print(f"  Vega  = {g.vega:+.4f}/sh per 1%vol -> ${g.vega*scale/1e4:+.2f}M / 1vol pt")
    print(f"  Theta = {g.theta:+.4f}/sh per day  -> ${g.theta*scale/1e3:+.2f}k/day")
    print(f"  Rho   = {g.rho:+.4f}/sh per 1%r")

    # ------------------------------------------------------------------
    # MC overlay vs QL on each leg
    # ------------------------------------------------------------------
    print("\n" + "-" * 80)
    print("MC OVERLAY vs QuantLib (per-share prices)")
    print("-" * 80)
    print(f"{'Leg':<4}{'Type':<18}{'K':>8}{'B':>8}  {'QL':>10}{'MC':>10}{'d ($)':>10}{'d (bps S)':>11}")
    sigma_used = regime.realised_vol_30d  # PricingAgent picks 30d when present
    r_used = regime.risk_free_rate
    q_used = regime.dividend_yield
    deltas_bps = []
    for i, leg in enumerate(rec.candidate.legs):
        T = leg.expiry_days / 365.0
        ql_price = rec.per_leg_prices[i]
        bar = "-"
        if leg.option_type.startswith("european_"):
            opt = leg.option_type.split("_")[1]
            mc = mc_european(spot, leg.strike, r_used, q_used, sigma_used, T,
                             opt=opt, n_paths=50_000, seed=4321 + i)
            method = "MC-EU"
        elif leg.option_type.startswith("american_"):
            mc = mc_american_via_router(spot, leg.strike, r_used, q_used,
                                         sigma_used, T, leg.option_type)
            method = "MC-AM"
        elif leg.option_type.startswith(("knockout_", "knockin_")):
            mc = float("nan")
            method = "QL-only"
            bar = f"{leg.barrier_level}"
        else:
            mc = float("nan")
            method = "skip"

        if math.isnan(mc):
            print(f"{i:<4}{leg.option_type:<18}{leg.strike:>8.1f}{bar:>8}  "
                  f"{ql_price:>10.4f}{'n/a':>10}{'n/a':>10}{'n/a':>11}  ({method})")
        else:
            d_dollar = mc - ql_price
            d_bps = (d_dollar / spot) * 10_000.0
            deltas_bps.append((i, d_bps))
            print(f"{i:<4}{leg.option_type:<18}{leg.strike:>8.1f}{bar:>8}  "
                  f"{ql_price:>10.4f}{mc:>10.4f}{d_dollar:>+10.4f}{d_bps:>+11.2f}  ({method})")

    # ------------------------------------------------------------------
    # Comparison table (10-col) and validator findings
    # ------------------------------------------------------------------
    print("\n" + "-" * 80)
    print("Comparison table (verbatim from memo)")
    print("-" * 80)
    print(memo.comparison_table_md)

    print("-" * 80)
    print("Validator findings")
    print("-" * 80)
    if not validator or not validator.findings:
        print("  (no findings)")
    else:
        for f in validator.findings:
            print(f"  [{f.severity.value.upper():>5}] {f.name}: {f.message}")

    print("-" * 80)
    print("Caveats")
    print("-" * 80)
    for c in memo.caveats:
        print(f"  • {c}")

    print("-" * 80)
    print("Recommendation (first 800 chars)")
    print("-" * 80)
    print(memo.recommendation_md[:800])

    # ------------------------------------------------------------------
    # PASS/FAIL summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("MC vs QL deltas summary (bps of spot, per-share):")
    for i, d in deltas_bps:
        print(f"  leg{i}: {d:+.2f} bps")
    print("=" * 80)

    return 0


if __name__ == "__main__":
    sys.exit(main())
