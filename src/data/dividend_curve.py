"""Dividend-curve forecasting.

Phase 1 (this file): a simple forecast curve that takes a base trailing yield
and decays it linearly to zero over a long horizon. Adequate for indicative
pricing on long-dated equity exotics where the dividend assumption is the
second-largest source of P&L after vol.

v2 (deferred): bootstrap from listed dividend futures (CME ED1, EUREX EDX1)
with bid/ask depth, then cross-check vs. consensus dividend estimates.

Used by:
- ``src/api/handlers.py`` — when the request flags long-dated American or
  autocallable products, build a `DividendCurve` rather than the scalar yield.
- ``src/engines/quantlib_engine.py:price_american_discrete_div_ql`` —
  consume the curve to seed the discrete-dividend schedule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass
class DividendCurve:
    """Continuous-yield forecast as a function of maturity.

    Two constructors:
      - ``flat(q)`` — single rate, identical to scalar (back-compat).
      - ``decay(q_today, half_life_years)`` — trailing yield decays to 0.
        Mimics the empirical fact that long-dated dividend futures trade
        below current yield as the market prices in cuts.
    """

    rates: list[tuple[float, float]]  # list of (T_years, q) anchor points, sorted by T
    label: str = "flat"

    @classmethod
    def flat(cls, q: float) -> "DividendCurve":
        return cls(rates=[(0.0, q), (50.0, q)], label="flat")

    @classmethod
    def decay(cls, q_today: float, decay_per_year: float = 0.05) -> "DividendCurve":
        """Linear decay: q(T) = max(q_today − decay_per_year · T, 0).

        decay_per_year is the *fraction of q_today* removed per year. A
        decay_per_year of 0.05 with q_today=0.02 means q falls by 0.001 per
        year (half-life ~10y).
        """
        anchors = []
        for t in (0.0, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 20.0, 50.0):
            q_t = max(q_today * (1.0 - decay_per_year * t), 0.0)
            anchors.append((t, q_t))
        return cls(rates=anchors, label="decay")

    def yield_at(self, T_years: float) -> float:
        """Linear-interpolate yield at maturity T (years)."""
        T = max(T_years, 0.0)
        anchors = self.rates
        if T <= anchors[0][0]:
            return anchors[0][1]
        if T >= anchors[-1][0]:
            return anchors[-1][1]
        for (t0, q0), (t1, q1) in zip(anchors[:-1], anchors[1:]):
            if t0 <= T <= t1:
                w = (T - t0) / (t1 - t0)
                return q0 * (1 - w) + q1 * w
        return anchors[-1][1]

    def average_yield(self, T_years: float) -> float:
        """Trapezoidal average yield from 0 to T — what a discounting/forwarding
        engine treats as the effective continuous q."""
        if T_years <= 0:
            return self.yield_at(0.0)
        n = 20
        dt = T_years / n
        s = 0.0
        prev = self.yield_at(0.0)
        for i in range(1, n + 1):
            curr = self.yield_at(i * dt)
            s += 0.5 * (prev + curr) * dt
            prev = curr
        return s / T_years
