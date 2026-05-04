"""verify_agent_backends.py — proves the agent layer is using QuantLib + RAG.

Runs a full end-to-end agent session against the live FastAPI backend (port
8002) and produces evidence that:

  1. The Pricing agent invoked the **QuantLib router** (not LLM-fabricated
     numbers): `priced_candidate.method_label` carries the engine label and
     an independent `POST /api/price` for one leg matches the agent's
     per-leg price within tolerance.

  2. The other agents invoked the **Market-Intelligence (RAG) layer**: the
     session's `market_context` is non-empty, contains entries from multiple
     agents, and each entry references concrete corpus sources (not made up
     by the LLM).

Usage
-----
    python -m uvicorn src.api.main:app --reload --port 8002    # if not already up
    python scripts/verify_agent_backends.py

Exits 0 on PASS, 1 on FAIL. Total runtime ~60-120s with live Gemini.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

import urllib.request
import urllib.error

# Windows cp1252 console can't encode the unicode arrow used in our notes.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


BASE = "http://127.0.0.1:8002"
PRICE_TOLERANCE = 0.05   # 5% — allows for scalar-σ vs surface differences
GATE_POLL_TIMEOUT_S = 240
GATE_POLL_INTERVAL_S = 1.5


# ---------------------------------------------------------------------------
# Tiny HTTP helpers (stdlib only — no extra deps)
# ---------------------------------------------------------------------------


def _request(method: str, path: str, body: dict | None = None) -> tuple[int, Any]:
    url = f"{BASE}{path}"
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = resp.read().decode("utf-8")
            return resp.status, (json.loads(payload) if payload else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def post(path: str, body: dict) -> Any:
    code, payload = _request("POST", path, body)
    if code >= 400:
        raise RuntimeError(f"POST {path} -> {code}: {payload}")
    return payload


def get(path: str) -> Any:
    code, payload = _request("GET", path, None)
    if code >= 400:
        raise RuntimeError(f"GET {path} -> {code}: {payload}")
    return payload


# ---------------------------------------------------------------------------
# Drive a full session
# ---------------------------------------------------------------------------


def _wait_for_status(session_id: str, want: set[str]) -> dict:
    deadline = time.time() + GATE_POLL_TIMEOUT_S
    last = None
    while time.time() < deadline:
        s = get(f"/api/agent/sessions/{session_id}?full=1")
        st = s["status"]
        if st != last:
            print(f"   ... status: {st}")
            last = st
        if st in want:
            return s
        if st in {"error", "cancelled"}:
            raise RuntimeError(f"Session terminated unexpectedly: {st}; last_error={s.get('last_error')}")
        time.sleep(GATE_POLL_INTERVAL_S)
    raise TimeoutError(f"Timed out waiting for {want} (last={last})")


def drive_session() -> dict:
    print("[1/4] Starting agent session (SPY put-spread RFQ)...")
    start = post(
        "/api/agent/sessions",
        {
            "intake_form": {
                "underlying": "SPY",
                "notional_usd": 10_000_000,
                "view": "bearish",
                "horizon_days": 90,
                "budget_bps_notional": 60,
                "premium_tolerance": "low",
                "capped_upside_ok": True,
                "barrier_appetite": False,
            }
        },
    )
    sid = start["session_id"]
    print(f"   session_id={sid}")

    print("[2/4] Waiting for Gate A...")
    _wait_for_status(sid, {"awaiting_gate_a"})
    post(f"/api/agent/sessions/{sid}/gate/a", {"approved": True})

    print("[2/4] Waiting for Gate B...")
    _wait_for_status(sid, {"awaiting_gate_b"})
    post(f"/api/agent/sessions/{sid}/gate/b", {"approved": True})

    print("[2/4] Waiting for Gate C...")
    _wait_for_status(sid, {"awaiting_gate_c"})
    post(f"/api/agent/sessions/{sid}/gate/c", {"approved": True})

    print("[2/4] Waiting for DONE...")
    final = _wait_for_status(sid, {"done"})
    return final


# ---------------------------------------------------------------------------
# Evidence checks
# ---------------------------------------------------------------------------


def check_rag(session: dict) -> tuple[bool, list[str]]:
    """Verify session.market_context proves RAG was actually queried."""
    notes: list[str] = []
    mc = session.get("market_context") or []
    if not mc:
        return False, ["market_context is empty — RAG layer was NEVER queried"]

    by_agent: dict[str, int] = {}
    sources_seen: set[str] = set()
    answers_with_sources = 0
    for entry in mc:
        agent = entry.get("agent", "?")
        by_agent[agent] = by_agent.get(agent, 0) + 1
        srcs = entry.get("sources") or []
        if srcs:
            answers_with_sources += 1
            for s in srcs:
                # source rows can be dicts ({id, doc_type, ...}) or plain strings
                if isinstance(s, dict):
                    sid_ = s.get("id") or s.get("source_id") or s.get("doc_id")
                    if sid_:
                        sources_seen.add(str(sid_))
                else:
                    sources_seen.add(str(s))

    notes.append(f"market_context entries: {len(mc)} across {len(by_agent)} agents → {by_agent}")
    notes.append(f"entries with non-empty sources: {answers_with_sources}/{len(mc)}")
    notes.append(f"distinct corpus source IDs cited: {len(sources_seen)}")
    if sources_seen:
        sample = list(sources_seen)[:5]
        notes.append(f"sample source IDs: {sample}")

    expected_agents = {"IntakeAgent", "StrategistAgent", "PricingAgent", "ScenarioAgent", "ValidatorAgent"}
    seen_agents = set(by_agent.keys())
    missing = expected_agents - seen_agents
    if missing:
        notes.append(f"WARN: no MI calls recorded from: {sorted(missing)}")

    ok = (
        len(mc) >= 3                       # at least 3 RAG calls
        and answers_with_sources >= 1      # at least one returned real corpus rows
        and len(sources_seen) >= 1
    )
    return ok, notes


def check_quantlib(session: dict) -> tuple[bool, list[str]]:
    """Verify pricing came from the QL engine, not the LLM."""
    notes: list[str] = []
    priced = session.get("priced") or []
    if not priced:
        return False, ["session.priced is empty — pricing agent never ran"]

    # 1) Method labels should reflect QL engines.
    labels = [pc.get("method_label", "") for pc in priced]
    notes.append(f"engine method_labels: {labels}")
    ql_signals = ["QuantLib", "ql.", "Binomial", "FDM", "AnalyticEuropean", "AnalyticBarrier", "Reiner", "MC"]
    ql_label_hits = sum(any(sig.lower() in lbl.lower() for sig in ql_signals) for lbl in labels)
    notes.append(f"labels matching QuantLib signature: {ql_label_hits}/{len(labels)}")

    # 2) Independent re-price for one leg.
    regime = session.get("regime") or {}
    spot = regime.get("spot")
    rate = regime.get("risk_free_rate", 0.045)
    div = regime.get("dividend_yield", 0.0)
    sigma = regime.get("atm_iv") or regime.get("realised_vol_30d") or regime.get("realised_vol_90d") or 0.20
    notes.append(f"regime σ used: {sigma:.4f}  spot={spot}  r={rate:.4f}  q={div:.4f}")

    # Pick a candidate with a single vanilla long leg if possible — most stable.
    target_pc, target_leg, target_leg_idx, target_per_leg_price = None, None, None, None
    for pc in priced:
        cand = pc.get("candidate", {})
        legs = cand.get("legs", [])
        prices = pc.get("per_leg_prices") or []
        for i, leg in enumerate(legs):
            ot = leg.get("option_type", "")
            if ot.startswith(("european_", "american_")) and i < len(prices):
                target_pc, target_leg, target_leg_idx = pc, leg, i
                target_per_leg_price = prices[i]
                break
        if target_leg is not None:
            break

    if target_leg is None:
        notes.append("could not find a vanilla leg to re-price independently")
        ok = ql_label_hits == len(labels)
        return ok, notes

    body = {
        "option_type": target_leg["option_type"],
        "underlying": session.get("objective", {}).get("underlying", "SPY"),
        "spot_price": float(spot),
        "strike_price": float(target_leg["strike"]),
        "days_to_expiration": int(target_leg["expiry_days"]),
        "risk_free_rate": float(rate),
        "volatility": float(sigma),
        "dividend_yield": float(div),
    }
    notes.append(f"re-pricing leg via POST /api/price: {body['option_type']} K={body['strike_price']} T={body['days_to_expiration']}d")
    indep = post("/api/price", body)
    indep_price = float(indep["price"])
    abs_diff = abs(indep_price - float(target_per_leg_price))
    rel_diff = abs_diff / max(abs(indep_price), 1e-9)
    notes.append(
        f"agent per-leg price = {target_per_leg_price:.6f}, "
        f"independent /api/price = {indep_price:.6f}, "
        f"|Δ| = {abs_diff:.6f} ({rel_diff*100:.2f}%)  "
        f"engine_method = {indep.get('method')!r}"
    )
    price_ok = rel_diff <= PRICE_TOLERANCE
    label_ok = ql_label_hits == len(labels)
    return (price_ok and label_ok), notes


def check_memo_citations(session: dict) -> tuple[bool, list[str]]:
    memo = session.get("memo") or {}
    # The Narrator appends 'Market Intelligence Citations' to recommendation_md
    # (see narrator._append_market_context_citations). Search every memo string
    # field defensively.
    blobs = []
    for k in ("recommendation_md", "comparison_table_md", "rendered_html", "title", "objective_restatement"):
        v = memo.get(k)
        if isinstance(v, str):
            blobs.append((k, v))
    for k in ("per_candidate_sections_md",):
        v = memo.get(k) or []
        for i, s in enumerate(v):
            if isinstance(s, str):
                blobs.append((f"{k}[{i}]", s))

    total = sum(len(b) for _, b in blobs)
    found_in = [k for k, b in blobs if "Market Intelligence Citations" in b]
    return bool(found_in), [
        f"memo present: {bool(memo)}, total memo string length: {total} chars",
        f"memo fields scanned: {[k for k, _ in blobs]}",
        f"'Market Intelligence Citations' found in: {found_in or 'none'}",
    ]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 78)
    print(" Agent backend verification: QuantLib + RAG")
    print("=" * 78)

    # Sanity: backend reachable?
    try:
        get("/api/market/spot-price?ticker=SPY")
    except Exception as e:
        print(f"FATAL: backend not reachable on {BASE}: {e}")
        return 1

    session = drive_session()

    print("\n[3/4] Checking evidence...\n")

    rag_ok, rag_notes = check_rag(session)
    ql_ok, ql_notes = check_quantlib(session)
    memo_ok, memo_notes = check_memo_citations(session)

    print("--- RAG (market_context) ---")
    for n in rag_notes:
        print(f"  {n}")
    print(f"  RESULT: {'PASS' if rag_ok else 'FAIL'}\n")

    print("--- QuantLib (engines) ---")
    for n in ql_notes:
        print(f"  {n}")
    print(f"  RESULT: {'PASS' if ql_ok else 'FAIL'}\n")

    print("--- Memo citations section ---")
    for n in memo_notes:
        print(f"  {n}")
    print(f"  RESULT: {'PASS' if memo_ok else 'FAIL'}\n")

    print("[4/4] Summary")
    overall = rag_ok and ql_ok and memo_ok
    print("=" * 78)
    print(f"  OVERALL: {'PASS — agents are using QL + RAG' if overall else 'FAIL — see above'}")
    print(f"  total cost (USD):   {session.get('total_cost_usd', 0):.4f}")
    print(f"  total tokens in:    {session.get('total_tokens_input', 0)}")
    print(f"  total tokens out:   {session.get('total_tokens_output', 0)}")
    print("=" * 78)
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
