"""Inventory tracking — the heart of every market-making book.

A position has three numbers that matter:
- quantity: how much we hold (signed; negative = short)
- avg_cost: VWAP of the existing position (used for realized P&L on partial closes)
- realized_pnl: cumulative P&L from closed-out flow

When a fill lands, we either:
- Add to the position (same side as current sign) → update avg_cost weighted-avg
- Reduce/close the position (opposite side) → book realized P&L = (price - avg_cost) * closed_qty
- Flip the position → book realized on the closed slice, open new at fill price for the residual
"""

from __future__ import annotations

from src.esmm.schemas import Fill, Position, Side


class InventoryBook:
    """Per-symbol position tracker with full P&L accounting."""

    def __init__(self) -> None:
        self._positions: dict[str, Position] = {}

    def get(self, symbol: str) -> Position:
        return self._positions.get(symbol, Position(symbol=symbol))

    def positions(self) -> list[Position]:
        return list(self._positions.values())

    def apply_fill(self, fill: Fill) -> Position:
        """Apply a fill, mutating the inventory and booking realized P&L.

        Returns the new position.
        """
        pos = self.get(fill.symbol)
        signed_qty = fill.size if fill.side == Side.BUY else -fill.size

        new_qty = pos.quantity + signed_qty
        # Same-side: weighted-average the cost basis
        if pos.quantity == 0 or _same_sign(pos.quantity, signed_qty):
            total_cost = pos.avg_cost * pos.quantity + fill.price * signed_qty
            new_avg = total_cost / new_qty if new_qty != 0 else 0.0
            updated = Position(
                symbol=fill.symbol,
                quantity=new_qty,
                avg_cost=new_avg,
                realized_pnl=pos.realized_pnl,
            )
        else:
            # Opposite-side: book realized P&L on the closed slice.
            # closed_qty is the size that actually closes (capped at the existing position).
            closed_qty = min(abs(signed_qty), abs(pos.quantity))
            sign_of_close = 1 if pos.quantity > 0 else -1
            # P&L per unit when closing a long: fill.price - avg_cost.
            # When closing a short: avg_cost - fill.price.
            pnl_per_unit = (fill.price - pos.avg_cost) * sign_of_close
            realized_delta = pnl_per_unit * closed_qty - _fee_cost(fill, closed_qty)

            if abs(signed_qty) <= abs(pos.quantity):
                # Pure reduction or full close — keep avg_cost
                new_avg = pos.avg_cost if new_qty != 0 else 0.0
            else:
                # Position flipped — open the residual at fill price
                residual = abs(signed_qty) - abs(pos.quantity)
                new_avg = fill.price
                new_qty = residual * (1 if signed_qty > 0 else -1)
            updated = Position(
                symbol=fill.symbol,
                quantity=new_qty,
                avg_cost=new_avg,
                realized_pnl=pos.realized_pnl + realized_delta,
            )

        self._positions[fill.symbol] = updated
        return updated

    def mark_to_market(self, symbol: str, mark: float) -> float:
        """Unrealized P&L of `symbol` against `mark`. Long positions show
        positive when mark > avg_cost; shorts show positive when mark < avg_cost."""
        pos = self.get(symbol)
        if pos.quantity == 0:
            return 0.0
        return (mark - pos.avg_cost) * pos.quantity

    def total_pnl(self, marks: dict[str, float]) -> float:
        total = 0.0
        for sym, pos in self._positions.items():
            total += pos.realized_pnl
            if sym in marks:
                total += self.mark_to_market(sym, marks[sym])
        return total


def inventory_skew_bps(
    inventory: float, max_inventory: float, skew_bps_per_unit: float
) -> float:
    """Compute the bps of skew to apply to quotes given current inventory.

    Positive return → skew the offer UP (and bid up too) so we attract more
    sells than buys, thus reducing our long position. Symmetric for shorts.

    Saturates at max_inventory to avoid runaway skew when the limit is breached.
    """
    capped = max(-max_inventory, min(max_inventory, inventory))
    return capped * skew_bps_per_unit


def _same_sign(a: float, b: float) -> bool:
    return (a > 0 and b > 0) or (a < 0 and b < 0)


def _fee_cost(fill: Fill, qty: float) -> float:
    """Fee in P&L units. Negative fee_bps = rebate (booked as positive P&L)."""
    return fill.fee_bps * 1e-4 * fill.price * qty
