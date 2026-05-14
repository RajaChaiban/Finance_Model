"""Quote engine — the brain of a market-making book.

Given fair value, current inventory, and a microstructure feature vector,
produce a (bid, ask) pair we'd post to the exchange.

Pricing recipe (the standard Avellaneda-Stoikov-style decomposition):
  1. Start from fair_value (mid, micro, or model price).
  2. Apply a half-spread = base_half_spread + adverse_selection_premium.
  3. Apply an inventory skew: long inventory → both bid AND ask shift down,
     so we offer competitively and bid passively.
  4. If |inventory| > max_inventory, pull the same-side quote (size=0).
"""

from __future__ import annotations

from src.esmm.inventory import InventoryBook, inventory_skew_bps
from src.esmm.orderbook import micro_price
from src.esmm.schemas import (
    MarketMakingConfig,
    OrderBookSnapshot,
    Quote,
)


class QuoteEngine:
    """Stateless given (config, inventory, market) — easy to test."""

    def __init__(self, config: MarketMakingConfig):
        self.config = config

    def quote(
        self,
        snap: OrderBookSnapshot,
        inventory: InventoryBook,
        fair_value: float | None = None,
        adverse_selection_bps: float = 0.0,
    ) -> Quote:
        """Generate a quote.

        Args:
            snap: latest order book snapshot for our symbol.
            inventory: live inventory book.
            fair_value: optional override; defaults to micro-price of `snap`.
            adverse_selection_bps: extra spread to charge when our recent
                fills have been hit by informed flow (set by the backtester).
        """
        if snap.symbol != self.config.symbol:
            raise ValueError(f"Snapshot symbol {snap.symbol} != config {self.config.symbol}")

        fv = fair_value if fair_value is not None else micro_price(snap)

        position = inventory.get(self.config.symbol)
        skew_bps = inventory_skew_bps(
            position.quantity,
            self.config.max_inventory,
            self.config.inventory_skew_bps_per_unit,
        )

        half_spread_bps = self.config.base_half_spread_bps + adverse_selection_bps
        half_spread = fv * half_spread_bps * 1e-4
        skew_amt = fv * skew_bps * 1e-4

        # Inventory skew shifts the WHOLE pair down for longs (and up for shorts).
        # This is the classic Stoikov result — fair value adjusts toward zero
        # inventory, then symmetric half-spread is applied.
        skewed_fv = fv - skew_amt

        bid = skewed_fv - half_spread
        ask = skewed_fv + half_spread

        # Pull the offending side past max inventory.
        bid_size = self.config.quote_size
        ask_size = self.config.quote_size
        if position.quantity >= self.config.max_inventory:
            bid_size = 0.0  # don't get any longer
        if position.quantity <= -self.config.max_inventory:
            ask_size = 0.0  # don't get any shorter

        return Quote(
            ts=snap.ts,
            symbol=self.config.symbol,
            bid_price=bid,
            bid_size=bid_size,
            ask_price=ask,
            ask_size=ask_size,
            fair_value=fv,
            skew_bps=skew_bps,
            half_spread_bps=half_spread_bps,
        )
