"""Microstructure feature library.

Features computed from a stream of OrderBookSnapshots + fill tape.
These feed both the quote engine (skew decisions) and the backtester
(adverse-selection labels).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from src.esmm.orderbook import log_return, mid_price, micro_price
from src.esmm.schemas import Fill, OrderBookSnapshot, Side


@dataclass
class RollingStats:
    """Welford-style rolling mean + variance over a fixed window."""

    window: int
    values: deque[float] = field(default_factory=deque)

    def add(self, x: float) -> None:
        self.values.append(x)
        while len(self.values) > self.window:
            self.values.popleft()

    @property
    def mean(self) -> float:
        n = len(self.values)
        return sum(self.values) / n if n else 0.0

    @property
    def variance(self) -> float:
        n = len(self.values)
        if n < 2:
            return 0.0
        mu = self.mean
        return sum((v - mu) ** 2 for v in self.values) / (n - 1)

    @property
    def std(self) -> float:
        return math.sqrt(self.variance)


class FeatureEngine:
    """Stateful feature extractor — feed snapshots, get a feature vector.

    Tracks:
    - rolling realized variance of log mid-returns (1s, 10s windows)
    - rolling micro-price - mid spread (drift toward fair value)
    - signed trade flow over the window
    - midprice momentum (sign of cumulative log return)
    """

    def __init__(self, fast_window: int = 10, slow_window: int = 60):
        self._fast_returns = RollingStats(fast_window)
        self._slow_returns = RollingStats(slow_window)
        self._signed_flow = RollingStats(slow_window)
        self._prev_mid: float | None = None
        self._snap_count = 0

    def update(self, snap: OrderBookSnapshot, recent_fills: list[Fill] | None = None) -> dict[str, float]:
        m = mid_price(snap)
        if self._prev_mid is not None:
            r = log_return(self._prev_mid, m)
            self._fast_returns.add(r)
            self._slow_returns.add(r)
        self._prev_mid = m
        self._snap_count += 1

        # signed flow: net buy-initiated minus sell-initiated size in this slot
        if recent_fills:
            net = sum(f.size if f.side == Side.BUY else -f.size for f in recent_fills)
            self._signed_flow.add(net)
        else:
            self._signed_flow.add(0.0)

        return {
            "mid": m,
            "micro": micro_price(snap),
            "micro_minus_mid_bps": 1e4 * (micro_price(snap) - m) / m if m > 0 else 0.0,
            "rv_fast": self._fast_returns.variance,
            "rv_slow": self._slow_returns.variance,
            "rv_ratio": (
                self._fast_returns.variance / self._slow_returns.variance
                if self._slow_returns.variance > 0
                else 1.0
            ),
            "momentum": sum(self._fast_returns.values),
            "signed_flow": self._signed_flow.mean,
        }


def realized_variance(snapshots: list[OrderBookSnapshot]) -> float:
    """Single-shot realized variance of log mid-returns."""
    if len(snapshots) < 2:
        return 0.0
    rets = [
        log_return(mid_price(snapshots[i - 1]), mid_price(snapshots[i]))
        for i in range(1, len(snapshots))
    ]
    if len(rets) < 2:
        return 0.0
    mu = sum(rets) / len(rets)
    return sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)


def signed_volume(fills: list[Fill]) -> float:
    """Net signed volume over a fill tape. Positive = net buyer-initiated."""
    return sum(f.size if f.side == Side.BUY else -f.size for f in fills)
