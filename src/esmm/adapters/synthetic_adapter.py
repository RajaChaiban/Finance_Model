"""SyntheticAdapter — wraps the GBM book generator behind the DataAdapter Protocol.

Always available, never hits the network, fully deterministic given a seed.
This is what tests + CI + the existing /api/esmm/backtest endpoint use.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Iterator

from src.esmm.schemas import OrderBookSnapshot
from src.esmm.synthetic import generate_order_book_path


class SyntheticAdapter:
    """GBM-driven L2 book generator. No network. Deterministic per seed."""

    name = "synthetic"

    def __init__(
        self,
        start_price: float = 500.0,
        mu: float = 0.0,
        sigma_per_step: float = 0.0005,
        base_spread_bps: float = 5.0,
        base_size: float = 200.0,
        levels: int = 5,
        tick_size: float = 0.01,
        dt_seconds: float = 1.0,
        seed: int = 42,
    ):
        self.start_price = start_price
        self.mu = mu
        self.sigma_per_step = sigma_per_step
        self.base_spread_bps = base_spread_bps
        self.base_size = base_size
        self.levels = levels
        self.tick_size = tick_size
        self.dt_seconds = dt_seconds
        self.seed = seed

    def replay(
        self, symbol: str, start: datetime, end: datetime
    ) -> Iterable[OrderBookSnapshot]:
        """Deterministic replay. `start`/`end` only used to size the path
        (n_snaps = floor((end - start) / dt_seconds)). The generator is GBM,
        not a true historical replay — naming is for API parity with other
        adapters."""
        seconds = max(0.0, (end - start).total_seconds())
        n_snaps = max(1, int(seconds / self.dt_seconds))
        return generate_order_book_path(
            symbol=symbol,
            n_snaps=n_snaps,
            start_price=self.start_price,
            mu=self.mu,
            sigma_per_step=self.sigma_per_step,
            base_spread_bps=self.base_spread_bps,
            base_size=self.base_size,
            levels=self.levels,
            tick_size=self.tick_size,
            dt_seconds=self.dt_seconds,
            seed=self.seed,
        )

    def stream(self, symbol: str) -> Iterator[OrderBookSnapshot]:
        """Endless stream — generator with no termination condition.

        Implemented for completeness; the backtester uses replay() only.
        """
        import math
        import random

        rng = random.Random(self.seed)
        mid = self.start_price
        ts = 0.0
        while True:
            z = rng.gauss(0.0, 1.0)
            mid *= math.exp(
                (self.mu - 0.5 * self.sigma_per_step**2)
                + self.sigma_per_step * z
            )
            # Re-use the generator helper for one step of book material
            one_step = generate_order_book_path(
                symbol=symbol,
                n_snaps=1,
                start_price=mid,
                sigma_per_step=self.sigma_per_step,
                base_spread_bps=self.base_spread_bps,
                base_size=self.base_size,
                levels=self.levels,
                tick_size=self.tick_size,
                dt_seconds=self.dt_seconds,
                seed=rng.randint(0, 2**31 - 1),
            )
            snap = one_step[0]
            # Re-stamp ts so it monotonically increases over the stream.
            yield OrderBookSnapshot(
                ts=ts,
                symbol=snap.symbol,
                bids=snap.bids,
                asks=snap.asks,
            )
            ts += self.dt_seconds
