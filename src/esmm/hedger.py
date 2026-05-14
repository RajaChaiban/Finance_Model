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

    def evaluate_with_gamma(
        self,
        ts: float,
        net_delta: float,
        net_gamma_dollar: float,
        hedge_price: float,
        hedge_fee_bps: float = 1.0,
    ) -> list[Fill]:
        """Evaluate both delta and gamma triggers together.

        Args:
            net_gamma_dollar: gamma exposure in dollar terms — typically
                gamma * S^2 (the actual P&L impact of a 1% spot move).
                Use 0 to disable; a positive value means a 1% spot move
                in either direction adds gamma_dollar to inventory exposure.

        Returns a list of Fills (0, 1, or 2 — both triggers can fire).

        Gamma hedging in production is more complex (you'd typically buy or
        sell options, not the underlier, to hedge gamma). For the lab we
        model the simpler case: gamma above threshold triggers an additional
        delta-style trade in the same hedge instrument, sized to bring
        gamma_dollar back to the band.
        """
        fills: list[Fill] = []
        delta_fill = self.evaluate(ts=ts, net_delta=net_delta, hedge_price=hedge_price, hedge_fee_bps=hedge_fee_bps)
        if delta_fill is not None:
            fills.append(delta_fill)

        gamma_threshold = self.config.gamma_hedge_threshold
        gamma_band = self.config.gamma_hedge_band
        if gamma_threshold > 0 and abs(net_gamma_dollar) > gamma_threshold:
            target = gamma_band if net_gamma_dollar > 0 else -gamma_band
            # Convert dollar gamma surplus back to a notional trade, divided
            # by hedge_price^2 to get share count. This is a simplification:
            # a real desk would source gamma via options.
            gamma_notional_excess = abs(net_gamma_dollar - target)
            size = gamma_notional_excess / max(hedge_price, 1e-9)
            side = Side.SELL if net_gamma_dollar > 0 else Side.BUY
            fills.append(
                Fill(
                    ts=ts,
                    symbol=self.hedge_symbol,
                    side=side,
                    price=hedge_price,
                    size=size,
                    fair_value_at_fill=hedge_price,
                    fee_bps=hedge_fee_bps,
                    is_hedge=True,
                    counterparty="gamma_hedge_venue",
                )
            )
        return fills
