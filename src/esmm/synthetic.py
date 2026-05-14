"""Synthetic order book generator.

Produces a deterministic (seeded) sequence of OrderBookSnapshots with a
GBM-driven mid + Poisson-distributed depth. Used by the test suite and by
the API demo endpoint so the platform is fully functional without a paid
tick-data subscription.

Real research would consume databento / polygon / TAQ data via a
DataAdapter that produces the same OrderBookSnapshot type — synthetic
and real data are interchangeable from the engine's point of view.
"""

from __future__ import annotations

import math
import random

from src.esmm.schemas import OrderBookLevel, OrderBookSnapshot


def generate_order_book_path(
    symbol: str = "SPY",
    n_snaps: int = 500,
    start_price: float = 500.0,
    mu: float = 0.0,
    sigma_per_step: float = 0.0005,  # ~ 8 bps per step
    base_spread_bps: float = 5.0,
    base_size: float = 200.0,
    levels: int = 5,
    tick_size: float = 0.01,
    dt_seconds: float = 1.0,
    seed: int = 42,
) -> list[OrderBookSnapshot]:
    """GBM-driven mid with a synthetic L2 around it."""
    rng = random.Random(seed)
    snaps: list[OrderBookSnapshot] = []
    mid = start_price
    ts = 0.0

    for _ in range(n_snaps):
        # GBM step
        z = rng.gauss(0.0, 1.0)
        mid *= math.exp((mu - 0.5 * sigma_per_step**2) + sigma_per_step * z)

        # Build symmetric L2 with poisson-ish noise
        spread = mid * base_spread_bps * 1e-4
        spread = max(spread, tick_size)
        bid0 = round((mid - spread / 2) / tick_size) * tick_size
        ask0 = round((mid + spread / 2) / tick_size) * tick_size

        bids = []
        asks = []
        for k in range(levels):
            size_b = max(1.0, base_size * (1 + 0.4 * rng.random()) * (1 - 0.15 * k))
            size_a = max(1.0, base_size * (1 + 0.4 * rng.random()) * (1 - 0.15 * k))
            bids.append(OrderBookLevel(price=round(bid0 - k * tick_size, 4), size=round(size_b, 2)))
            asks.append(OrderBookLevel(price=round(ask0 + k * tick_size, 4), size=round(size_a, 2)))

        snaps.append(
            OrderBookSnapshot(ts=ts, symbol=symbol, bids=bids, asks=asks)
        )
        ts += dt_seconds

    return snaps


def imbalanced_path(
    n_snaps: int = 200,
    bias: str = "bid_heavy",
    seed: int = 7,
) -> list[OrderBookSnapshot]:
    """Convenience generator for testing OBI / micro-price behaviour."""
    snaps = generate_order_book_path(n_snaps=n_snaps, seed=seed)
    if bias == "bid_heavy":
        for s in snaps:
            for lvl in s.bids:
                object.__setattr__(lvl, "size", lvl.size * 3.0)
    elif bias == "ask_heavy":
        for s in snaps:
            for lvl in s.asks:
                object.__setattr__(lvl, "size", lvl.size * 3.0)
    return snaps
