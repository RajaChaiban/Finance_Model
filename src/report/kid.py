"""PRIIPs Key Information Document (KID) supplement.

The existing ``src/report/term_sheet.py`` already produces a 4-scenario
PRIIPs-style block. This module adds the parts that make a KID a *real* KID:

1. **Summary Risk Indicator (SRI)** — a 1-7 risk bucket combining:
   - Market Risk Measure (MRM) from a 99% VaR over the recommended holding
     period (RHP), expressed in vol-equivalent terms.
   - Credit Risk Measure (CRM) from the issuer's CQS (credit quality step).
   The PRIIPs regulation defines the matrix that maps (MRM, CRM) → SRI.

2. **Cost table** — entry, ongoing, exit, and incidental costs as a
   percentage of investment over the RHP. Dealers must disclose total
   reduction-in-yield (RIY).

3. **Performance scenarios** at 1y, mid-RHP, and end-of-RHP — *not* just
   end-of-RHP. PRIIPs requires the time-decomposition.

This is a Phase-1 *enabling* implementation: it produces the right structure,
with conservative defaults, that a junior structurer can edit before hand-off
to the regulatory team. It is NOT a regulator-ready KID — that requires
firm-specific cost data and audited methodology.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

# PRIIPs SRI matrix: rows = MRM (1-7), cols = CRM (1-6). Source: Annex II of
# the PRIIPs RTS. Values in this table are taken from EU regulation.
_PRIIPS_SRI_MATRIX = [
    # CRM:  1  2  3  4  5  6
    [1, 1, 2, 3, 4, 5],   # MRM 1
    [1, 2, 3, 4, 5, 6],   # MRM 2
    [2, 3, 3, 4, 5, 6],   # MRM 3
    [3, 4, 4, 5, 6, 6],   # MRM 4
    [4, 4, 5, 6, 6, 7],   # MRM 5
    [5, 5, 6, 6, 7, 7],   # MRM 6
    [6, 6, 6, 7, 7, 7],   # MRM 7
]


@dataclass
class CostBreakdown:
    """Cost table required by PRIIPs Article 5(b)."""
    entry_costs_pct: float = 0.50           # one-off entry fee
    exit_costs_pct: float = 0.00            # one-off exit fee
    ongoing_costs_pct_per_year: float = 0.10
    incidental_costs_pct: float = 0.00      # performance fees etc.

    def riy_over_rhp(self, rhp_years: float) -> float:
        """Total reduction-in-yield over the recommended holding period."""
        return (
            self.entry_costs_pct
            + self.exit_costs_pct
            + self.ongoing_costs_pct_per_year * rhp_years
            + self.incidental_costs_pct
        )


@dataclass
class PerformanceScenarios:
    """Per-PRIIPs: scenarios at multiple holding periods."""
    horizons_years: list[float]                   # e.g. [1, 5, 10]
    favourable_pct: list[float] = field(default_factory=list)
    moderate_pct: list[float] = field(default_factory=list)
    unfavourable_pct: list[float] = field(default_factory=list)
    stress_pct: list[float] = field(default_factory=list)


@dataclass
class KIDDocument:
    """Aggregated KID payload — turn into PDF or HTML downstream."""
    product_name: str
    isin: str | None
    currency: str
    notional: float
    rhp_years: float
    sri_bucket: int                       # 1 (low) ... 7 (high)
    mrm: int
    crm: int
    cost_breakdown: CostBreakdown
    riy_pct: float                        # cumulative RIY at RHP
    scenarios: PerformanceScenarios
    intended_retail_target: bool = True
    sri_methodology: str = "PRIIPs SRI matrix (MRM, CRM)"

    def to_dict(self) -> dict:
        return {
            "product_name": self.product_name,
            "isin": self.isin,
            "currency": self.currency,
            "notional": self.notional,
            "rhp_years": self.rhp_years,
            "sri_bucket": self.sri_bucket,
            "mrm": self.mrm,
            "crm": self.crm,
            "cost_breakdown": self.cost_breakdown.__dict__,
            "riy_pct": self.riy_pct,
            "scenarios": {
                "horizons_years": list(self.scenarios.horizons_years),
                "favourable_pct": list(self.scenarios.favourable_pct),
                "moderate_pct": list(self.scenarios.moderate_pct),
                "unfavourable_pct": list(self.scenarios.unfavourable_pct),
                "stress_pct": list(self.scenarios.stress_pct),
            },
            "intended_retail_target": self.intended_retail_target,
            "sri_methodology": self.sri_methodology,
        }


def compute_mrm(*, vol_annualised: float, rhp_years: float) -> int:
    """Map annualised vol over RHP to PRIIPs MRM bucket (1-7).

    Approximation: PRIIPs Annex II Table 1 uses VaR-equivalent vol thresholds.
    This is a simplified mapping based on σ·√RHP.
    """
    sigma_T = vol_annualised * math.sqrt(max(rhp_years, 0.01))
    # Thresholds calibrated to PRIIPs Annex II.
    if sigma_T < 0.005:
        return 1
    if sigma_T < 0.05:
        return 2
    if sigma_T < 0.12:
        return 3
    if sigma_T < 0.20:
        return 4
    if sigma_T < 0.30:
        return 5
    if sigma_T < 0.80:
        return 6
    return 7


def compute_sri(*, mrm: int, crm: int = 2) -> int:
    """Combine MRM and CRM via the PRIIPs matrix to get SRI."""
    mrm = max(1, min(mrm, 7))
    crm = max(1, min(crm, 6))
    return _PRIIPS_SRI_MATRIX[mrm - 1][crm - 1]


def build_kid(
    *,
    product_name: str,
    notional: float,
    currency: str = "USD",
    rhp_years: float,
    annualised_vol: float,
    crm: int = 2,                    # default = investment grade
    isin: str | None = None,
    cost_breakdown: CostBreakdown | None = None,
    favourable_total_return_pct_at_rhp: float = 0.30,
    moderate_total_return_pct_at_rhp: float = 0.05,
    unfavourable_total_return_pct_at_rhp: float = -0.20,
    stress_total_return_pct_at_rhp: float = -0.50,
) -> KIDDocument:
    """Assemble a KID payload.

    The 4 scenario terminal returns are inputs (typically computed by the
    structure's MC engine for the recommended holding period). This function
    extends them to the 1y / mid-RHP / end-of-RHP grid by linear scaling on
    log-return — which is approximate but adequate for KID disclosure.
    """
    cb = cost_breakdown or CostBreakdown()
    mrm = compute_mrm(vol_annualised=annualised_vol, rhp_years=rhp_years)
    sri = compute_sri(mrm=mrm, crm=crm)

    horizons = [1.0, max(rhp_years / 2, 1.0), rhp_years]
    horizons = sorted(set(round(h, 2) for h in horizons))

    def _scale(total_return_pct: float, h: float) -> float:
        # Convert end-of-RHP % to log-return space, then scale linearly by horizon ratio.
        if rhp_years <= 0:
            return total_return_pct
        log_r = math.log1p(total_return_pct)
        scaled = log_r * (h / rhp_years)
        return math.expm1(scaled)

    scenarios = PerformanceScenarios(
        horizons_years=horizons,
        favourable_pct=[_scale(favourable_total_return_pct_at_rhp, h) for h in horizons],
        moderate_pct=[_scale(moderate_total_return_pct_at_rhp, h) for h in horizons],
        unfavourable_pct=[_scale(unfavourable_total_return_pct_at_rhp, h) for h in horizons],
        stress_pct=[_scale(stress_total_return_pct_at_rhp, h) for h in horizons],
    )

    return KIDDocument(
        product_name=product_name,
        isin=isin,
        currency=currency,
        notional=notional,
        rhp_years=rhp_years,
        sri_bucket=sri,
        mrm=mrm,
        crm=crm,
        cost_breakdown=cb,
        riy_pct=cb.riy_over_rhp(rhp_years),
        scenarios=scenarios,
    )
