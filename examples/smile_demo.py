"""VP-facing demo: live smile vs flat historical vol on a SPY knock-out call.

Run: ``python examples/smile_demo.py``

Pipeline
--------
1. Pull live SPY chain from Yahoo, cap to the front 6 expiries.
2. Per-quote IV inversion → 2-D variance surface (``ql.BlackVarianceSurface``).
3. Price a 90-day, ATM-strike, 90 %-barrier down-and-out call under
   (a) flat 30-day historical vol (the "naïve calculator" baseline) and
   (b) the surface, sampled at the barrier (the smile-aware path).
4. Print prices, σ-at-strike vs σ-at-barrier, bp difference, and write
   ``smile_grid.csv`` with the strike × expiry IV matrix.

The point: a structuring desk would never quote a barrier off a single
historical-vol number. This artefact shows the calibration step that
closes the realism gap.
"""

from __future__ import annotations

import csv
import logging
import sys
from datetime import date
from pathlib import Path

import numpy as np

# Allow running from anywhere: prepend the repo root to sys.path.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("smile_demo")


def main() -> int:
    try:
        import QuantLib as ql
        from src.api.market_data import (
            fetch_option_chain,
            get_dividend_yield,
            get_historical_volatility,
            get_risk_free_rate,
            get_spot_price,
        )
        from src.data.iv_grid import build_iv_grid
        from src.data.vol_surface import build_vol_surface, sample_sigma_for_closed_form
        from src.engines import quantlib_engine
    except Exception as exc:
        print(f"Failed to import dependencies: {exc}", file=sys.stderr)
        return 1

    ticker = "SPY"
    days_to_expiry = 90
    T = days_to_expiry / 365.0

    logger.info("Fetching live market data for %s ...", ticker)
    spot = get_spot_price(ticker)
    if spot is None or spot <= 0:
        logger.error("Could not fetch spot for %s; aborting demo.", ticker)
        return 1

    q = get_dividend_yield(ticker) or 0.015
    r = get_risk_free_rate(days_to_expiry)
    sigma_hist = get_historical_volatility(ticker, lookback_days=30) or 0.20

    K = float(round(spot))                # ATM call
    barrier = float(round(spot * 0.90))   # 10 % down-out barrier

    logger.info(
        "Spot=%.2f  K=%.2f  B=%.2f  r=%.4f  q=%.4f  sigma_hist_30d=%.4f",
        spot, K, barrier, r, q, sigma_hist,
    )

    logger.info("Building live IV surface ...")
    chain = fetch_option_chain(ticker, max_expiries=6)
    if not chain:
        logger.error("Empty option chain for %s; aborting demo.", ticker)
        return 1

    # Live SPY wings are illiquid → relax the inversion floor for the demo.
    grid = build_iv_grid(chain, S=spot, r=r, q=q, min_success_rate=0.4)
    surface = build_vol_surface(grid)
    handle = ql.BlackVolTermStructureHandle(surface)

    sigma_atm = float(surface.blackVol(T, K, True))
    sigma_barrier = sample_sigma_for_closed_form(
        surface, K=K, T=T, S=spot, barrier=barrier
    )

    logger.info("Surface sigma at strike : %.2f%%", sigma_atm * 100.0)
    logger.info("Surface sigma at barrier: %.2f%%", sigma_barrier * 100.0)
    logger.info("Smile lift (barrier - strike): %+.0f bp",
                (sigma_barrier - sigma_atm) * 10_000.0)

    logger.info("Pricing knock-out call (90 d, ATM, 10pct down-out)...")
    p_flat, _, _ = quantlib_engine.price_knockout_ql(
        spot, K, barrier, r, sigma_hist, T, q, "call",
    )
    p_smile_bridge, _, _ = quantlib_engine.price_knockout_ql(
        spot, K, barrier, r, sigma_barrier, T, q, "call",
    )
    p_handle, _, _ = quantlib_engine.price_knockout_ql(
        spot, K, barrier, r, sigma_atm, T, q, "call", vol_handle=handle,
    )

    print()
    print("=" * 70)
    print(f"  SPY knock-out call  K=${K:.0f}  B=${barrier:.0f}  {days_to_expiry}d")
    print("=" * 70)
    print(f"  Flat 30-day historical vol ({sigma_hist:.2%}):       ${p_flat:8.4f}")
    print(f"  Surface sigma at strike  ({sigma_atm:.2%}, QL handle): ${p_handle:8.4f}")
    print(f"  Surface sigma at barrier ({sigma_barrier:.2%}, bridge): ${p_smile_bridge:8.4f}")
    print("-" * 70)
    bridge_delta_bp = (p_smile_bridge - p_flat) / max(p_flat, 1e-9) * 10_000.0
    handle_delta_bp = (p_handle - p_flat) / max(p_flat, 1e-9) * 10_000.0
    print(f"  delta(bridge - flat) : {p_smile_bridge - p_flat:+8.4f}  ({bridge_delta_bp:+5.0f} bp)")
    print(f"  delta(handle - flat) : {p_handle - p_flat:+8.4f}  ({handle_delta_bp:+5.0f} bp)")
    print("=" * 70)
    print()

    out_path = Path(__file__).resolve().parent / "smile_grid.csv"
    with out_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["expiry_T_years"] + [f"K_{int(k)}" for k in grid.strikes])
        for i, T_row in enumerate(grid.expiries):
            row = [f"{T_row:.4f}"] + [
                f"{grid.iv[i, j]:.4f}" if np.isfinite(grid.iv[i, j]) else ""
                for j in range(len(grid.strikes))
            ]
            writer.writerow(row)
    print(f"Wrote IV grid -> {out_path}")

    if abs(p_smile_bridge - p_flat) < 1e-3:
        logger.warning(
            "Smile vs flat KO price delta < 0.1 ¢ — surface is suspiciously "
            "flat. Quote feed may be stale; check market hours."
        )
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
