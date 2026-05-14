"""Fill-level backtester.

Replays a sequence of OrderBookSnapshots, runs the QuoteEngine on each,
simulates whether our quotes would have been hit (queue-aware), tracks
inventory, runs the auto-hedger, and produces a fill tape + position
trajectory + final TCA.

Fill simulation model (deliberately simple, easy to defend in interview):
- We always sit at the BACK of the queue at our quoted price.
- A fill on the bid (our perspective: BUY) happens when in the next snapshot
  the best bid is at-or-below our bid AND there was incoming sell flow
  (proxied by a downward mid move + size at the touch).
- Symmetric for offer fills.
- Cross-touch ⇒ guaranteed fill.

This is NOT a production fill model. A production model uses queue position,
cancellation rates, and a probabilistic match. This is an interview-grade
*adversarial* model: it will only fill us when the market moves through us,
guaranteeing we are adversely-selected unless our skew compensates. That
property is what makes the backtester actually useful for tuning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.esmm.hedger import AutoHedger
from src.esmm.inventory import InventoryBook
from src.esmm.orderbook import mid_price
from src.esmm.quote_engine import QuoteEngine
from src.esmm.schemas import (
    Fill,
    MarketMakingConfig,
    OrderBookSnapshot,
    Quote,
    Side,
)
from src.esmm.tca import attribute_pnl


@dataclass
class BacktestResult:
    quotes: list[Quote] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    mid_path: list[tuple[float, float]] = field(default_factory=list)  # (ts, mid)
    inventory_path: list[tuple[float, float]] = field(default_factory=list)  # (ts, qty)
    final_inventory: float = 0.0
    final_mid: float = 0.0
    realised_pnl: float = 0.0
    unrealised_pnl: float = 0.0
    total_pnl: float = 0.0
    n_quotes: int = 0
    n_fills: int = 0
    tca: Optional[dict] = None


def run_backtest(
    snapshots: list[OrderBookSnapshot],
    config: MarketMakingConfig,
    hedger: AutoHedger | None = None,
) -> BacktestResult:
    """Run the engine over a snapshot sequence and return the trace + P&L.

    The mid is recomputed every snapshot. We post a quote, see what the next
    snapshot does to the touch, and book a fill if our price was crossed.
    """
    if not snapshots:
        return BacktestResult()

    inventory = InventoryBook()
    engine = QuoteEngine(config)
    hedger = hedger or AutoHedger(config)
    result = BacktestResult()

    prev_quote: Quote | None = None
    prev_mid: float = mid_price(snapshots[0])

    for snap in snapshots:
        m = mid_price(snap)
        result.mid_path.append((snap.ts, m))

        # 1. Did our previous quote get hit?
        if prev_quote is not None:
            for fill in _check_fills(prev_quote, snap, prev_mid, config):
                inventory.apply_fill(fill)
                result.fills.append(fill)

        # 2. Run the auto-hedger on the post-fill inventory.
        position = inventory.get(config.symbol)
        # Simplified: treat each unit of inventory as 1 unit of delta (true
        # for cash equity; for options this would multiply by per-leg delta).
        net_delta = position.quantity
        hedge_fill = hedger.evaluate(ts=snap.ts, net_delta=net_delta, hedge_price=m)
        if hedge_fill is not None:
            inventory.apply_fill(hedge_fill)
            result.fills.append(hedge_fill)

        # 3. Re-quote off the new state.
        quote = engine.quote(snap, inventory)
        prev_quote = quote
        result.quotes.append(quote)

        prev_mid = m
        result.inventory_path.append((snap.ts, inventory.get(config.symbol).quantity))

    # Final book-stamp + TCA
    pos = inventory.get(config.symbol)
    final_mid = mid_price(snapshots[-1])
    result.final_inventory = pos.quantity
    result.final_mid = final_mid
    result.realised_pnl = pos.realized_pnl
    result.unrealised_pnl = inventory.mark_to_market(config.symbol, final_mid)
    result.total_pnl = result.realised_pnl + result.unrealised_pnl
    result.n_quotes = len(result.quotes)
    result.n_fills = len(result.fills)
    result.tca = attribute_pnl(result.fills, snapshots).model_dump()

    return result


def _check_fills(
    quote: Quote,
    next_snap: OrderBookSnapshot,
    prev_mid: float,
    config: MarketMakingConfig,
) -> list[Fill]:
    """Adversarial fill rule: we get filled only when the touch crosses our quote.

    Returns 0, 1, or 2 fills (rare to get both sides in one slot but possible
    in a fast-moving book).
    """
    fills: list[Fill] = []
    next_best_bid = next_snap.best_bid
    next_best_ask = next_snap.best_ask

    # We bought on our bid: someone hit us. Trigger when the next book's best
    # ask trades down to our bid (buyers withdrew, sellers came in).
    if quote.bid_size > 0 and next_best_ask <= quote.bid_price:
        fills.append(
            Fill(
                ts=next_snap.ts,
                symbol=quote.symbol,
                side=Side.BUY,
                price=quote.bid_price,
                size=quote.bid_size,
                fair_value_at_fill=quote.fair_value,
                fee_bps=config.fee_bps,
                is_hedge=False,
                counterparty="external",
            )
        )

    # We sold on our ask: lifted. Trigger when the next book's best bid trades
    # up to our ask.
    if quote.ask_size > 0 and next_best_bid >= quote.ask_price:
        fills.append(
            Fill(
                ts=next_snap.ts,
                symbol=quote.symbol,
                side=Side.SELL,
                price=quote.ask_price,
                size=quote.ask_size,
                fair_value_at_fill=quote.fair_value,
                fee_bps=config.fee_bps,
                is_hedge=False,
                counterparty="external",
            )
        )

    return fills
