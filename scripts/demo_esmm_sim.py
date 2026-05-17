"""Real-world simulation demos.

Exercises the simulation platform against live REST endpoints with
concrete configurations a 20y trader would actually care about.
Prints structured results to stdout.
"""

from __future__ import annotations

import json
import time
import urllib.request

BASE = "http://127.0.0.1:8002"


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def _header(s: str) -> None:
    print("=" * 70)
    print(s)
    print("=" * 70)


def demo_1_calm_market_sandbox() -> None:
    _header("DEMO 1 — CALM-market sandbox with noise + MM flow")
    body = {
        "kernel": {
            "duration_sec": 2.0,
            "tick_interval_sec": 0.01,
            "snapshot_interval_sec": 0.05,
            "enable_latency": True,
            "starting_mid": 450.0,
            "starting_spread_bps": 15.0,  # wide seed → MM can step inside
            "seed": 42,
        },
        "participants": [
            {
                "kind": "noise",
                "params": {
                    "participant_id": "retail",
                    "symbol": "SPY",
                    "arrival_rate_hz": 5.0,
                    "lot_min": 100,
                    "lot_max": 300,
                    "aggressive_pct": 0.6,
                    "seed": 1,
                },
            },
            {
                "kind": "market_maker",
                "params": {
                    "participant_id": "mm_tight",
                    "config": {
                        "symbol": "SPY",
                        "base_half_spread_bps": 5.0,  # half of seed → MM is BBO
                        "inventory_skew_bps_per_unit": 0.05,
                        "max_inventory": 500,
                        "quote_size": 50,
                        "fee_bps": -0.2,
                        "delta_hedge_threshold": 10000,
                        "delta_hedge_band": 100,
                    },
                    "requote_interval_sec": 0.05,
                    "use_hedger": False,
                },
            },
        ],
    }
    t0 = time.perf_counter()
    r = _post("/api/esmm/sim/sandbox", body)
    elapsed = time.perf_counter() - t0
    print(f"  duration:    {r['duration_sec']:6.2f}s sim time, {elapsed*1000:6.1f}ms wall")
    print(f"  ticks:       {r['n_ticks']}")
    print(f"  snapshots:   {r['n_snapshots']}")
    print(f"  orders:      {r['n_orders_submitted']}")
    print(f"  fills:       {r['n_fills']}")
    print(f"  mid path:    {r['initial_mid']:.4f} -> {r['final_mid']:.4f}")
    print(f"  P&L:")
    for owner, pnl in r["pnl_per_participant"].items():
        inv = r["inventory_per_participant"].get(owner, 0)
        print(f"    {owner:12s}  pnl={pnl:+12.4f}  inv={inv:+8.0f}")


def demo_2_arena_three_mm() -> None:
    _header("DEMO 2 — Arena bake-off: tight vs balanced vs wide MM")
    flow = [
        {
            "kind": "noise",
            "params": {
                "participant_id": "noise_flow",
                "symbol": "SPY",
                "arrival_rate_hz": 8.0,
                "lot_min": 100,
                "lot_max": 250,
                "aggressive_pct": 0.7,
                "seed": 1,
            },
        },
    ]
    strategies = []
    # Half-spreads must stay BELOW seed half-spread (15bps/2 = 7.5bps)
    # so the MM is at BBO and can capture flow.
    for label, half_bps, size in [("tight", 2.0, 50), ("balanced", 4.0, 50), ("wide", 6.0, 50)]:
        strategies.append({
            "strategy_id": label,
            "participant": {
                "kind": "market_maker",
                "params": {
                    "config": {
                        "symbol": "SPY",
                        "base_half_spread_bps": half_bps,
                        "quote_size": size,
                        "max_inventory": 500,
                        "delta_hedge_threshold": 10000,
                        "fee_bps": -0.2,
                    },
                    "requote_interval_sec": 0.05,
                    "use_hedger": False,
                },
            },
        })
    body = {
        "kernel": {
            "duration_sec": 1.5,
            "tick_interval_sec": 0.01,
            "snapshot_interval_sec": 0.05,
            "enable_latency": False,
            "starting_mid": 450.0,
            "starting_spread_bps": 15.0,  # wide seed → strategies can fight inside
            "seed": 7,
        },
        "flow": flow,
        "strategies": strategies,
    }
    t0 = time.perf_counter()
    r = _post("/api/esmm/sim/arena", body)
    elapsed = time.perf_counter() - t0
    print(f"  run_id: {r['run_id']}   wall: {elapsed*1000:.1f}ms")
    print(f"  {'strategy':12s}  {'pnl':>12s}  {'inv':>8s}  {'fills':>6s}")
    for s in r["per_strategy"]:
        print(f"  {s['strategy_id']:12s}  {s['pnl']:+12.4f}  {s['final_inventory']:+8.0f}  {s['n_fills']:>6d}")
    c = r["comparison"]
    print()
    print(f"  best_pnl  : {c['best_pnl']['strategy_id']:10s} (P&L={c['best_pnl']['pnl']:+.4f})")
    print(f"  worst_pnl : {c['worst_pnl']['strategy_id']:10s} (P&L={c['worst_pnl']['pnl']:+.4f})")
    print(f"  pnl range : {c['pnl_range']:+.4f}")
    print(f"  pnl stdev : {c['pnl_stdev']:+.4f}")


def demo_3_agentic_flash_crash() -> None:
    _header("DEMO 3 — Agentic Layer-C loop vs COVID liquidity rout")
    body = {
        "scenario_id": "covid_mar_2020",  # widest seed (8bps) so MM can step inside
        "baseline_config": {
            "symbol": "SPY",
            "base_half_spread_bps": 2.5,  # 5bps total — inside the seed
            "inventory_skew_bps_per_unit": 0.05,
            "max_inventory": 500,
            "quote_size": 50,
            "fee_bps": -0.2,
            "delta_hedge_threshold": 10000,
            "delta_hedge_band": 100,
        },
        "flow": [
            {
                "kind": "noise",
                "params": {
                    "participant_id": "noise",
                    "symbol": "SPY",
                    "arrival_rate_hz": 30.0,    # hot enough to produce reliable fills
                    "aggressive_pct": 0.9,       # mostly market orders
                    "lot_min": 100,
                    "lot_max": 150,
                    "seed": 11,
                },
            },
        ],
        "acceptance_score": 80.0,
        "max_iterations": 3,
        "base_seed": 42,
        "duration_override_sec": 0.8,
    }

    t0 = time.perf_counter()
    r = _post("/api/esmm/sim/agentic", body)
    elapsed = time.perf_counter() - t0
    print(f"  converged: {r['converged']}   reason: {r['stopped_reason']}")
    print(f"  iterations: {len(r['iterations'])}   wall: {elapsed*1000:.0f}ms")
    if r["best_iteration"] is not None:
        print(f"  best iter: #{r['best_iteration']}  score: {r['best_score']:.1f}/100")
    print()
    print(f"  {'iter':>4s}  {'regime':10s}  {'score':>6s}  {'total_pnl':>10s}  {'spread':>9s}  {'adv_sel':>9s}")
    for it in r["iterations"]:
        print(
            f"  {it['iteration']:>4d}  {it['regime']:10s}  "
            f"{it['score']:6.1f}  {it['total_pnl']:+10.4f}  "
            f"{it['spread_capture_pnl']:+9.4f}  {it['adverse_selection_pnl']:+9.4f}"
        )


def demo_4_opex_pin() -> None:
    _header("DEMO 4 — OPEX pin scenario: mean-reverters dominate")
    body = {
        "kernel": {
            "duration_sec": 2.0,
            "tick_interval_sec": 0.01,
            "snapshot_interval_sec": 0.05,
            "enable_latency": False,
            "starting_mid": 450.0,
            "starting_spread_bps": 4.0,
            "seed": 13,
        },
        "participants": [
            {
                "kind": "noise",
                "params": {
                    "participant_id": "noise",
                    "symbol": "SPY",
                    "arrival_rate_hz": 4.0,
                    "seed": 13,
                },
            },
            {
                "kind": "mean_reverter",
                "params": {
                    "participant_id": "mean_rev",
                    "symbol": "SPY",
                    "window_sec": 1.0,
                    "zscore_threshold": 1.5,
                    "lot": 200,
                    "cooldown_sec": 0.1,
                    "pin_strike": 450.0,
                    "pin_strength_bps": 15.0,
                    "seed": 13,
                },
            },
            {
                "kind": "market_maker",
                "params": {
                    "participant_id": "mm",
                    "config": {
                        "symbol": "SPY",
                        "base_half_spread_bps": 4.0,
                        "quote_size": 50,
                        "max_inventory": 500,
                        "delta_hedge_threshold": 10000,
                    },
                    "use_hedger": False,
                },
            },
        ],
    }
    t0 = time.perf_counter()
    r = _post("/api/esmm/sim/sandbox", body)
    elapsed = time.perf_counter() - t0
    print(f"  mid path: {r['initial_mid']:.4f} -> {r['final_mid']:.4f}  (drift: {r['final_mid']-r['initial_mid']:+.4f})")
    print(f"  wall: {elapsed*1000:.0f}ms")
    print(f"  P&L:")
    for owner, pnl in sorted(r["pnl_per_participant"].items()):
        inv = r["inventory_per_participant"].get(owner, 0)
        print(f"    {owner:12s}  pnl={pnl:+12.4f}  inv={inv:+8.0f}")


if __name__ == "__main__":
    demo_1_calm_market_sandbox()
    print()
    demo_2_arena_three_mm()
    print()
    demo_3_agentic_flash_crash()
    print()
    demo_4_opex_pin()
