"""Backend regression test for the eSMM lab.

Exercises every endpoint the UI hits, in the order the UI hits them.
Designed to be looped — run as a script and it returns exit 0 on full
pass, 1 on any failure, with timing and per-endpoint summaries to stdout.

Usage:
    python scripts/regression_esmm_backend.py           # one pass
    for i in 1 2 3 ... 10; do python scripts/regression_esmm_backend.py; done
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE_URL = "http://127.0.0.1:8002"

TIMEOUT_S = 15.0


def _post(path: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read())
        except Exception:
            payload = {"raw_error": str(e)}
        return e.code, payload


def _get(path: str) -> tuple[int, object]:
    try:
        with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=TIMEOUT_S) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, {"raw_error": str(e)}


def _check(name: str, condition: bool, detail: str = "") -> tuple[str, bool, str]:
    return (name, condition, detail)


def run_regression() -> int:
    results: list[tuple[str, bool, str]] = []
    t0 = time.time()

    # 1. Health
    status, _ = _get("/health")
    results.append(_check("health", status == 200, f"status={status}"))

    # 2. Synthetic book
    status, body = _post("/api/esmm/synthetic-book", {"n_snaps": 50, "symbol": "SPY", "seed": 7})
    ok = status == 200 and isinstance(body, list) and len(body) == 50
    results.append(_check("synthetic-book n_snaps=50", ok, f"status={status}, len={len(body) if isinstance(body, list) else 'N/A'}"))

    # 3. Quote (flat inventory)
    snap = body[-1] if isinstance(body, list) and body else None
    if snap is not None:
        status, qbody = _post(
            "/api/esmm/quote",
            {
                "snapshot": snap,
                "config": {"symbol": "SPY", "base_half_spread_bps": 10.0},
            },
        )
        ok = (
            status == 200
            and isinstance(qbody, dict)
            and qbody.get("bid_price", 0) < qbody.get("ask_price", 0)
            and qbody.get("fair_value", 0) > 0
        )
        results.append(_check("quote-flat-inventory", ok, f"status={status}, fv={qbody.get('fair_value') if isinstance(qbody, dict) else None}"))
    else:
        results.append(_check("quote-flat-inventory", False, "no snapshot available"))

    # 4. Quote with long position
    if snap is not None:
        status, qbody = _post(
            "/api/esmm/quote",
            {
                "snapshot": snap,
                "config": {"symbol": "SPY", "base_half_spread_bps": 10.0, "inventory_skew_bps_per_unit": 0.5},
                "seed_position": {"symbol": "SPY", "quantity": 200.0, "avg_cost": 500.0},
            },
        )
        ok = status == 200 and isinstance(qbody, dict) and qbody.get("skew_bps", 0) > 0
        results.append(_check("quote-long-inventory-positive-skew", ok, f"skew={qbody.get('skew_bps') if isinstance(qbody, dict) else None}"))

    # 5. Backtest
    status, bbody = _post(
        "/api/esmm/backtest",
        {
            "config": {
                "symbol": "SPY",
                "base_half_spread_bps": 8.0,
                "inventory_skew_bps_per_unit": 0.05,
                "max_inventory": 500.0,
                "quote_size": 50.0,
                "delta_hedge_threshold": 200.0,
                "delta_hedge_band": 50.0,
            },
            "n_snaps": 200,
            "seed": 42,
        },
    )
    ok = (
        status == 200
        and isinstance(bbody, dict)
        and bbody.get("n_quotes") == 200
        and "tca" in bbody
        and "mid_path_sample" in bbody
        and "inventory_path_sample" in bbody
    )
    results.append(_check("backtest n=200", ok, f"status={status}, n_quotes={bbody.get('n_quotes') if isinstance(bbody, dict) else None}, n_fills={bbody.get('n_fills') if isinstance(bbody, dict) else None}"))

    # 5b. TCA buckets sum to total
    if isinstance(bbody, dict) and "tca" in bbody:
        tca = bbody["tca"]
        components = (
            tca["spread_capture_pnl"]
            + tca["inventory_pnl"]
            + tca["hedge_pnl"]
            + tca["adverse_selection_pnl"]
            + tca["fees_pnl"]
        )
        ok = abs(components - tca["total_pnl"]) < 1e-3
        results.append(_check("tca-sums-to-total", ok, f"sum={components:.6f}, total={tca['total_pnl']:.6f}"))

    # 6. CRB single
    if snap is not None:
        status, cbody = _post(
            "/api/esmm/crb/internalise",
            {"snapshot": snap, "incoming_buys": 1000, "incoming_sells": 800, "internalisation_cap_pct": 1.0},
        )
        ok = status == 200 and isinstance(cbody, dict) and cbody.get("internalised") == 800
        results.append(_check("crb-internalise-overlap-800", ok, f"internalised={cbody.get('internalised') if isinstance(cbody, dict) else None}"))

    # 7. CRB book (multi-symbol)
    # Need a snapshot per symbol → fetch a few synthetic books first.
    snap_spy_status, snap_spy = _post("/api/esmm/synthetic-book", {"n_snaps": 10, "symbol": "SPY", "seed": 1})
    snap_qqq_status, snap_qqq = _post("/api/esmm/synthetic-book", {"n_snaps": 10, "symbol": "QQQ", "seed": 2})
    if (
        snap_spy_status == 200 and snap_qqq_status == 200
        and isinstance(snap_spy, list) and isinstance(snap_qqq, list)
        and snap_spy and snap_qqq
    ):
        status, cbbody = _post(
            "/api/esmm/crb/internalise-book",
            {
                "snapshots": [snap_spy[-1], snap_qqq[-1]],
                "flows": [
                    {"symbol": "SPY", "incoming_buys": 1000, "incoming_sells": 600},
                    {"symbol": "QQQ", "incoming_buys": 500, "incoming_sells": 1200},
                ],
                "internalisation_cap_pct": 1.0,
            },
        )
        ok = (
            status == 200
            and isinstance(cbbody, dict)
            and len(cbbody.get("per_symbol", [])) == 2
            and cbbody.get("total_internalised_notional", 0) > 0
        )
        results.append(_check("crb-internalise-book-multi-symbol", ok, f"per_symbol={len(cbbody.get('per_symbol', [])) if isinstance(cbbody, dict) else 'N/A'}"))

    # 8. Error path: invalid n_snaps (below min)
    status, _ = _post("/api/esmm/synthetic-book", {"n_snaps": 1})
    results.append(_check("error-422-on-bad-n_snaps", status == 422, f"got status={status}"))

    # 9. Error path: symbol mismatch on quote
    status, _ = _post(
        "/api/esmm/quote",
        {
            "snapshot": {
                "ts": 0.0, "symbol": "AAA",
                "bids": [{"price": 1, "size": 1}], "asks": [{"price": 2, "size": 1}],
            },
            "config": {"symbol": "BBB"},
        },
    )
    results.append(_check("error-400-on-symbol-mismatch", status == 400, f"got status={status}"))

    elapsed_ms = (time.time() - t0) * 1000
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)

    print(f"=== eSMM Backend Regression — {passed}/{total} passed in {elapsed_ms:.1f}ms ===")
    for name, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f"  ({detail})" if detail else ""))

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(run_regression())
