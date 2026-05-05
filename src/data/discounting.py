"""Multi-curve discounting context.

Post-2008 every dealer prices off OIS-discounted, projection-curve-forward
cashflows. Pre-2008 single-curve assumes the same curve does both jobs;
that's a multi-bp error on anything > 6m.

This module wraps two `RateCurve`-shaped objects so handlers can ask
``ctx.discount_factor(t)`` and ``ctx.forward_rate(t)`` without caring which
curve is which. Today both curves can be the same `FlatRateCurve` — that's
the single-curve shim. v2 plugs an OIS bootstrap for the discount side and
a swap-curve / SOFR-future bootstrap for the projection side.

Used by:
- ``src/api/handlers.py`` — passes ``ctx`` instead of scalar ``r``.
- ``src/engines/router.py`` — lifts ``ctx.discount_rate(T)`` for the closed-form
  engines that still take a scalar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from src.data.rate_curve import FlatRateCurve


class _Curve(Protocol):
    def spot_rate(self, maturity_years: float) -> float: ...

    @property
    def kind(self) -> str: ...


@dataclass
class DiscountingContext:
    """A pair of curves: one for discounting, one for forward projection.

    For single-curve callers, pass ``DiscountingContext.flat(rate)`` and both
    sides resolve to the same ``FlatRateCurve``.
    """

    discount_curve: _Curve
    projection_curve: _Curve
    label: str = "single-curve"

    @classmethod
    def flat(cls, rate: float) -> "DiscountingContext":
        """Single-curve shim — both sides are the same flat rate."""
        c = FlatRateCurve(rate=rate)
        return cls(discount_curve=c, projection_curve=c, label="flat")

    @classmethod
    def dual(cls, ois_rate: float, projection_rate: float) -> "DiscountingContext":
        """Two flat curves with a fixed basis. Only useful for tests / illustrative."""
        return cls(
            discount_curve=FlatRateCurve(rate=ois_rate),
            projection_curve=FlatRateCurve(rate=projection_rate),
            label="dual-flat",
        )

    def discount_rate(self, t: float) -> float:
        return self.discount_curve.spot_rate(t)

    def projection_rate(self, t: float) -> float:
        return self.projection_curve.spot_rate(t)

    def discount_factor(self, t: float) -> float:
        import math
        return math.exp(-self.discount_rate(t) * t)

    @property
    def basis_bps(self) -> float:
        """Spread (projection - discount) at 1y, in bps. Useful for surfacing
        in the report so the client can see the curve assumption."""
        return (self.projection_rate(1.0) - self.discount_rate(1.0)) * 10_000.0


def discount_legacy_scalar(ctx: Optional[DiscountingContext], fallback_r: float, T: float) -> float:
    """Adapter for closed-form engines that still take a scalar `r`.

    If ``ctx`` is None, returns ``fallback_r`` unchanged (preserves old
    behaviour). Otherwise returns the discount-curve spot rate at T.
    """
    if ctx is None:
        return fallback_r
    return ctx.discount_rate(T)
