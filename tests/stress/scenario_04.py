"""Scenario 04 stress harness — RIA quarterly rebal, XLV neutral 90d 40bps.

Drives the structuring co-pilot end-to-end via DEMO_REPLAY=1, runs a 50k-path
GBM Monte Carlo overlay against the recommended candidate's short-call leg
(covered-call style), and prints a senior-desk critique.

Run:
    python tests/stress/scenario_04.py
"""

from __future__ import annotations

import json
import os
import sys
import math
from typing import Any
from unittest.mock import patch

# 1. Replay env BEFORE importing src.* (mirrors test_copilot_scenarios.py).
os.environ["DEMO_REPLAY"] = "1"
os.environ["GEMINI_API_KEY"] = ""

import numpy as np  # noqa: E402

# Force UTF-8 stdout so Greek glyphs (Δ, Γ, Θ, σ) render on cp1252 consoles.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

# Ensure project root on sys.path when invoked as a script.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.agents.orchestrator import OrchestratorAgent, SessionStore  # noqa: E402
from src.agents.state import Gate, SessionStatus  # noqa: E402
from src.agents import llm_client  # noqa: E402
from src.config import agent_config  # noqa: E402


# ---------------------------------------------------------------------------
# Scenario inputs
# ---------------------------------------------------------------------------

RFQ_TEXT = (
    "RIA on a quarterly rebal, $50M XLV core. Neutral 90d view, expects "
    "range-bound. 40bps yield-enhancement budget. Capped upside OK if "
    "income enhances IRR."
)

INTAKE_PAYLOAD: dict[str, Any] = {
    "underlying": "XLV",
    "notional_usd": 50_000_000,
    "view": "neutral",
    "horizon_days": 90,
    # Note: schema validates 0-2000, so we keep budget positive even though
    # the client is RECEIVING premium (this is the MIN credit they want).
    "budget_bps_notional": 40,
    "premium_tolerance": "medium",
    "capped_upside_ok": True,
    "barrier_appetite": False,
    "constraints": [],
    "clarifications_needed": [],
}

SPOT = 155.0
VOL_30D = 0.16
VOL_90D = 0.17
DIV = 0.014
HORIZON_DAYS = 90
N_PATHS = 50_000


# ---------------------------------------------------------------------------
# Helpers
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


def _gbm_mc_short_call(
    *, spot: float, strike: float, vol: float, r: float, q: float,
    days: int, n_paths: int, leg_qty: float, scale: float, seed: int = 42,
) -> dict[str, float]:
    """Pure GBM simulation of a short European call leg.

    Returns dict with per-share short-call value and USD value scaled to notional.
    """
    rng = np.random.default_rng(seed)
    T = days / 365.0
    drift = (r - q - 0.5 * vol * vol) * T
    diffusion = vol * math.sqrt(T)
    z = rng.standard_normal(n_paths)
    S_T = spot * np.exp(drift + diffusion * z)
    # Short call leg: payoff = -max(S_T - K, 0); value = E[PV(payoff)]
    short_call_payoff = -np.maximum(S_T - strike, 0.0)
    pv = math.exp(-r * T) * short_call_payoff.mean()
    se = math.exp(-r * T) * short_call_payoff.std(ddof=1) / math.sqrt(n_paths)
    # Apply leg quantity (-1 for short) — but the payoff formula already encodes
    # the short side. So we multiply by abs(qty) to scale up correctly: quantity
    # convention here is "per unit" (a unit = one share). leg_qty=-1 means short
    # 1 share-equivalent worth. We undo the implicit minus by multiplying by
    # -leg_qty when leg_qty<0, which equals +1 for our short-call leg. The MC
    # *price contribution* already carries the correct sign.
    # To stay faithful to the orchestrator's accounting we mimic:
    #   net_price_per_unit += leg.quantity * price_long_equivalent
    # where price_long_equivalent = +max(S_T-K,0) discounted.
    long_call_pv = math.exp(-r * T) * np.maximum(S_T - strike, 0.0).mean()
    long_call_se = math.exp(-r * T) * np.maximum(S_T - strike, 0.0).std(ddof=1) / math.sqrt(n_paths)
    net_price_per_unit = leg_qty * long_call_pv  # negative for short
    net_premium_usd = net_price_per_unit * scale
    return {
        "long_call_pv_per_share": long_call_pv,
        "long_call_se_per_share": long_call_se,
        "short_call_pv_per_share": pv,
        "short_call_se_per_share": se,
        "net_price_per_unit": net_price_per_unit,
        "net_premium_usd": net_premium_usd,
    }


def _bs_call_price(S, K, T, r, q, sigma):
    """Closed-form Black-Scholes call for sanity-check vs MC."""
    if T <= 0:
        return max(S - K, 0.0)
    from math import log, sqrt, exp
    from statistics import NormalDist
    N = NormalDist().cdf
    d1 = (log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrt(T))
    d2 = d1 - sigma * sqrt(T)
    return S * exp(-q * T) * N(d1) - K * exp(-r * T) * N(d2)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main() -> int:
    agent_config.reload()
    llm_client.reset_llm_client()
    _install_intake_replay(INTAKE_PAYLOAD)

    with _fake_market(spot=SPOT, vol_30d=VOL_30D, vol_90d=VOL_90D, div=DIV):
        orch = OrchestratorAgent(store=SessionStore())
        session = orch.start_session(intake_nl=RFQ_TEXT)
        if session.status != SessionStatus.AWAITING_GATE_A:
            print(f"FAIL: Intake did not reach Gate A. status={session.status}, "
                  f"err={session.last_error}")
            return 1

        # Gate A
        session = orch.decide_gate(session.session_id, Gate.A, approved=True)
        if session.status != SessionStatus.AWAITING_GATE_B:
            print(f"FAIL: Gate A advance failed. status={session.status}, "
                  f"err={session.last_error}")
            return 1
        if len(session.candidates) != 3:
            print(f"FAIL: expected 3 candidates, got {len(session.candidates)}")
            return 1

        # Gate B
        session = orch.decide_gate(session.session_id, Gate.B, approved=True)
        if session.status != SessionStatus.AWAITING_GATE_C:
            print(f"FAIL: Gate B advance failed. status={session.status}, "
                  f"err={session.last_error}")
            return 1

        memo = session.memo
        priced = session.priced
        validator = session.validator

        # Find recommended candidate
        rec_pc = next(
            (p for p in priced if p.candidate.candidate_id == memo.recommended_candidate_id),
            None,
        )
        if rec_pc is None:
            print("FAIL: recommended_candidate_id not in priced list")
            return 1

        # Identify short call leg in the recommendation (for covered_call expect lone leg)
        short_call_leg = next(
            (l for l in rec_pc.candidate.legs
             if l.quantity < 0 and l.option_type.endswith("_call")),
            None,
        )

        # ALSO surface any covered_call candidate present in the priced set
        # (the strategist may have ranked it #2 / #3). MC overlay against it
        # gives the senior reviewer a true short-call comparison even when
        # the recommendation went elsewhere.
        cc_pc = next(
            (p for p in priced if p.candidate.kind.value == "covered_call"),
            None,
        )
        cc_short_call_leg = None
        if cc_pc is not None:
            cc_short_call_leg = next(
                (l for l in cc_pc.candidate.legs
                 if l.quantity < 0 and l.option_type.endswith("_call")),
                None,
            )

        # Gate C
        session = orch.decide_gate(session.session_id, Gate.C, approved=True)

        # ------------------------------------------------------------------
        # MC overlay
        # ------------------------------------------------------------------
        regime = session.regime
        r = regime.risk_free_rate
        q = regime.dividend_yield
        # Use 90d vol since horizon=90d
        sigma = VOL_90D

        scale = rec_pc.candidate.notional_usd / regime.spot

        mc_summary = None
        bs_long = None
        if short_call_leg is not None:
            mc_summary = _gbm_mc_short_call(
                spot=regime.spot, strike=short_call_leg.strike,
                vol=sigma, r=r, q=q,
                days=HORIZON_DAYS, n_paths=N_PATHS,
                leg_qty=short_call_leg.quantity, scale=scale,
            )
            bs_long = _bs_call_price(regime.spot, short_call_leg.strike,
                                     HORIZON_DAYS / 365.0, r, q, sigma)

        # Independent MC overlay on the covered_call candidate (if present),
        # regardless of whether it was the recommendation. This is the trade
        # the client actually asked for; we want MC vs QL parity on it.
        cc_mc = None
        cc_bs_long = None
        if cc_pc is not None and cc_short_call_leg is not None:
            cc_scale = cc_pc.candidate.notional_usd / regime.spot
            cc_mc = _gbm_mc_short_call(
                spot=regime.spot, strike=cc_short_call_leg.strike,
                vol=sigma, r=r, q=q,
                days=HORIZON_DAYS, n_paths=N_PATHS,
                leg_qty=cc_short_call_leg.quantity, scale=cc_scale,
                seed=137,
            )
            cc_bs_long = _bs_call_price(regime.spot, cc_short_call_leg.strike,
                                        HORIZON_DAYS / 365.0, r, q, sigma)

        # ------------------------------------------------------------------
        # Print diagnostics
        # ------------------------------------------------------------------
        print("=" * 80)
        print("SCENARIO 4 — XLV neutral $50M 90d 40bps yield-enhance (covered-call)")
        print("=" * 80)
        print()
        print(f"VERDICT: {memo.title.splitlines()[0]}")
        print()
        print(f"Recommended candidate: kind={rec_pc.candidate.kind} "
              f"name={rec_pc.candidate.name!r}")
        if short_call_leg is not None:
            pct = (short_call_leg.strike / regime.spot - 1.0) * 100.0
            print(f"Short call strike: K={short_call_leg.strike:.2f} "
                  f"({pct:+.2f}% vs spot {regime.spot:.2f})")
        else:
            print("Short call strike: N/A (no short-call leg in recommendation)")

        net_prem_bps = rec_pc.net_premium_bps
        net_prem_usd = rec_pc.net_premium
        is_credit = net_prem_usd < 0
        sign_word = "CREDIT" if is_credit else "DEBIT"
        print(f"Net premium (memo): ${net_prem_usd/1e6:+.3f}M "
              f"({net_prem_bps:+.2f} bps {sign_word})")
        # Annualized yield (only meaningful if credit)
        credit_bps = -net_prem_bps if is_credit else net_prem_bps
        annualized = (credit_bps / HORIZON_DAYS) * 365.0
        flag = " <<< BELOW 100 bps THRESHOLD" if annualized < 100 else ""
        print(f"Annualized yield: {annualized:+.1f} bps{flag}")
        print()

        # MC vs QL
        if mc_summary is not None:
            mc_usd = mc_summary["net_premium_usd"]
            ql_usd = net_prem_usd
            delta_usd = mc_usd - ql_usd
            delta_bps = (mc_usd - ql_usd) / rec_pc.candidate.notional_usd * 10000.0
            print(f"MC overlay (50k GBM paths, 90d, σ={sigma:.4f}, r={r:.4f}, q={q:.4f}):")
            print(f"  Long-call analytic BS : {bs_long:.4f} per share")
            print(f"  Long-call MC          : {mc_summary['long_call_pv_per_share']:.4f} "
                  f"(SE {mc_summary['long_call_se_per_share']:.4f})")
            print(f"  Net price per unit MC : {mc_summary['net_price_per_unit']:+.4f}")
            print(f"  Net premium MC USD    : ${mc_usd/1e6:+.3f}M")
            print(f"  Net premium QL  USD    : ${ql_usd/1e6:+.3f}M")
            print(f"  Δ (MC - QL)            : ${delta_usd/1e6:+.4f}M = {delta_bps:+.3f} bps")
            mc_ok = abs(delta_bps) < 100  # <1% of notional in bps
            print(f"  Within 1% of notional? {'YES' if mc_ok else 'NO'}")
        else:
            print("MC overlay (recommended): SKIPPED — no short-call leg in pick.")

        # Independent covered-call MC overlay
        if cc_pc is not None and cc_mc is not None:
            cc_pct = (cc_short_call_leg.strike / regime.spot - 1.0) * 100.0
            print()
            print(f"MC overlay (covered_call sibling, K={cc_short_call_leg.strike:.2f} "
                  f"= {cc_pct:+.2f}% vs spot):")
            print(f"  Long-call analytic BS : {cc_bs_long:.4f} per share")
            print(f"  Long-call MC          : {cc_mc['long_call_pv_per_share']:.4f} "
                  f"(SE {cc_mc['long_call_se_per_share']:.4f})")
            cc_ql_usd = cc_pc.net_premium
            cc_mc_usd = cc_mc["net_premium_usd"]
            cc_delta_usd = cc_mc_usd - cc_ql_usd
            cc_delta_bps = cc_delta_usd / cc_pc.candidate.notional_usd * 10000.0
            cc_credit_bps = -cc_pc.net_premium_bps if cc_pc.net_premium < 0 else cc_pc.net_premium_bps
            cc_annual = (cc_credit_bps / HORIZON_DAYS) * 365.0
            print(f"  Net premium QL  USD   : ${cc_ql_usd/1e6:+.4f}M ({cc_pc.net_premium_bps:+.2f} bps)")
            print(f"  Net premium MC  USD   : ${cc_mc_usd/1e6:+.4f}M")
            print(f"  Δ (MC - QL)            : ${cc_delta_usd/1e6:+.4f}M = {cc_delta_bps:+.3f} bps")
            cc_ok = abs(cc_delta_bps) < 100
            print(f"  Within 1% of notional? {'YES' if cc_ok else 'NO'}")
            print(f"  Annualized credit on covered_call: {cc_annual:+.1f} bps "
                  f"{'(BELOW 100 bps)' if cc_annual < 100 else ''}")
        print()

        # 10-col comparison table
        print("Comparison table (memo):")
        print(memo.comparison_table_md.strip())
        print()

        # Per-candidate Greeks dump
        print("Per-candidate priced summary:")
        for pc in priced:
            g = pc.greeks
            print(f"  - {pc.candidate.candidate_id} {pc.candidate.kind:<22} "
                  f"prem={pc.net_premium_bps:+.2f}bps "
                  f"Δ={g.delta:+.3f} Γ={g.gamma:+.5f} "
                  f"V={g.vega:+.2f} Θ={g.theta:+.3f}")
        print()

        # Validator findings
        print(f"Validator findings ({len(validator.findings)} total):")
        for f in validator.findings:
            cid = f" [{f.candidate_id}]" if f.candidate_id else ""
            print(f"  - [{f.severity.value.upper()}]{cid} {f.name}: {f.message}")
        print()

        # Caveats
        print(f"Caveats ({len(memo.caveats)}):")
        for c in memo.caveats:
            print(f"  - {c}")
        print()

        # Recommendation MD (first 1500 chars)
        print("Recommendation MD (truncated):")
        rec_md = memo.recommendation_md
        print(rec_md[:1500] + ("..." if len(rec_md) > 1500 else ""))
        print()

        # Verdict summary line for grep
        passed_mc = mc_summary is not None and abs(
            (mc_summary["net_premium_usd"] - net_prem_usd)
            / rec_pc.candidate.notional_usd * 10000.0
        ) < 100
        if mc_summary is None:
            verdict = "WARN: no short-call leg found in recommendation"
        elif not passed_mc:
            verdict = "FAIL: MC vs QL >1% of notional"
        elif annualized < 100:
            verdict = "WARN: annualized yield below 100 bps threshold"
        else:
            verdict = "PASS"
        print(f"END VERDICT: {verdict}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
