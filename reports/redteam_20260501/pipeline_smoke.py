"""Live pipeline smoke against the running backend on :8002.

Surfaces touched:
  - GET /health
  - GET /api/market/{spot-price, dividend-yield, risk-free-rate, historical-volatility, dividend-info, movers}
  - POST /api/price for all 12 option types (happy path)
  - POST /api/agent/sessions full lifecycle (gate A -> B -> C)
  - Direct call to MarketIntelligence.get_market_intelligence() (RAG)

Exit code:
  0 = all pass, non-zero = any failure. Failures printed inline.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8002"
results: list[tuple[str, str, str]] = []  # (section, label, outcome)


def post(path: str, body: dict, timeout: int = 60) -> tuple[int, dict | str]:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, str(e)
    except Exception as e:
        return 0, str(e)


def get(path: str, timeout: int = 30) -> tuple[int, dict | str]:
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, str(e)
    except Exception as e:
        return 0, str(e)


# ---------- 1. Health ----------
print("=" * 80)
print("1) Health")
status, body = get("/health")
ok = status == 200
results.append(("health", "/health", "PASS" if ok else f"FAIL status={status}"))
print(f"  /health -> {status}  {'OK' if ok else body}")

# ---------- 2. Market endpoints ----------
print("=" * 80)
print("2) Market endpoints (SPY)")
for path in [
    "/api/market/movers",
    "/api/market/spot-price?ticker=SPY",
    "/api/market/dividend-yield?ticker=SPY",
    "/api/market/risk-free-rate?days_to_expiration=90",
    "/api/market/historical-volatility?ticker=SPY",
    "/api/market/dividend-info?ticker=SPY",
]:
    status, body = get(path)
    ok = 200 <= status < 300
    results.append(("market", path, "PASS" if ok else f"FAIL {status}"))
    snippet = (json.dumps(body)[:120] + "...") if isinstance(body, dict) else str(body)[:120]
    print(f"  {status:3d}  {path:60s}  {snippet}")

# ---------- 3. Pricing matrix (12 types) ----------
print("=" * 80)
print("3) /api/price matrix (12 option types)")
base_payload = {
    "underlying": "SPY",
    "spot_price": 100.0, "strike_price": 100.0,
    "days_to_expiration": 90,
    "risk_free_rate": 0.05, "volatility": 0.20, "dividend_yield": 0.02,
}
matrix = [
    ("european_call", {}),
    ("european_put", {}),
    ("american_call", {}),
    ("american_put", {}),
    ("knockout_call", {"barrier_level": 120.0}),
    ("knockout_put", {"barrier_level": 80.0}),
    ("knockin_call", {"barrier_level": 120.0}),
    ("knockin_put", {"barrier_level": 80.0}),
    ("asian_call", {"averaging_method": "geometric", "averaging_frequency": "daily"}),
    ("asian_put", {"averaging_method": "geometric", "averaging_frequency": "daily"}),
    ("lookback_call", {"lookback_type": "floating"}),
    ("lookback_put", {"lookback_type": "fixed"}),
]
for option_type, extras in matrix:
    payload = {"option_type": option_type, **base_payload, **extras}
    status, body = post("/api/price", payload, timeout=120)
    ok = status == 200 and isinstance(body, dict) and isinstance(body.get("price"), (int, float)) and body["price"] >= 0
    if ok:
        method = body.get("method", "?")
        delta = body.get("greeks", {}).get("delta")
        results.append(("price", option_type, "PASS"))
        print(f"  {status:3d}  {option_type:18s}  price={body['price']:9.4f}  delta={delta!s:>10}  {method}")
    else:
        results.append(("price", option_type, f"FAIL {status}"))
        print(f"  {status:3d}  {option_type:18s}  FAIL  {str(body)[:150]}")

# ---------- 4. Agent lifecycle ----------
print("=" * 80)
print("4) /api/agent/sessions lifecycle")
intake = {
    "intake_form": {
        "underlying": "SPY",
        "notional_usd": 1_000_000,
        "view": "neutral",
        "horizon_days": 60,
        "budget_bps_notional": 200,
    }
}
status, body = post("/api/agent/sessions", intake, timeout=60)
session_ok = status == 200 and isinstance(body, dict) and "session_id" in body
if not session_ok:
    results.append(("agent", "create", f"FAIL {status} {str(body)[:200]}"))
    print(f"  CREATE FAILED status={status} body={str(body)[:300]}")
else:
    sid = body["session_id"]
    print(f"  created session {sid}  status={body.get('status')}")
    results.append(("agent", "create", "PASS"))

    def wait_for_status(prefix: str, target_substr: str, max_s: int = 60) -> tuple[bool, dict]:
        deadline = time.time() + max_s
        last = {}
        while time.time() < deadline:
            s, b = get(f"/api/agent/sessions/{sid}")
            if isinstance(b, dict):
                last = b
                st = (b.get("status") or "").lower()
                if target_substr in st:
                    return True, b
                if st in {"error", "cancelled", "failed"}:
                    return False, b
            time.sleep(1.5)
        return False, last

    # Wait for awaiting_gate_a
    reached_a, last = wait_for_status("A", "awaiting_gate_a", max_s=90)
    results.append(("agent", "reach_gate_a", "PASS" if reached_a else f"FAIL last_status={last.get('status') if isinstance(last, dict) else last}"))
    print(f"  reach gate A -> {last.get('status') if isinstance(last, dict) else last}")

    if reached_a:
        # Approve A
        st_a, body_a = post(f"/api/agent/sessions/{sid}/gate/a", {"approved": True})
        approve_a_ok = st_a == 200
        results.append(("agent", "approve_gate_a", "PASS" if approve_a_ok else f"FAIL {st_a}"))
        print(f"  approve gate A -> {st_a}")

        # Wait for B
        reached_b, last_b = wait_for_status("B", "awaiting_gate_b", max_s=120)
        results.append(("agent", "reach_gate_b", "PASS" if reached_b else f"FAIL {last_b.get('status') if isinstance(last_b, dict) else last_b}"))
        print(f"  reach gate B -> {last_b.get('status') if isinstance(last_b, dict) else last_b}")

        if reached_b:
            st_b, _ = post(f"/api/agent/sessions/{sid}/gate/b", {"approved": True})
            approve_b_ok = st_b == 200
            results.append(("agent", "approve_gate_b", "PASS" if approve_b_ok else f"FAIL {st_b}"))
            print(f"  approve gate B -> {st_b}")

            reached_c, last_c = wait_for_status("C", "awaiting_gate_c", max_s=180)
            results.append(("agent", "reach_gate_c", "PASS" if reached_c else f"FAIL {last_c.get('status') if isinstance(last_c, dict) else last_c}"))
            print(f"  reach gate C -> {last_c.get('status') if isinstance(last_c, dict) else last_c}")

            if reached_c:
                st_c, _ = post(f"/api/agent/sessions/{sid}/gate/c", {"approved": True})
                results.append(("agent", "approve_gate_c", "PASS" if st_c == 200 else f"FAIL {st_c}"))
                print(f"  approve gate C -> {st_c}")

                done, last_d = wait_for_status("done", "complet", max_s=60)
                results.append(("agent", "complete", "PASS" if done else f"FAIL {last_d.get('status') if isinstance(last_d, dict) else last_d}"))
                print(f"  reach completed -> {last_d.get('status') if isinstance(last_d, dict) else last_d}")

# ---------- 5. RAG probe ----------
print("=" * 80)
print("5) RAG / MarketIntelligence direct probe")
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
try:
    from src.agents.market_intelligence import get_market_intelligence
    mi = get_market_intelligence()
    if mi is None:
        # Disabled by env or init failed — record as PASS-disabled, not a failure.
        results.append(("rag", "get_market_intelligence", "PASS (disabled / None — expected when MARKET_INTEL_ENABLED off)"))
        print("  None  (singleton disabled or init returned None — not a hard failure)")
    else:
        # Try a retrieval if the API exposes one.
        ok = True
        preview = ""
        for method_name in ("retrieve", "query", "search", "get_context"):
            fn = getattr(mi, method_name, None)
            if callable(fn):
                try:
                    out = fn("SPY equity volatility outlook")
                    preview = f"{method_name}() -> type={type(out).__name__}"
                    break
                except Exception as e:
                    preview = f"{method_name}() raised {type(e).__name__}: {e}"
                    ok = False
                    break
        results.append(("rag", "get_market_intelligence", "PASS" if ok else f"FAIL {preview}"))
        print(f"  {'PASS' if ok else 'FAIL'}  type={type(mi).__name__}  {preview}")
except Exception as e:
    results.append(("rag", "get_market_intelligence", f"FAIL {type(e).__name__}: {e}"))
    print(f"  FAIL  {type(e).__name__}: {e}")

# ---------- Summary ----------
print("=" * 80)
print("SUMMARY")
print("=" * 80)
fail_count = 0
for section, label, outcome in results:
    icon = "PASS" if outcome == "PASS" else "FAIL"
    if outcome != "PASS":
        fail_count += 1
    print(f"  [{icon}]  {section:8s}  {label:36s}  {outcome}")

print(f"\n{len(results)} checks, {fail_count} failure(s)")
sys.exit(0 if fail_count == 0 else 1)
