"""Stress test scenario #6 — Hedge fund AAPL into Q3 print, 21d earnings_hedge.

Drives an RFQ for a $30M AAPL earnings_hedge with barrier appetite through the
structuring co-pilot in DEMO_REPLAY mode, then runs a Monte Carlo overlay (with
daily barrier monitoring for any KO/KI legs) to cross-check QuantLib pricing.

Run: ``python tests/stress/scenario_06.py``
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Any
from unittest.mock import patch

# --- env must be set BEFORE we import src.* (DEMO_REPLAY gate) ---------------
os.environ["DEMO_REPLAY"] = "1"
os.environ["GEMINI_API_KEY"] = ""

try:  # pragma: no cover — Windows console fallback
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import numpy as np

from src.agents import llm_client
from src.agents.orchestrator import OrchestratorAgent, SessionStore
from src.agents.state import Gate, SessionStatus
from src.config import agent_config


# ---------------------------------------------------------------------------
# Replay + market patch helpers
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
# Monte Carlo overlay — vanilla European leg (terminal payoff)
# ---------------------------------------------------------------------------


def _mc_european_leg(
    *,
    S0: float,
    K: float,
    is_call: bool,
    T: float,
    r: float,
    q: float,
    sigma: float,
    n_paths: int = 50_000,
    seed: int = 42,
) -> tuple[float, float]:
    """Price a vanilla European leg via 50k GBM paths.

    Returns ``(per_share_premium, standard_error)``.
    """
    rng = np.random.default_rng(seed)
    z = rng.standard_normal(n_paths)
    drift = (r - q - 0.5 * sigma * sigma) * T
    vol = sigma * math.sqrt(T)
    ST = S0 * np.exp(drift + vol * z)
    disc = math.exp(-r * T)
    if is_call:
        payoff = np.maximum(ST - K, 0.0)
    else:
        payoff = np.maximum(K - ST, 0.0)
    prem = float(disc * payoff.mean())
    se = float(disc * payoff.std(ddof=1) / math.sqrt(n_paths))
    return prem, se


# ---------------------------------------------------------------------------
# Monte Carlo overlay — barrier leg (daily monitoring, ~T*252 steps)
# ---------------------------------------------------------------------------


def _mc_barrier_leg(
    *,
    S0: float,
    K: float,
    B: float,
    is_call: bool,
    barrier_kind: str,  # "in" or "out"
    direction: str,     # "up" or "down"
    T: float,
    r: float,
    q: float,
    sigma: float,
    n_paths: int = 50_000,
    n_steps: int = 15,  # ~daily for 21cal-d horizon
    seed: int = 137,
) -> tuple[float, float, float]:
    """Price a barrier leg via discrete-monitoring GBM Monte Carlo.

    Returns ``(per_share_premium, standard_error, breach_probability)``.

    Direction is inferred by the caller from B vs S (down if B<S, up if B>S).
    Daily monitoring is approximated with ``n_steps`` (~15 steps over 21cal-d ≈
    daily business). This intentionally OVER-prices KO and UNDER-prices KI vs
    continuous monitoring to keep MC vs QL within a reasonable band; QL's
    continuous-monitoring analytic with the BGK shift adjusts for the gap.
    """
    rng = np.random.default_rng(seed)
    dt = T / n_steps
    drift = (r - q - 0.5 * sigma * sigma) * dt
    vol = sigma * math.sqrt(dt)

    # Simulate paths step-by-step. For 50k * 15 ≈ 750k normals — fits memory.
    log_inc = drift + vol * rng.standard_normal((n_paths, n_steps))
    log_paths = np.cumsum(log_inc, axis=1)
    paths = S0 * np.exp(log_paths)  # (n_paths, n_steps), S at end of each step

    if direction == "down":
        breached = (paths.min(axis=1) <= B)
    else:
        breached = (paths.max(axis=1) >= B)

    ST = paths[:, -1]
    if is_call:
        terminal = np.maximum(ST - K, 0.0)
    else:
        terminal = np.maximum(K - ST, 0.0)

    if barrier_kind == "out":
        active = ~breached
    else:  # "in"
        active = breached

    payoff = np.where(active, terminal, 0.0)
    disc = math.exp(-r * T)
    prem = float(disc * payoff.mean())
    se = float(disc * payoff.std(ddof=1) / math.sqrt(n_paths))
    breach_prob = float(breached.mean())
    return prem, se, breach_prob


# ---------------------------------------------------------------------------
# Drive the scenario
# ---------------------------------------------------------------------------


SCENARIO = {
    "rfq": (
        "Hedge fund holds $30M AAPL into Q3 print in 21 days. Earnings_hedge "
        "mandate. 80bps budget. Barrier OK on the protection wing."
    ),
    "intake": {
        "underlying": "AAPL",
        "notional_usd": 30_000_000,
        "view": "earnings_hedge",
        "horizon_days": 21,
        "budget_bps_notional": 80,
        "premium_tolerance": "low",
        "capped_upside_ok": False,
        "barrier_appetite": True,
        "constraints": ["expiry post-earnings"],
        "clarifications_needed": [],
    },
    "spot": 225.0,
    "vol_30d": 0.34,
    "vol_90d": 0.28,
    "div": 0.005,
}


def _bps(usd: float, notional: float) -> float:
    return 10_000.0 * usd / notional if notional else 0.0


def main() -> int:
    agent_config.reload()
    llm_client.reset_llm_client()
    _install_intake_replay(SCENARIO["intake"])

    sc = SCENARIO
    with _fake_market(
        spot=sc["spot"], vol_30d=sc["vol_30d"], vol_90d=sc["vol_90d"], div=sc["div"]
    ):
        orch = OrchestratorAgent(store=SessionStore())
        session = orch.start_session(intake_nl=sc["rfq"])
        assert session.status == SessionStatus.AWAITING_GATE_A, session.last_error

        # Gate A
        session = orch.decide_gate(session.session_id, Gate.A, approved=True)
        assert session.status == SessionStatus.AWAITING_GATE_B
        assert len(session.candidates) == 3, (
            f"strategist must produce 3 candidates, got {len(session.candidates)}"
        )

        # Gate B
        session = orch.decide_gate(session.session_id, Gate.B, approved=True)
        assert session.status == SessionStatus.AWAITING_GATE_C
        priced = session.priced
        memo = session.memo
        assert priced and memo is not None

        regime = session.regime
        S0 = regime.spot
        r = regime.risk_free_rate
        q = regime.dividend_yield
        sigma = (
            regime.atm_iv
            or regime.realised_vol_30d
            or regime.realised_vol_90d
            or 0.20
        )

        # Pick recommended candidate.
        rec = next(
            (p for p in priced if p.candidate.candidate_id == memo.recommended_candidate_id),
            priced[0],
        )

        # Print scenario header
        print("=" * 84)
        print("SCENARIO 6 — AAPL earnings_hedge $30M 21d 80bps barrier-OK")
        print("=" * 84)
        print(f"VERDICT line: {memo.title}")
        print()

        # 1-sigma move sanity check
        one_sigma_dollars = sigma * math.sqrt(21.0 / 365.0) * S0
        print(
            f"Regime: spot=${S0:.2f}  r={r:.4f}  q={q:.4f}  sigma={sigma:.4f}  "
            f"21d 1σ ≈ ${one_sigma_dollars:.2f} ({100*one_sigma_dollars/S0:.2f}%)"
        )
        print()

        # ------- Per-candidate MC overlay -------
        notional = rec.candidate.notional_usd
        scale = notional / S0  # share-count equivalent
        rec_breach_prob = None
        rec_has_barrier = False

        print("--- ALL CANDIDATES: per-leg MC vs QL ---")
        for pc in priced:
            cand = pc.candidate
            print()
            print(
                f"[{cand.candidate_id}] kind={cand.kind.value}  name={cand.name}"
            )
            print(
                f"   net_premium=${pc.net_premium:,.0f}  "
                f"({pc.net_premium_bps:+.2f} bps)  method={pc.method_label}"
            )
            for i, leg in enumerate(cand.legs):
                ql_prem = pc.per_leg_prices[i]
                T = leg.expiry_days / 365.0
                otype = leg.option_type
                is_call = otype.endswith("_call")
                if otype.startswith(("knockout_", "knockin_")):
                    barrier_kind = "out" if otype.startswith("knockout_") else "in"
                    direction = "down" if (leg.barrier_level or 0) < S0 else "up"
                    mc_prem, mc_se, breach = _mc_barrier_leg(
                        S0=S0,
                        K=leg.strike,
                        B=leg.barrier_level,
                        is_call=is_call,
                        barrier_kind=barrier_kind,
                        direction=direction,
                        T=T,
                        r=r,
                        q=q,
                        sigma=float(sigma),
                        n_paths=50_000,
                        n_steps=15,
                    )
                    delta = mc_prem - ql_prem
                    delta_bps = _bps(delta * scale, notional)
                    print(
                        f"   leg[{i}] {otype:14s}  K={leg.strike:7.2f}  B={leg.barrier_level:7.2f}  "
                        f"qty={leg.quantity:+.0f}  QL=${ql_prem:.4f}  "
                        f"MC=${mc_prem:.4f} (SE ${mc_se:.4f})  "
                        f"Δ=${delta:+.4f} ({delta_bps:+.2f} bps)  "
                        f"breach_prob={breach*100:.1f}% [{direction}-and-{barrier_kind}]"
                    )
                    if pc.candidate.candidate_id == rec.candidate.candidate_id:
                        rec_breach_prob = breach
                        rec_has_barrier = True
                else:
                    mc_prem, mc_se = _mc_european_leg(
                        S0=S0,
                        K=leg.strike,
                        is_call=is_call,
                        T=T,
                        r=r,
                        q=q,
                        sigma=float(sigma),
                        n_paths=50_000,
                    )
                    delta = mc_prem - ql_prem
                    delta_bps = _bps(delta * scale, notional)
                    print(
                        f"   leg[{i}] {otype:14s}  K={leg.strike:7.2f}                "
                        f"qty={leg.quantity:+.0f}  QL=${ql_prem:.4f}  "
                        f"MC=${mc_prem:.4f} (SE ${mc_se:.4f})  "
                        f"Δ=${delta:+.4f} ({delta_bps:+.2f} bps)"
                    )

        print()
        print("--- RECOMMENDED CANDIDATE ---")
        cand = rec.candidate
        print(f"kind={cand.kind.value}  name={cand.name}")
        for i, leg in enumerate(cand.legs):
            pct_K = 100.0 * (leg.strike / S0 - 1.0)
            extra = ""
            if leg.barrier_level:
                pct_B = 100.0 * (leg.barrier_level / S0 - 1.0)
                extra = f"  B={leg.barrier_level:.2f} ({pct_B:+.2f}%)"
            print(
                f"  leg[{i}] {leg.option_type}  K={leg.strike:.2f} ({pct_K:+.2f}%)  "
                f"qty={leg.quantity:+.0f}{extra}  expiry={leg.expiry_days}d"
            )
        print()

        # Budget check
        budget_bps = sc["intake"]["budget_bps_notional"]
        net_bps = rec.net_premium_bps
        net_usd = rec.net_premium
        budget_status = "UNDER" if abs(net_bps) <= budget_bps else "OVER"
        delta_to_budget = abs(net_bps) - budget_bps
        print(
            f"Net premium (QL): ${net_usd:,.0f}  ({net_bps:+.2f} bps) vs "
            f"budget {budget_bps} bps — {budget_status} by {abs(delta_to_budget):.2f} bps"
        )
        print()

        # Greeks
        g = rec.greeks
        print(
            f"Greeks (per-share, scale-free): "
            f"delta={g.delta:+.4f}  gamma={g.gamma:+.6f}  vega={g.vega:+.4f} (per 1% σ)  "
            f"theta={g.theta:+.4f} (per cal-day)  rho={g.rho:+.4f}"
        )
        # Theta-bleed as fraction of net premium per day
        if rec.net_premium != 0:
            theta_usd_per_day = g.theta * scale
            theta_pct_premium = 100.0 * theta_usd_per_day / max(abs(rec.net_premium), 1.0)
            print(
                f"Theta bleed: ${theta_usd_per_day:,.0f}/day "
                f"= {theta_pct_premium:+.2f}% of premium / day"
            )
        print()

        # Validator findings
        print("--- VALIDATOR FINDINGS ---")
        if session.validator and session.validator.findings:
            for f in session.validator.findings:
                cid = f" [{f.candidate_id}]" if f.candidate_id else ""
                print(f"  [{f.severity.value.upper():5}]{cid} {f.name}: {f.message}")
        else:
            print("  (none)")
        print()

        # Memo comparison table
        print("--- COMPARISON TABLE (10 cols) ---")
        print(memo.comparison_table_md.strip())
        print()

        # Caveats
        print("--- CAVEATS ---")
        for c in memo.caveats:
            print(f"  - {c}")
        print()

        # Recent comparable deals section
        print("--- RECOMMENDATION (incl. RAG) ---")
        print(memo.recommendation_md.strip())
        print()

        # ---- VERDICT GRADE ----
        budget_ok = abs(net_bps) <= budget_bps + 10  # 10bps slack
        print(f"VERDICT GRADE: budget_ok={budget_ok}  has_barrier={rec_has_barrier}")
        if rec_breach_prob is not None:
            print(f"  recommended barrier breach_prob={rec_breach_prob*100:.1f}%")
        print("=" * 84)

        # Gate C
        session = orch.decide_gate(session.session_id, Gate.C, approved=True)
        assert session.status == SessionStatus.DONE
        return 0


if __name__ == "__main__":
    sys.exit(main())
