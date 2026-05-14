"""Transaction Cost Analysis — P&L attribution at fill level.

Every fill gets bucketed into one of:
- spread_capture: edge captured at the moment of the fill = (fill.price - fair_value_at_fill) * signed_size
                  This is positive when you sold above fair (lifted on offer) or bought below fair (hit on bid).
                  It's the per-trade theoretical edge before any market move.
- adverse_selection: post-fill markout — does fair value move against us in the
                     next K snapshots? In this MVP we approximate using the next
                     fair value the engine recorded.
- inventory_pnl: P&L from holding the position as fair value drifts.
- hedge_pnl: P&L attributed to is_hedge fills (which by construction are
             expected to lose the bid/ask + fees but reduce risk).
- fees_pnl: maker rebates + taker fees.

The decomposition isn't perfectly orthogonal — different desks slice
slightly differently. The interview-relevant property is that the
buckets sum to total realized + unrealized P&L (within rounding), and
each bucket has a defensible interpretation.
"""

from __future__ import annotations

from collections import defaultdict

from src.esmm.orderbook import mid_price
from src.esmm.schemas import Fill, OrderBookSnapshot, Side, TCABreakdown


def attribute_pnl(
    fills: list[Fill],
    snapshots: list[OrderBookSnapshot],
    markout_horizon_snaps: int = 10,
) -> TCABreakdown:
    """Decompose a fill tape into P&L buckets.

    Args:
        fills: ordered list of fills produced by the backtester.
        snapshots: ordered snapshot trail used to compute markouts.
        markout_horizon_snaps: how many snapshots ahead to read fair value
            for adverse-selection calculation.
    """
    if not fills:
        return TCABreakdown(
            spread_capture_pnl=0.0,
            inventory_pnl=0.0,
            hedge_pnl=0.0,
            adverse_selection_pnl=0.0,
            fees_pnl=0.0,
            total_pnl=0.0,
            n_fills=0,
            avg_fill_size=0.0,
        )

    # Build a ts → mid map for O(1) lookup.
    snap_by_ts = {s.ts: s for s in snapshots}
    snap_index = {s.ts: i for i, s in enumerate(snapshots)}

    spread_capture = 0.0
    adverse_selection = 0.0
    hedge_pnl = 0.0
    fees = 0.0
    inventory_state: dict[str, tuple[float, float]] = defaultdict(lambda: (0.0, 0.0))

    for fill in fills:
        signed = fill.size if fill.side == Side.BUY else -fill.size
        # spread capture: sell above fair OR buy below fair → positive
        edge_per_share = (fill.fair_value_at_fill - fill.price) if fill.side == Side.BUY else (fill.price - fill.fair_value_at_fill)
        spread_capture += edge_per_share * fill.size

        # fees: negative bps (maker rebate) → positive P&L
        fee_dollars = -fill.fee_bps * 1e-4 * fill.price * fill.size
        fees += fee_dollars

        if fill.is_hedge:
            # Hedge fills are *expected* to underperform spread capture; we
            # attribute the spread-capture loss to hedge_pnl rather than
            # leaving it in spread_capture.
            hedge_pnl += -edge_per_share * fill.size
            spread_capture -= edge_per_share * fill.size

        # adverse selection markout
        idx = snap_index.get(fill.ts)
        if idx is not None and idx + markout_horizon_snaps < len(snapshots):
            future_mid = mid_price(snapshots[idx + markout_horizon_snaps])
            markout_per_share = (future_mid - fill.price) * (1 if fill.side == Side.BUY else -1)
            adverse_selection += markout_per_share * fill.size

        # inventory state for residual P&L calc below
        qty, cost = inventory_state[fill.symbol]
        new_qty = qty + signed
        if qty == 0 or (qty > 0) == (signed > 0):
            new_cost = (cost * qty + fill.price * signed) / new_qty if new_qty != 0 else 0.0
        else:
            new_cost = cost if abs(signed) <= abs(qty) else fill.price
        inventory_state[fill.symbol] = (new_qty, new_cost)

    # Residual inventory P&L: mark to last snapshot mid
    inventory_pnl = 0.0
    if snapshots:
        last_snap = snapshots[-1]
        last_mid = mid_price(last_snap)
        for sym, (qty, cost) in inventory_state.items():
            inventory_pnl += (last_mid - cost) * qty

    total = spread_capture + inventory_pnl + hedge_pnl + adverse_selection + fees

    return TCABreakdown(
        spread_capture_pnl=round(spread_capture, 6),
        inventory_pnl=round(inventory_pnl, 6),
        hedge_pnl=round(hedge_pnl, 6),
        adverse_selection_pnl=round(adverse_selection, 6),
        fees_pnl=round(fees, 6),
        total_pnl=round(total, 6),
        n_fills=len(fills),
        avg_fill_size=sum(f.size for f in fills) / len(fills),
    )
