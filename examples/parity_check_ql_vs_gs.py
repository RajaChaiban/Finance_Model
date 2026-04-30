"""Parity check: QuantLib vs gs_quant European option pricing, side by side.

Run: ``python examples/parity_check_ql_vs_gs.py``
Optional: ``python examples/parity_check_ql_vs_gs.py --underlier SPX --spot 4500``

What it does
------------
For a 3 x 3 grid (3 strikes around the spot x 3 tenors) of European calls
AND puts, prices each option through both engines and prints a side-by-side
table with absolute and percent differences. Exits cleanly with a clear
message if Marquee credentials aren't set.

What "parity" means here (read this carefully)
----------------------------------------------
QuantLib prices the **scalar inputs you pass it** (S, r, sigma, T, q).
gs_quant prices the **instrument** against GS's live market data — which
means GS uses its own spot, vol surface, and rates, not yours. Even with
a fully-correct Black-Scholes implementation in both engines, **the prices
will not match exactly** unless your scalar inputs happen to align with
GS's view of the market today.

So the table answers two questions:

  1. Are the engines in the same ballpark for an at-the-money option?
     (Yes → both implementations are sane.)
  2. Where do they disagree, and by how much?
     (Disagreements localise the model + market-data differences.)

This script is a sanity harness, not a regression test. For automated
parity tests with controlled market data, see ``tests/test_engines.py``
and ``tests/test_engine_consistency.py``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Tuple

# Allow running from anywhere: prepend the repo root to sys.path.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.engines import gs_quant_engine  # noqa: E402
from src.engines.router import route_with_engine  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(message)s")


# ---------------------------------------------------------------------------
# Defaults — SPX-like scenario, easy to override on the CLI
# ---------------------------------------------------------------------------
DEFAULT_SPOT = 4500.0
DEFAULT_RATE = 0.045
DEFAULT_VOL = 0.20
DEFAULT_DIV = 0.015
DEFAULT_TENORS_DAYS = (30, 90, 180)
DEFAULT_STRIKE_OFFSETS_PCT = (-5.0, 0.0, 5.0)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="QL vs gs_quant parity check.")
    p.add_argument("--underlier", default="SPX",
                   help="GS underlier for gs_quant. Default: SPX.")
    p.add_argument("--spot", type=float, default=DEFAULT_SPOT,
                   help=f"Spot used by QL engine (gs_quant uses live). Default: {DEFAULT_SPOT}.")
    p.add_argument("--rate", type=float, default=DEFAULT_RATE,
                   help=f"Risk-free rate used by QL. Default: {DEFAULT_RATE}.")
    p.add_argument("--vol", type=float, default=DEFAULT_VOL,
                   help=f"Vol used by QL. Default: {DEFAULT_VOL}.")
    p.add_argument("--div", type=float, default=DEFAULT_DIV,
                   help=f"Dividend yield used by QL. Default: {DEFAULT_DIV}.")
    return p.parse_args()


def _build_grid(spot: float) -> List[Tuple[str, float, int]]:
    """Return list of (option_type, strike, tenor_days) for the grid."""
    grid: List[Tuple[str, float, int]] = []
    for side in ("european_call", "european_put"):
        for offset_pct in DEFAULT_STRIKE_OFFSETS_PCT:
            strike = round(spot * (1.0 + offset_pct / 100.0), 2)
            for days in DEFAULT_TENORS_DAYS:
                grid.append((side, strike, days))
    return grid


def _price_one(
    option_type: str,
    strike: float,
    tenor_days: int,
    *,
    spot: float,
    rate: float,
    vol: float,
    div: float,
    underlier: str,
) -> Tuple[float, float]:
    """Price one option under both engines. Returns (ql_price, gs_price)."""
    T = tenor_days / 365.0

    ql_pricer, _, _ = route_with_engine(option_type, engine="auto")
    ql_price, _, _ = ql_pricer(spot, strike, rate, vol, T, div)

    gs_pricer, _, _ = route_with_engine(option_type, engine="gs")
    gs_price, _, _ = gs_pricer(
        spot, strike, rate, vol, T, div, gs_underlier=underlier,
    )
    return float(ql_price), float(gs_price)


def _print_header(underlier: str, spot: float, rate: float, vol: float, div: float) -> None:
    print()
    print("=" * 88)
    print(f" QuantLib vs gs_quant European-option parity — underlier={underlier!r}")
    print("=" * 88)
    print(f" QL inputs:  S={spot:.2f}  r={rate:.4f}  σ={vol:.4f}  q={div:.4f}")
    print(f" GS inputs:  instrument only ({underlier}); GS uses its own live S/r/σ/q.")
    print("-" * 88)
    print(f" {'side':<14} {'strike':>10} {'tenor (d)':>10} "
          f"{'QL price':>12} {'GS price':>12} {'Δ abs':>10} {'Δ %':>8}")
    print("-" * 88)


def _print_row(
    option_type: str, strike: float, tenor_days: int,
    ql: float, gs: float,
) -> None:
    diff = gs - ql
    pct = (diff / ql * 100.0) if ql != 0 else float("nan")
    print(f" {option_type:<14} {strike:>10.2f} {tenor_days:>10d} "
          f"{ql:>12.4f} {gs:>12.4f} {diff:>10.4f} {pct:>7.2f}%")


def _print_error_row(option_type: str, strike: float, tenor_days: int, msg: str) -> None:
    print(f" {option_type:<14} {strike:>10.2f} {tenor_days:>10d} "
          f"{'(error)':>12} {'(error)':>12}     skipped — {msg}")


def main() -> int:
    args = _parse_args()

    if not gs_quant_engine.is_gs_available():
        print(
            "\n[WARN] gs_quant Marquee credentials not set.\n"
            "       Add GS_MARQUEE_CLIENT_ID and GS_MARQUEE_CLIENT_SECRET to .env.\n"
            "       Sign up at https://marquee.gs.com to get them.\n",
            file=sys.stderr,
        )
        return 2

    grid = _build_grid(args.spot)
    _print_header(args.underlier, args.spot, args.rate, args.vol, args.div)

    n_ok = 0
    n_err = 0
    abs_diffs: List[float] = []

    for option_type, strike, tenor_days in grid:
        try:
            ql, gs = _price_one(
                option_type, strike, tenor_days,
                spot=args.spot, rate=args.rate, vol=args.vol, div=args.div,
                underlier=args.underlier,
            )
            _print_row(option_type, strike, tenor_days, ql, gs)
            abs_diffs.append(abs(gs - ql))
            n_ok += 1
        except Exception as exc:  # noqa: BLE001
            _print_error_row(option_type, strike, tenor_days, repr(exc)[:60])
            n_err += 1

    print("-" * 88)
    if abs_diffs:
        mean_abs = sum(abs_diffs) / len(abs_diffs)
        max_abs = max(abs_diffs)
        print(f" Summary: {n_ok} priced OK, {n_err} errors. "
              f"Mean |Δ| = {mean_abs:.4f}, max |Δ| = {max_abs:.4f}")
    else:
        print(f" Summary: {n_ok} priced OK, {n_err} errors. No data to summarise.")
    print("=" * 88)
    print(
        " Reminder: prices are NOT expected to match — QL uses your scalar\n"
        " inputs while GS uses live market data. Big disagreements localise\n"
        " where your assumed σ / r / S diverges from GS's view of the market.\n"
    )
    return 0 if n_err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
