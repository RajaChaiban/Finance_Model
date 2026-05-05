"""Synthetic dealer bid-list — what a junior structurer does in Excel.

When an RFQ comes in, the structurer pings 3–5 dealers for indicative levels
to triangulate the desk's own quote. This module simulates that process by
generating N synthetic dealer quotes around a model mid, each with:
- A per-dealer bias (some dealers are "rich", some are "cheap" on certain
  structure types — this is observable in real flow).
- A per-dealer spread (top dealers run tighter than tier-2).
- An idiosyncratic noise term.

Use case: shows the desk's quote vs. the synthetic peer distribution. Lets
sales tell the client "we're 5bps inside the median." It is *not* a model
output — it's a sales tool.

This is calibrated to typical equity-exotic flow:
- 5 synthetic dealers (mirroring top-5 by ENA).
- Median spread ~2-3bps for vanilla, 8-12bps for KO, 15-25bps for autocall.
- Bias is structure-dependent (e.g. desk B is structurally cheap on long-vol
  protective puts because they short vol on autocalls).
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass
class DealerQuote:
    dealer_id: str
    bid_bps: float
    offer_bps: float
    mid_offset_bps: float           # mid - mid_model, in bps of notional


@dataclass
class BidList:
    structure_kind: str
    dealer_quotes: list[DealerQuote]
    median_mid_offset_bps: float
    median_spread_bps: float
    desk_quote_offset_bps: float    # desk's bid-mid - median bid-mid
    method: str = "synthetic_calibrated"

    def to_dict(self) -> dict:
        return {
            "structure_kind": self.structure_kind,
            "dealer_quotes": [
                {
                    "dealer_id": d.dealer_id,
                    "bid_bps": d.bid_bps,
                    "offer_bps": d.offer_bps,
                    "mid_offset_bps": d.mid_offset_bps,
                }
                for d in self.dealer_quotes
            ],
            "median_mid_offset_bps": self.median_mid_offset_bps,
            "median_spread_bps": self.median_spread_bps,
            "desk_quote_offset_bps": self.desk_quote_offset_bps,
            "method": self.method,
        }


# Per-structure typical spread (bid-offer in bps of notional). These are
# desk lore; production should pull from a calibrated dataset.
_SPREAD_BPS = {
    "long_put": 3.0,
    "long_call": 3.0,
    "put_spread": 5.0,
    "call_spread": 5.0,
    "collar": 6.0,
    "zero_cost_collar": 7.0,
    "covered_call": 4.0,
    "ko_put": 12.0,
    "ki_put": 12.0,
    "ko_call": 12.0,
    "ki_call": 12.0,
    "phoenix_autocall": 25.0,
    "worst_of_put": 18.0,
    "reverse_convertible": 15.0,
    "variance_swap": 30.0,           # var swaps are wide
    "digital_call": 8.0,
    "digital_put": 8.0,
}


# Bias bps applied per (dealer, structure_family). Reflects observed flow
# patterns — these are placeholders in this skeleton; v2 calibrates them.
_DEALER_BIASES: dict[str, dict[str, float]] = {
    "dealer_A": {"phoenix_autocall": -2.0, "ko_put": +1.0},
    "dealer_B": {"long_put": -3.0, "phoenix_autocall": +4.0},
    "dealer_C": {"variance_swap": -5.0},
    "dealer_D": {"reverse_convertible": +3.0},
    "dealer_E": {},
}


def synthesize_bid_list(
    *,
    structure_kind: str,
    desk_mid_bps: float = 0.0,
    seed: int | None = None,
) -> BidList:
    """Generate a synthetic dealer bid-list around a model mid.

    Parameters
    ----------
    structure_kind : str
        Matches `StructureKind.value` (e.g. "phoenix_autocall", "long_put").
    desk_mid_bps : float
        The desk's *own* mid (delta vs the model in bps; usually 0 if the
        desk's mid IS the model). Used to position the desk vs the peer
        distribution.
    seed : int, optional
        For deterministic reproduction in tests.

    Returns
    -------
    BidList
    """
    rng = random.Random(seed)
    base_spread = _SPREAD_BPS.get(structure_kind, 10.0)

    quotes: list[DealerQuote] = []
    for dealer_id, biases in _DEALER_BIASES.items():
        bias = biases.get(structure_kind, 0.0)
        idio = rng.uniform(-1.5, 1.5)         # idiosyncratic noise
        spread_mult = rng.uniform(0.85, 1.30) # tier-2 dealers run wider
        spread = base_spread * spread_mult
        mid_offset = bias + idio
        quotes.append(DealerQuote(
            dealer_id=dealer_id,
            bid_bps=mid_offset - spread / 2,
            offer_bps=mid_offset + spread / 2,
            mid_offset_bps=mid_offset,
        ))

    # Median offset and spread.
    sorted_offsets = sorted(q.mid_offset_bps for q in quotes)
    median_offset = sorted_offsets[len(sorted_offsets) // 2]
    sorted_spreads = sorted(q.offer_bps - q.bid_bps for q in quotes)
    median_spread = sorted_spreads[len(sorted_spreads) // 2]

    desk_offset = desk_mid_bps - median_offset

    return BidList(
        structure_kind=structure_kind,
        dealer_quotes=quotes,
        median_mid_offset_bps=median_offset,
        median_spread_bps=median_spread,
        desk_quote_offset_bps=desk_offset,
    )
