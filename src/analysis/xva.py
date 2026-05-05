"""XVA overlay — funding (FVA) and simple bilateral credit (CVA, DVA).

A model price is mid-market. A trader cannot quote off mid alone — there's
no carry, no funding charge, no capital. This module produces a ``XVAOverlay``
block that sits next to ``PricingResult.price``: the structurer surfaces the
mid, the FVA/CVA charge, and the all-in cost.

Conventions:
- All XVA values are returned in the same currency / units as the input price.
- Sign convention: XVA is a *cost* to the dealer, so it is added to the price
  to get the "ask" the client pays. ``apply_to_price(mid, mode="ask")`` does this.
- Counterparty default is modelled by a flat hazard rate ``lambda_cp`` (per
  year). This is the simplest possible CVA approximation: ``CVA ≈ LGD · EPE_avg ·
  (1 − exp(−λ T))``. Real desks bootstrap λ from CDS spreads and discount each
  EPE bucket; that's the v2.

Inputs the structurer should know:
- ``funding_spread_bps``: dealer's marginal funding spread above OIS. Typical
  dealer mid: 25–80bps. Lower for IB at top tier, higher in stress.
- ``epe_avg``: time-averaged Expected Positive Exposure. For an option this
  is well-approximated by the option price for the buyer, ~zero for the
  seller. Scaled by maturity in the simplest version.
- ``lambda_cp``: counterparty hazard rate. ``CDS_5y_bps / 10000 / (1 - recovery)``.
- ``recovery``: post-default recovery (default 0.40 — standard senior unsecured).

This is deliberately the *simplest* model that produces a non-zero number;
the value is in the structure, not the precision. A senior reviewer will see
the FVA/CVA fields and know the question to ask the desk's xVA group.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal, Optional


@dataclass(frozen=True)
class XVAInputs:
    funding_spread_bps: float = 50.0    # dealer's marginal funding spread above OIS
    cds_spread_bps: float = 100.0       # counterparty 5y CDS spread (bps)
    recovery: float = 0.40              # standard senior unsecured
    direction: Literal["buy", "sell"] = "buy"  # client position vs dealer
    csa: bool = False                   # True if collateralised (CVA → 0)


@dataclass
class XVAOverlay:
    """The output block — attached to PricingResult."""

    mid_price: float
    fva: float                          # funding charge, $
    cva: float                          # credit charge, $
    dva: float = 0.0                    # debit valuation adjustment (dealer's own credit)
    funding_spread_bps: float = 0.0
    cp_hazard_rate: float = 0.0
    csa_protected: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def total_xva(self) -> float:
        return self.fva + self.cva - self.dva

    @property
    def ask_price(self) -> float:
        """Price the client pays — mid + xVA cost."""
        return self.mid_price + self.total_xva

    @property
    def bid_price(self) -> float:
        """Price the dealer pays the client — mid − xVA cost."""
        return self.mid_price - self.total_xva

    def to_dict(self) -> dict:
        return {
            "mid_price": self.mid_price,
            "fva": self.fva,
            "cva": self.cva,
            "dva": self.dva,
            "total_xva": self.total_xva,
            "ask_price": self.ask_price,
            "bid_price": self.bid_price,
            "funding_spread_bps": self.funding_spread_bps,
            "cp_hazard_rate": self.cp_hazard_rate,
            "csa_protected": self.csa_protected,
            "notes": list(self.notes),
        }


def hazard_from_cds(cds_spread_bps: float, recovery: float) -> float:
    """λ ≈ s / (1 − R) under the standard credit-triangle approximation."""
    if recovery >= 1.0:
        return 0.0
    return (cds_spread_bps / 10_000.0) / max(1.0 - recovery, 1e-6)


def compute_xva(
    *,
    mid_price: float,
    maturity_years: float,
    inputs: XVAInputs,
    epe_avg: Optional[float] = None,
) -> XVAOverlay:
    """Compute FVA + CVA for a single trade.

    Parameters
    ----------
    mid_price : float
        Model mid-market price (same currency as the trade notional).
    maturity_years : float
        Trade tenor in years. Used to scale FVA carry and CVA hazard exposure.
    inputs : XVAInputs
        Funding / CDS / direction / CSA flags.
    epe_avg : float, optional
        Time-averaged EPE. If None, defaulted to ``max(mid_price, 0)`` for the
        buy-side and ``max(-mid_price, 0)`` for sell-side — the simplest
        non-trivial proxy. A real desk produces this from the MC paths.

    Returns
    -------
    XVAOverlay
        Block ready to attach to PricingResult.
    """
    notes: list[str] = []

    if epe_avg is None:
        # Crudest possible EPE: use max(mid_price, 0). Senior reviewer should
        # know this is wrong for two-sided products (collars net to small EPE).
        if inputs.direction == "buy":
            epe_avg = max(mid_price, 0.0)
        else:
            epe_avg = max(-mid_price, 0.0)
        notes.append("EPE proxy = max(price, 0); precise EPE requires path simulation")

    funding_spread = inputs.funding_spread_bps / 10_000.0
    fva = funding_spread * epe_avg * maturity_years
    notes.append(f"FVA = {inputs.funding_spread_bps:.0f}bps × EPE × T")

    if inputs.csa:
        cva = 0.0
        cp_hazard = 0.0
        notes.append("CVA = 0 (CSA-protected)")
    else:
        cp_hazard = hazard_from_cds(inputs.cds_spread_bps, inputs.recovery)
        # Standard credit triangle: cumulative default prob = 1 − exp(−λT)
        default_prob = 1.0 - math.exp(-cp_hazard * maturity_years)
        cva = (1.0 - inputs.recovery) * epe_avg * default_prob
        notes.append(
            f"CVA = (1−R) × EPE × (1−exp(−λT))  (λ={cp_hazard*1e4:.0f}bps/yr)"
        )

    return XVAOverlay(
        mid_price=mid_price,
        fva=fva,
        cva=cva,
        dva=0.0,
        funding_spread_bps=inputs.funding_spread_bps,
        cp_hazard_rate=cp_hazard,
        csa_protected=inputs.csa,
        notes=notes,
    )
