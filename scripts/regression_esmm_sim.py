"""Backend regression test for the ESMM simulation platform.

Exercises every /api/esmm/sim/* endpoint plus the Python-level kernel
and arena harnesses. Returns exit 0 on full pass, 1 on any failure,
with per-check timing.

Mirrors scripts/regression_esmm_backend.py — designed to be run
standalone (with the API up on 8002) or via pytest indirectly.

Usage:
    python scripts/regression_esmm_sim.py
"""

from __future__ import annotations

import json
import math
import sys
import time
import urllib.error
import urllib.request

BASE_URL = "http://127.0.0.1:8002"
TIMEOUT_S = 30.0


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


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def _check_api_health() -> bool:
    code, _ = _get("/api/esmm/sim/scenarios")
    return code == 200


def _check_scenarios_list() -> bool:
    code, data = _get("/api/esmm/sim/scenarios")
    if code != 200:
        return False
    if not isinstance(data, list):
        return False
    ids = {row["scenario_id"] for row in data}
    expected = {
        "flash_crash_2010",
        "covid_mar_2020",
        "hot_cpi",
        "fomc_surprise",
        "opex_pin",
        "liquidity_drought",
    }
    return expected.issubset(ids)


def _check_participants_list() -> bool:
    code, data = _get("/api/esmm/sim/participants")
    return code == 200 and isinstance(data, list)


def _check_sandbox_empty() -> bool:
    code, body = _post(
        "/api/esmm/sim/sandbox",
        {
            "kernel": {
                "duration_sec": 0.05,
                "tick_interval_sec": 0.001,
                "snapshot_interval_sec": 0.01,
                "enable_latency": False,
            },
            "participants": [],
        },
    )
    if code != 200:
        return False
    return body["n_fills"] == 0 and body["n_snapshots"] >= 1


def _check_sandbox_with_risk() -> bool:
    code, body = _post(
        "/api/esmm/sim/sandbox",
        {
            "kernel": {
                "duration_sec": 0.05,
                "tick_interval_sec": 0.001,
                "snapshot_interval_sec": 0.01,
                "enable_latency": False,
                "symbol": "SPY",
                "starting_mid": 100.0,
            },
            "participants": [],
            "risk": {
                "max_notional_usd": 1_000_000.0,
                "max_net_delta": 10_000.0,
                "concentration_pct": 1.0,
                "daily_loss_kill_switch_usd": 50_000.0,
                "max_drawdown_pct": 0.75,
            },
        },
    )
    return code == 200 and body["n_risk_breaches"] == 0


def _check_unknown_participant_rejected() -> bool:
    code, _ = _post(
        "/api/esmm/sim/sandbox",
        {
            "kernel": {
                "duration_sec": 0.05,
                "tick_interval_sec": 0.001,
                "enable_latency": False,
            },
            "participants": [{"kind": "alien", "weight": 1.0}],
        },
    )
    return code == 400


def _check_kernel_python_determinism() -> bool:
    """Determinism check at the Python level (no HTTP)."""
    from src.esmm.sim.kernel import Kernel, KernelConfig

    def run_once(seed: int) -> tuple[int, float]:
        cfg = KernelConfig(
            duration_sec=0.1,
            tick_interval_sec=0.001,
            snapshot_interval_sec=0.005,
            enable_latency=True,
            seed=seed,
        )
        k = Kernel(cfg)
        result = k.run()
        return result.n_snapshots, result.final_mid

    a = run_once(7)
    b = run_once(7)
    return a == b


def _check_arena_python() -> bool:
    """Arena harness check at the Python level."""
    from dataclasses import dataclass, field
    from src.esmm.sim.arena import Arena, ArenaConfig
    from src.esmm.sim.kernel import KernelConfig
    from src.esmm.sim.lob import Order, OrderSide, OrderType

    @dataclass
    class FixedBuyer:
        participant_id: str
        size: float
        fired: bool = False

        def on_book(self, snap) -> None:
            pass

        def on_fill(self, fill) -> None:
            pass

        def decide(self, now: float):
            if self.fired or now < 0.005:
                return []
            self.fired = True
            return [
                Order(
                    order_id=0,
                    symbol="SPY",
                    side=OrderSide.BUY,
                    price=math.nan,
                    size=self.size,
                    ts=now,
                    owner_id=self.participant_id,
                    order_type=OrderType.MARKET,
                )
            ]

    arena = Arena(
        config=ArenaConfig(
            kernel_config=KernelConfig(
                duration_sec=0.05,
                tick_interval_sec=0.001,
                enable_latency=False,
            ),
            seed=11,
        ),
        strategies={
            "small": lambda cfg: FixedBuyer("small", size=50),
            "big": lambda cfg: FixedBuyer("big", size=200),
        },
    )
    r = arena.run()
    return len(r.per_strategy) == 2 and r.comparison["pnl_range"] >= 0


def _check_attribution_python() -> bool:
    from src.esmm.schemas import Fill, Side
    from src.esmm.sim.attribution import FillContext, attribute

    fills = [
        Fill(
            ts=0.0, symbol="SPY", side=Side.BUY, price=99.5, size=100,
            fair_value_at_fill=100.0, fee_bps=0.0,
        )
    ]
    contexts = [FillContext(regime="CALM", participant_kind="noise")]
    r = attribute(fills, contexts, initial_inventory=0.0, initial_mid=100.0, final_mid=100.0)
    return abs(r.actual_realized_pnl - 50.0) < 1e-6


def _check_risk_pretrade_python() -> bool:
    from src.esmm.sim.risk import RiskEngine, RiskLimits, RiskState

    eng = RiskEngine(RiskLimits(max_notional_usd=1000, concentration_pct=1.0))
    ok, _ = eng.check_pretrade(
        participant_id="mm",
        symbol="SPY",
        order_notional_usd=2000,
        order_delta=0,
        state=RiskState(ts=0.0),
    )
    return not ok


def _check_latency_determinism() -> bool:
    from src.esmm.sim.latency import LatencyConfig, LatencyModel

    a = LatencyModel(LatencyConfig(seed=42))
    b = LatencyModel(LatencyConfig(seed=42))
    return all(a.sample_submit_ms() == b.sample_submit_ms() for _ in range(50))


CHECKS = [
    ("api_health", _check_api_health),
    ("scenarios_list", _check_scenarios_list),
    ("participants_list", _check_participants_list),
    ("sandbox_empty", _check_sandbox_empty),
    ("sandbox_with_risk", _check_sandbox_with_risk),
    ("unknown_participant_rejected", _check_unknown_participant_rejected),
    ("kernel_determinism_py", _check_kernel_python_determinism),
    ("arena_py", _check_arena_python),
    ("attribution_py", _check_attribution_python),
    ("risk_pretrade_py", _check_risk_pretrade_python),
    ("latency_determinism_py", _check_latency_determinism),
]


def main() -> int:
    failures = 0
    print("=" * 60)
    print("ESMM SIM regression — running", len(CHECKS), "checks")
    print("=" * 60)
    for name, fn in CHECKS:
        t0 = time.perf_counter()
        try:
            ok = fn()
            elapsed_ms = (time.perf_counter() - t0) * 1000
            status = "PASS" if ok else "FAIL"
            if not ok:
                failures += 1
            print(f"  [{status}] {name:35s}  ({elapsed_ms:7.1f} ms)")
        except Exception as e:
            failures += 1
            elapsed_ms = (time.perf_counter() - t0) * 1000
            print(f"  [ERR ] {name:35s}  ({elapsed_ms:7.1f} ms) — {type(e).__name__}: {e}")

    print("=" * 60)
    if failures == 0:
        print(f"All {len(CHECKS)} checks passed.")
        return 0
    print(f"{failures}/{len(CHECKS)} checks FAILED.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
