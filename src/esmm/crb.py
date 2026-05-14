"""Central Risk Book simulator.

The CRB sits between client flow and the street. Instead of hedging every
incoming trade externally, the firm maintains a *single* aggregated risk
book across desks. When a buy and a sell overlap, we *internalize* — match
them off-book at the CRB mid — saving the bid-ask spread on the netted
quantity. Only the residual is hedged on the street.

Real CRBs are vastly more complex (multi-asset, multi-venue, capital-aware,
priority-tier-aware). This module implements the canonical *internalization
math* on a single symbol, which is the conceptual core every interview
will probe.

Edge per internalised share is approximately:
    saved_spread = 0.5 * (street_bid_ask)
which is the half-spread we would have paid on each leg if we'd hedged
externally instead of crossing internally.
"""

from __future__ import annotations

from src.esmm.orderbook import spread_bps
from src.esmm.schemas import (
    CRBInternalisationResult,
    OrderBookSnapshot,
)


class CentralRiskBook:
    """Single-symbol CRB with capacity and priority knobs."""

    def __init__(self, internalisation_cap_pct: float = 1.0):
        """Args:
            internalisation_cap_pct: max fraction of overlapping flow to
                internalize. Real CRBs cap this for risk and information
                reasons (e.g. 0.6 means only net up to 60% of the overlap).
        """
        self.cap = internalisation_cap_pct

    def internalise(
        self,
        snap: OrderBookSnapshot,
        incoming_buys: float,
        incoming_sells: float,
    ) -> CRBInternalisationResult:
        """Run incoming firm-wide flow through the CRB.

        Args:
            snap: street order book for the symbol (used to estimate savings).
            incoming_buys: total firm-wide buy interest in the slot.
            incoming_sells: total firm-wide sell interest in the slot.

        Returns:
            Internalisation breakdown with street residual + estimated
            spread savings in bps.
        """
        overlap = min(incoming_buys, incoming_sells)
        internalised = overlap * self.cap
        residual = (incoming_buys - incoming_sells)  # signed: +ve = net buy on street

        street_spread_bps = spread_bps(snap)
        # Each internalised SHARE saves a half-spread on each leg = full spread.
        # Convention: report saved bps per internalised share (caller multiplies by qty if needed).
        saved_bps = street_spread_bps  # full spread saved per fully-matched pair

        return CRBInternalisationResult(
            symbol=snap.symbol,
            incoming_buy=incoming_buys,
            incoming_sell=incoming_sells,
            internalised=internalised,
            residual_to_street=residual,
            estimated_savings_bps=saved_bps,
        )
