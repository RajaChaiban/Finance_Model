"""Stress test scenario #3 — Pension XLF post-Powell-pivot rally lock-in.

Drives an RFQ for a $250M XLF protect_gains zero-cost no-barrier 12mo collar
through the structuring co-pilot in DEMO_REPLAY mode, then runs a Monte Carlo
overlay to cross-check QuantLib pricing of the recommended candidate.

Run: ``python tests/stress/scenario_03.py``
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

# Ensure stdout/stderr can render Unicode em-dashes etc. (memo titles).
try:  # pragma: no cover — Windows console fallback
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Make sure the repo root is on sys.path so ``import src.*`` works when this
# file is invoked directly via ``python tests/stress/scenario_03.py``.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import numpy as np

from src.agents import llm_client
from src.agents.orchestrator import OrchestratorAgent, SessionStore
from src.agents.state import Gate, SessionStatus
from src.config import agent_config


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
# Monte Carlo overlay — GBM under risk-neutral measure with continuous div q.
# ---------------------------------------------------------------------------


def _mc_european_legs(
    *,
    S0: float,
    K_put: float,
    K_call: float,
    T: float,
    r: float,
    q: float,
    sigma: float,
    n_paths: int = 50_000,
    seed: int = 7,
) -> dict[str, float]:
    """Price a long-put / short-call collar overlay via 50k GBM paths.

    Returns per-share leg premiums + standard error.
    """
    rng = np.random.default_rng(seed)
    z = rng.standard_normal(n_paths)
    drift = (r - q - 0.5 * sigma * sigma) * T
    vol = sigma * math.sqrt(T)
    ST = S0 * np.exp(drift + vol * z)

    disc = math.exp(-r * T)
    put_payoffs = np.maximum(K_put - ST, 0.0)
    call_payoffs = np.maximum(ST - K_call, 0.0)

    put_prem = float(disc * put_payoffs.mean())
    call_prem = float(disc * call_payoffs.mean())
    put_se = float(disc * put_payoffs.std(ddof=1) / math.sqrt(n_paths))
    call_se = float(disc * call_payoffs.std(ddof=1) / math.sqrt(n_paths))
    return {
        "put_prem": put_prem,
        "call_prem": call_prem,
        "put_se": put_se,
        "call_se": call_se,
    }


# ---------------------------------------------------------------------------
# Drive the scenario
# ---------------------------------------------------------------------------


SCENARIO = {
    "rfq": (
        "Pension allocator, $250M XLF after Powell pivot rally. Want to lock "
        "in 12mo protect_gains zero-cost. Capped upside fine. No barriers — "
        "board policy."
    ),
    "intake": {
        "underlying": "XLF",
        "notional_usd": 250_000_000,
        "view": "protect_gains",
        "horizon_days": 365,
        "budget_bps_notional": 0,
        "premium_tolerance": "zero_cost_only",
        "capped_upside_ok": True,
        "barrier_appetite": False,
        "constraints": ["no barriers", "board-approved zero-cost only"],
        "clarifications_needed": [],
    },
    "spot": 52.0,
    "vol_30d": 0.18,
    "vol_90d": 0.19,
    "div": 0.022,
}


def _find_collar(priced):
    """Pick the recommended candidate when known; else first collar-like."""
    for pc in priced:
        kind = pc.candidate.kind.value
        if "collar" in kind:
            return pc
    return priced[0]


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
        assert len(session.candidates) == 3

        # Gate B
        session = orch.decide_gate(session.session_id, Gate.B, approved=True)
        assert session.status == SessionStatus.AWAITING_GATE_C
        priced = session.priced
        memo = session.memo
        assert priced and memo is not None

        # Pick recommended candidate (memo recommendation), else collar.
        rec = next(
            (p for p in priced if p.candidate.candidate_id == memo.recommended_candidate_id),
            None,
        )
        target = rec or _find_collar(priced)
        cand = target.candidate

        # Identify put / call legs (european, vanilla collar). For zero-cost
        # collar the put is long, call is short.
        put_leg = next(
            (l for l in cand.legs if l.option_type.endswith("_put") and l.quantity > 0),
            None,
        )
        call_leg = next(
            (l for l in cand.legs if l.option_type.endswith("_call") and l.quantity < 0),
            None,
        )
        if put_leg is None or call_leg is None:
            # Some structurer rows return short-put or short-call combos; just
            # pick whichever put/call we can.
            put_leg = put_leg or next(
                (l for l in cand.legs if l.option_type.endswith("_put")), None
            )
            call_leg = call_leg or next(
                (l for l in cand.legs if l.option_type.endswith("_call")), None
            )

        # MC overlay parameters from the regime.
        regime = session.regime
        S0 = regime.spot
        r = regime.risk_free_rate
        q = regime.dividend_yield
        # Match the PricingAgent's sigma pick (30d > 90d > 0.20) so MC and QL
        # are using the same vol input. Otherwise the headline drift will be
        # dominated by a vol mismatch, not engine drift.
        sigma = (
            regime.atm_iv
            or regime.realised_vol_30d
            or regime.realised_vol_90d
            or 0.20
        )
        T_days = put_leg.expiry_days if put_leg else 365
        T = T_days / 365.0

        mc = _mc_european_legs(
            S0=S0,
            K_put=put_leg.strike if put_leg else 0.95 * S0,
            K_call=call_leg.strike if call_leg else 1.10 * S0,
            T=T,
            r=r,
            q=q,
            sigma=float(sigma),
            n_paths=50_000,
        )

        # per_leg_prices[i] is the per-SHARE premium (engine output, before
        # the candidate's notional/spot scale is applied). Net USD premium is
        # `sum(qty * price) * (notional / spot)` (see src/agents/pricing.py).
        # leg.quantity is +/- 1 (long/short unit) — NOT share count. The scale
        # to total USD is `notional / spot`.
        ql_per_leg = target.per_leg_prices
        leg_to_idx = {id(l): i for i, l in enumerate(cand.legs)}
        ql_put_pershare = ql_per_leg[leg_to_idx[id(put_leg)]] if put_leg else 0.0
        ql_call_pershare = ql_per_leg[leg_to_idx[id(call_leg)]] if call_leg else 0.0

        notional = cand.notional_usd
        scale = notional / S0  # share-count equivalent
        ql_put_total = ql_put_pershare * scale  # USD long-put debit
        ql_call_total = ql_call_pershare * scale  # USD short-call credit (premium received)

        mc_put_usd = mc["put_prem"] * scale
        mc_call_usd = mc["call_prem"] * scale
        # Net debit USD = long put paid - short call received.
        mc_net_usd = mc_put_usd - mc_call_usd

        net_bps_ql = target.net_premium_bps
        net_usd_ql = target.net_premium

        # Deltas in bps (relative to notional) and as % of QL price.
        def _bps(usd: float) -> float:
            return 10_000.0 * usd / notional if notional else 0.0

        put_delta_usd = mc_put_usd - ql_put_total
        call_delta_usd = mc_call_usd - ql_call_total
        net_delta_usd = mc_net_usd - net_usd_ql

        # Zero-cost premium symmetry check (per-share).
        if ql_put_pershare > 0 and ql_call_pershare > 0:
            symmetry = abs(ql_put_pershare - ql_call_pershare) / max(
                ql_put_pershare, ql_call_pershare
            )
        else:
            symmetry = float("nan")

        # Greeks (option overlay alone — not "long stock + collar").
        g = target.greeks

        # ---- Print VERDICT block ----
        print("=" * 78)
        print("SCENARIO 3 — XLF protect_gains $250M 12mo zero-cost no-barrier")
        print("=" * 78)
        print(f"VERDICT line: {memo.title}")
        print()
        print(f"Recommended candidate: kind={cand.kind.value}  name={cand.name}")
        if put_leg:
            put_pct = 100.0 * (put_leg.strike / S0 - 1.0)
            print(
                f"  PUT  leg: {put_leg.option_type}  K={put_leg.strike:.2f}  "
                f"({put_pct:+.2f}% vs spot {S0:.2f})  qty={put_leg.quantity:,.0f}"
            )
        if call_leg:
            call_pct = 100.0 * (call_leg.strike / S0 - 1.0)
            print(
                f"  CALL leg: {call_leg.option_type}  K={call_leg.strike:.2f}  "
                f"({call_pct:+.2f}% vs spot {S0:.2f}) -- CAP at +{call_pct:.2f}% above spot"
                f"  qty={call_leg.quantity:,.0f}"
            )
        print(
            f"Net premium (QL): ${net_usd_ql:,.0f}  "
            f"({net_bps_ql:+.2f} bps of notional) — "
            f"zero-cost? {'YES' if abs(net_bps_ql) < 5 else 'NO'}"
        )
        print(f"Per-share put premium (QL):  ${ql_put_pershare:.4f}")
        print(f"Per-share call premium (QL): ${ql_call_pershare:.4f}")
        if not math.isnan(symmetry):
            print(
                f"Put/Call premium symmetry: {100*symmetry:.2f}% gap "
                f"(target <5% for true zero-cost)"
            )
        print()
        print(f"Method label: {target.method_label}")
        print()
        print("--- MONTE CARLO OVERLAY (50,000 GBM paths, 365d) ---")
        print(
            f"  spot={S0:.2f}  r={r:.4f}  q={q:.4f}  sigma={sigma:.4f}  T={T:.4f}"
        )
        print(
            f"  MC put prem/share:  ${mc['put_prem']:.4f}  "
            f"(SE ${mc['put_se']:.4f})  vs QL ${ql_put_pershare:.4f}"
        )
        print(
            f"  MC call prem/share: ${mc['call_prem']:.4f}  "
            f"(SE ${mc['call_se']:.4f})  vs QL ${ql_call_pershare:.4f}"
        )
        print(
            f"  MC net (long put - short call) USD: ${mc_net_usd:,.0f}  "
            f"vs QL ${net_usd_ql:,.0f}"
        )
        print()
        print("--- MC vs QL DELTAS (bps of notional) ---")
        print(f"  PUT  leg delta: {_bps(put_delta_usd):+.3f} bps  (${put_delta_usd:,.0f})")
        print(f"  CALL leg delta: {_bps(call_delta_usd):+.3f} bps  (${call_delta_usd:,.0f})")
        print(f"  NET       delta: {_bps(net_delta_usd):+.3f} bps  (${net_delta_usd:,.0f})")
        print()
        print("--- GREEKS (option overlay alone) ---")
        print(
            f"  delta={g.delta:+.4f}  gamma={g.gamma:+.6f}  vega={g.vega:+.4f}  "
            f"theta={g.theta:+.4f}  rho={g.rho:+.4f}"
        )
        print()
        print("--- VALIDATOR FINDINGS ---")
        if session.validator and session.validator.findings:
            for f in session.validator.findings:
                print(f"  [{f.severity.value.upper():5}] {f.name}: {f.message}")
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

        # ---- VERDICT GRADE ----
        is_zero_cost = abs(net_bps_ql) < 5
        sym_ok = (not math.isnan(symmetry)) and symmetry < 0.05
        mc_net_bps = abs(_bps(net_delta_usd))
        engine_ok = mc_net_bps < 50  # half a percent of notional drift cap

        if is_zero_cost and sym_ok and engine_ok:
            grade = "PASS"
        elif is_zero_cost and engine_ok:
            grade = "WARN"
        else:
            grade = "FAIL"

        print(f"VERDICT GRADE: {grade}")
        print(
            f"  zero_cost_ok={is_zero_cost} ({net_bps_ql:+.2f} bps); "
            f"premium_symmetry_ok={sym_ok}; "
            f"engine_drift_bps={mc_net_bps:.2f}"
        )
        print("=" * 78)

        # Gate C
        session = orch.decide_gate(session.session_id, Gate.C, approved=True)
        assert session.status == SessionStatus.DONE
        return 0


if __name__ == "__main__":
    sys.exit(main())
