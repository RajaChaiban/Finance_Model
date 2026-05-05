"""HedgeTicket — what gets emailed to the flow desk after Gate C.

Once a candidate is approved at Gate C, the structuring desk hands a
**hedge ticket** to flow: net delta to hedge, gamma rebalance budget, and
listed-proxy suggestions for the wings (so the flow trader doesn't have to
guess which listed strikes to use against an OTC structure).

This is deliberately not a hedge plan — it's an *opening hedge* spec. The
flow desk owns rebalancing, P&L attribution, and intraday hedging from then
on. The structuring desk's job is to make sure the opening hedge is precise.

Outputs:
- ``opening_delta_shares`` — total shares to short/long against the position.
- ``opening_vega_per_pct`` — total vega (in $-per-1%σ) the desk inherits.
- ``gamma_rebal_budget_per_day`` — expected daily gamma cost (= 0.5·Γ·σ²·S²·dt).
- ``listed_proxies`` — listed options the trader can use to neutralize wings.
- ``rebalance_frequency`` — daily / weekly / on-event, based on gamma size.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ListedProxy:
    underlier: str
    expiry: str            # ISO date string
    strike: float
    side: str              # "call" or "put"
    quantity_contracts: int
    rationale: str         # e.g. "neutralize 25Δ put wing"


@dataclass
class HedgeTicket:
    """Opening-hedge specification for the flow desk."""

    candidate_id: str
    structure_name: str
    notional_usd: float

    opening_delta_shares: float       # shares to trade (long if positive)
    opening_vega_per_pct: float       # $ vega per 1% σ
    opening_gamma_per_dollar: float   # $ gamma per $1 spot

    gamma_rebal_budget_per_day: float
    rebalance_frequency: str          # "daily" | "weekly" | "on-event"
    listed_proxies: list[ListedProxy] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "structure_name": self.structure_name,
            "notional_usd": self.notional_usd,
            "opening_delta_shares": self.opening_delta_shares,
            "opening_vega_per_pct": self.opening_vega_per_pct,
            "opening_gamma_per_dollar": self.opening_gamma_per_dollar,
            "gamma_rebal_budget_per_day": self.gamma_rebal_budget_per_day,
            "rebalance_frequency": self.rebalance_frequency,
            "listed_proxies": [
                {
                    "underlier": p.underlier,
                    "expiry": p.expiry,
                    "strike": p.strike,
                    "side": p.side,
                    "quantity_contracts": p.quantity_contracts,
                    "rationale": p.rationale,
                }
                for p in self.listed_proxies
            ],
            "notes": list(self.notes),
        }


def build_hedge_ticket(
    *,
    candidate_id: str,
    structure_name: str,
    notional_usd: float,
    delta_per_share: float,
    gamma_per_share: float,
    vega_per_share: float,
    spot: float,
    sigma: float,
    underlier: str,
    expiry_iso: str,
) -> HedgeTicket:
    """Translate a structure's per-share Greeks into a hedge ticket.

    Conventions:
    - delta_per_share = ∂V/∂S in $/share, scalar over the structure
    - gamma_per_share = ∂²V/∂S² in $/share/$
    - vega_per_share = ∂V/∂σ in $/share per 1% σ

    Notional is converted to share count via spot — i.e. shares = notional/spot.
    """
    if spot <= 0:
        raise ValueError("spot must be positive")
    contracts_implied = max(notional_usd / spot, 0.0)
    opening_delta = delta_per_share * contracts_implied
    opening_vega = vega_per_share * contracts_implied
    opening_gamma = gamma_per_share * contracts_implied

    # Daily gamma rebalance cost: 0.5 · Γ · (σ · S)² · dt  (dt = 1/252).
    daily_var = (sigma * spot) ** 2 / 252.0
    gamma_cost = 0.5 * abs(opening_gamma) * daily_var

    # Rebalance frequency: heuristic — high gamma → daily; small → on-event.
    abs_gamma_dollars = abs(opening_gamma) * spot ** 2
    if abs_gamma_dollars > 0.005 * notional_usd:
        freq = "daily"
    elif abs_gamma_dollars > 0.001 * notional_usd:
        freq = "weekly"
    else:
        freq = "on-event"

    notes = []
    if abs(opening_delta) < contracts_implied * 0.05:
        notes.append("Opening delta near zero — structure is delta-neutral; minor flow hedge only.")
    if opening_vega < 0:
        notes.append("Negative vega — desk inherits short-vol risk; consider VIX call overlay.")

    proxies: list[ListedProxy] = []
    if abs(opening_vega) > 100.0:  # arbitrary "meaningful vega" threshold for proxy suggestion
        proxies.append(ListedProxy(
            underlier=underlier,
            expiry=expiry_iso,
            strike=spot,
            side="call" if opening_vega > 0 else "put",
            quantity_contracts=max(int(abs(opening_vega) / 100.0), 1),
            rationale="ATM vega proxy — listed-strike approximation of OTC vega tilt",
        ))

    return HedgeTicket(
        candidate_id=candidate_id,
        structure_name=structure_name,
        notional_usd=notional_usd,
        opening_delta_shares=opening_delta,
        opening_vega_per_pct=opening_vega,
        opening_gamma_per_dollar=opening_gamma,
        gamma_rebal_budget_per_day=gamma_cost,
        rebalance_frequency=freq,
        listed_proxies=proxies,
        notes=notes,
    )
