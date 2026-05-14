"""Pydantic schemas for the eSMM lab.

These are the typed contracts every module passes around. Keeping them in one
file lets us reason about the data shape without reading every implementation.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


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
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    ts: float = Field(..., description="Unix epoch seconds (float for sub-second)")
    symbol: str
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]

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


class CRBInternalisationResult(BaseModel):
    """Result of running flow through the Central Risk Book."""

    model_config = ConfigDict(frozen=True)
    symbol: str
    incoming_buy: float
    incoming_sell: float
    internalised: float  # quantity netted internally
    residual_to_street: float  # signed — positive = need to buy on street
    estimated_savings_bps: float


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
