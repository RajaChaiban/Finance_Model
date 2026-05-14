"""Auto-hedger — band-based delta hedging.

Rule:
- If |net_delta| > delta_hedge_threshold, trade the hedge instrument
  (typically a future or the underlying) to bring net_delta back to within
  delta_hedge_band.
- Direction: long delta → SELL the hedge; short delta → BUY the hedge.

This is the most basic hedging discipline in equity options MM.
A real desk would use additional triggers (gamma, vega, regime changes,
imminent events). This module exposes the band-rule and lets the backtester
or a future agent layer compose more sophisticated triggers.
"""

from __future__ import annotations

from src.esmm.schemas import Fill, MarketMakingConfig, Side


class AutoHedger:
    """Stateless rule evaluator. Returns a hedge fill or None each step."""

    def __init__(self, config: MarketMakingConfig, hedge_symbol: str | None = None):
        self.config = config
        # Default hedge instrument is the same symbol (cash equity hedging itself).
        # For options MM, override with the underlier or a future ticker.
        self.hedge_symbol = hedge_symbol or config.symbol

    def evaluate(
        self,
        ts: float,
        net_delta: float,
        hedge_price: float,
        hedge_fee_bps: float = 1.0,
    ) -> Fill | None:
        """If we're outside the band, emit a hedge fill that brings us back to band.

        Args:
            ts: timestamp for the hedge fill
            net_delta: current net delta of the book (signed)
            hedge_price: where we'd execute the hedge (typically the touch
                or VWAP estimate of a marketable order)
            hedge_fee_bps: cost of hedging in bps; positive = pay (taker fees + slip)
        """
        if abs(net_delta) <= self.config.delta_hedge_threshold:
            return None

        # Trade enough to bring |net_delta| down to delta_hedge_band, on the
        # opposite side of the exposure.
        target = self.config.delta_hedge_band if net_delta > 0 else -self.config.delta_hedge_band
        size = abs(net_delta - target)
        side = Side.SELL if net_delta > 0 else Side.BUY

        return Fill(
            ts=ts,
            symbol=self.hedge_symbol,
            side=side,
            price=hedge_price,
            size=size,
            fair_value_at_fill=hedge_price,
            fee_bps=hedge_fee_bps,
            is_hedge=True,
            counterparty="hedge_venue",
        )
