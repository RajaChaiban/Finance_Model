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

from src.esmm.orderbook import mid_price, spread_bps
from src.esmm.schemas import (
    CRBBookFlow,
    CRBBookResult,
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

    def internalise_book(
        self,
        snapshots_by_symbol: dict[str, OrderBookSnapshot],
        flows: list[CRBBookFlow],
    ) -> CRBBookResult:
        """Run multi-symbol firm flow through the CRB.

        For each symbol with both a snapshot and a flow row, internalise
        independently. Aggregate notional saved + residual into a single
        book-level result. Symbols without snapshots are silently dropped
        (caller should filter beforehand if that matters).
        """
        per_symbol: list[CRBInternalisationResult] = []
        total_internal_notional = 0.0
        total_buy_residual_notional = 0.0
        total_sell_residual_notional = 0.0
        weighted_savings = 0.0
        weighted_denom = 0.0

        for flow in flows:
            snap = snapshots_by_symbol.get(flow.symbol)
            if snap is None:
                continue
            result = self.internalise(
                snap, incoming_buys=flow.incoming_buys, incoming_sells=flow.incoming_sells
            )
            per_symbol.append(result)
            ref_price = mid_price(snap)
            internal_notional = result.internalised * ref_price
            total_internal_notional += internal_notional
            if result.residual_to_street > 0:
                total_buy_residual_notional += result.residual_to_street * ref_price
            else:
                total_sell_residual_notional += abs(result.residual_to_street) * ref_price
            # Weight savings by notional internalised (a $1bn match @ 5bps
            # matters more than a $10k match @ 50bps).
            weighted_savings += result.estimated_savings_bps * internal_notional
            weighted_denom += internal_notional

        avg_savings_bps = (
            weighted_savings / weighted_denom if weighted_denom > 0 else 0.0
        )
        return CRBBookResult(
            per_symbol=per_symbol,
            total_internalised_notional=total_internal_notional,
            total_residual_buy_notional=total_buy_residual_notional,
            total_residual_sell_notional=total_sell_residual_notional,
            total_estimated_savings_bps_weighted=avg_savings_bps,
        )
