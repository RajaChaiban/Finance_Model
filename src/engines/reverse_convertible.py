"""Reverse convertible — yield-enhanced note linked to a single underlier.

Composition:
    long zero-coupon bond + short ATM put on the underlier
    (the bond pays a coupon; the short put leg gives the investor
     downside exposure if the underlier falls below strike at maturity).

Investor pays par; receives par + coupon if S_T ≥ K, else (S_T / K) · par
(plus the coupon, which is fixed).

This engine is a composition layer — it does not introduce a new pricing
algorithm. It calls Black-Scholes for the put leg and a discount-factor
calc for the bond, then nets.

Used by:
- ``src/engines/router.py`` if "reverse_convertible" is added to the routing
  table. (Not auto-wired today because the inputs differ from a standard
  option request — coupon rate is a structural parameter rather than σ/K.)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .black_scholes import price_european


@dataclass
class ReverseConvertibleResult:
    fair_value: float           # PV of the structure to the investor (USD)
    bond_pv: float
    put_pv: float
    coupon_rate: float          # the implied coupon at par, if not given
    method: str = "bond + short_put composition"

    def to_dict(self) -> dict:
        return self.__dict__


def price_reverse_convertible(
    *,
    S: float, K: float, r: float, sigma: float, T: float, q: float = 0.0,
    coupon_rate: float | None = None,
    notional: float = 1_000_000.0,
) -> ReverseConvertibleResult:
    """Price a reverse convertible.

    If ``coupon_rate`` is supplied, computes the structure's PV at that coupon.
    If ``coupon_rate is None``, solves for the par-pricing coupon (i.e. the
    coupon rate that makes the structure worth exactly ``notional`` today).
    """
    bond_pv_unit = math.exp(-r * T)         # zero-coupon factor per $1 face
    short_put_value = price_european(S, K, r, sigma, T, q, "put")

    if coupon_rate is None:
        # Solve par: notional = bond_pv + coupon_pv − put_value · n_units
        # where n_units = notional / K (so each "share" of put is sized to
        # the strike ratio).
        n_units = notional / K
        coupon_pv_unit = (1.0 - bond_pv_unit) / r if r > 0 else T  # annuity ≈ T at r→0
        # Solving:  notional = notional·bond_pv_unit + c·notional·coupon_pv_unit
        #                     − n_units · short_put_value
        rhs_no_coupon = notional * bond_pv_unit - n_units * short_put_value
        c = (notional - rhs_no_coupon) / (notional * coupon_pv_unit) if coupon_pv_unit > 0 else 0.0
        coupon_rate = float(c)

    # Now compute the structure's fair value at the determined coupon.
    coupon_pv = coupon_rate * notional * ((1.0 - bond_pv_unit) / r if r > 0 else T)
    bond_pv = notional * bond_pv_unit
    put_pv = (notional / K) * short_put_value
    fair = bond_pv + coupon_pv - put_pv

    return ReverseConvertibleResult(
        fair_value=float(fair),
        bond_pv=float(bond_pv),
        put_pv=float(put_pv),
        coupon_rate=float(coupon_rate),
    )
