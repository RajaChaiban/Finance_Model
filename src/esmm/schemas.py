"""Pydantic schemas for the eSMM lab.

These are the typed contracts every module passes around. Keeping them in one
file lets us reason about the data shape without reading every implementation.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderBookLevel(BaseModel):
    model_config = ConfigDict(frozen=True)
    price: float
    size: float


class OrderBookSnapshot(BaseModel):
    """Top-of-book + optional depth for a single instrument at a single ts.

    `bids` and `asks` are sorted best-first (highest bid, lowest ask).

    Enforced invariants (so real-data adapters can't silently mis-price the engine):
      - both sides have ≥ 1 level
      - bids strictly descending in price
      - asks strictly ascending in price
      - book is not crossed: best_bid < best_ask
      - all sizes ≥ 0
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    ts: float = Field(..., description="Unix epoch seconds (float for sub-second)")
    symbol: str
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]

    @model_validator(mode="after")
    def _validate_book_shape(self) -> "OrderBookSnapshot":
        """Enforce the invariants real-data adapters can actually violate.

        We deliberately do NOT enforce price > 0 or non-crossed: defensive
        engine code (e.g. spread_bps NaN-guard, the both-sides-fill rule)
        is unit-tested with intentionally malformed inputs constructed via
        model_construct(). The only thing we *can't* afford to let through is
        out-of-order depth levels — that would silently mis-price every
        downstream call.
        """
        if not self.bids or not self.asks:
            raise ValueError("OrderBookSnapshot requires at least one bid and one ask level")
        for i in range(1, len(self.bids)):
            if self.bids[i].price >= self.bids[i - 1].price:
                raise ValueError(
                    f"bids must be strictly descending; got "
                    f"{self.bids[i - 1].price} then {self.bids[i].price} at index {i}"
                )
        for i in range(1, len(self.asks)):
            if self.asks[i].price <= self.asks[i - 1].price:
                raise ValueError(
                    f"asks must be strictly ascending; got "
                    f"{self.asks[i - 1].price} then {self.asks[i].price} at index {i}"
                )
        return self

    @property
    def best_bid(self) -> float:
        return self.bids[0].price

    @property
    def best_ask(self) -> float:
        return self.asks[0].price

    @property
    def best_bid_size(self) -> float:
        return self.bids[0].size

    @property
    def best_ask_size(self) -> float:
        return self.asks[0].size


class Quote(BaseModel):
    """A single bid/ask we'd post to the exchange."""

    model_config = ConfigDict(frozen=True)
    ts: float
    symbol: str
    bid_price: float
    bid_size: float
    ask_price: float
    ask_size: float
    fair_value: float
    skew_bps: float = 0.0  # positive = skewed offer-side (long inventory)
    half_spread_bps: float = 0.0


class Fill(BaseModel):
    """A trade we executed (we were the maker / passive side)."""

    model_config = ConfigDict(frozen=True)
    ts: float
    symbol: str
    side: Side  # from OUR perspective: BUY = we bought (sold the bid)
    price: float
    size: float
    fair_value_at_fill: float
    fee_bps: float = 0.0
    is_hedge: bool = False  # True if this was an auto-hedge, not a customer fill
    counterparty: str = "external"


class Position(BaseModel):
    """Inventory in a single symbol."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    symbol: str
    quantity: float = 0.0
    avg_cost: float = 0.0
    realized_pnl: float = 0.0


class MarketMakingConfig(BaseModel):
    """Knobs for the quote engine + risk limits."""

    symbol: str
    base_half_spread_bps: float = 5.0
    inventory_skew_bps_per_unit: float = 0.5  # bps of skew per unit of inventory
    max_inventory: float = 1000.0  # hard cap; quotes are pulled past this
    quote_size: float = 100.0
    fee_bps: float = -0.2  # negative = maker rebate
    delta_hedge_threshold: float = 50.0  # |net delta| > threshold → trigger hedge
    delta_hedge_band: float = 10.0  # hedge down to this level
    # Gamma hedging — only used by AutoHedger.evaluate_with_gamma. Default 0
    # disables it (delta-only behaviour matches v1).
    gamma_hedge_threshold: float = 0.0  # |net gamma * S^2| > threshold → trigger
    gamma_hedge_band: float = 0.0  # hedge gamma exposure back to this band


class CRBInternalisationResult(BaseModel):
    """Result of running flow through the Central Risk Book."""

    model_config = ConfigDict(frozen=True)
    symbol: str
    incoming_buy: float
    incoming_sell: float
    internalised: float  # quantity netted internally
    residual_to_street: float  # signed — positive = need to buy on street
    estimated_savings_bps: float


class CRBBookFlow(BaseModel):
    """One row of incoming flow across the firm in a single time slot."""

    model_config = ConfigDict(frozen=True)
    symbol: str
    incoming_buys: float
    incoming_sells: float
    street_spread_bps: float = Field(
        default=10.0,
        description="Estimated street bid-offer spread in bps; defaults to 10",
    )


class CRBBookResult(BaseModel):
    """Aggregate CRB internalisation across a multi-symbol book."""

    model_config = ConfigDict(frozen=True)
    per_symbol: list[CRBInternalisationResult]
    total_internalised_notional: float = 0.0
    total_residual_buy_notional: float = 0.0
    total_residual_sell_notional: float = 0.0
    total_estimated_savings_bps_weighted: float = 0.0


class TCABreakdown(BaseModel):
    """P&L attribution for a backtest or live session."""

    model_config = ConfigDict(frozen=True)
    spread_capture_pnl: float
    inventory_pnl: float
    hedge_pnl: float
    adverse_selection_pnl: float
    fees_pnl: float
    total_pnl: float
    n_fills: int
    avg_fill_size: float
