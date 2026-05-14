"""eSMM — Listed-options market-making research platform with central-risk-book simulation.

Layered on top of the existing ArgoPilot pricing engines:
- src/engines/* still computes fair value (Black-Scholes / vol surface / QuantLib).
- src/esmm/* builds the market-making layer: order books, quote engine, inventory,
  auto-hedger, central risk book, fill-level backtester, TCA.

Entry points used in tests and the FastAPI router:
- schemas.OrderBookSnapshot / Quote / Fill / Position / MarketMakingConfig
- orderbook.mid_price / micro_price / order_book_imbalance / spread_bps
- inventory.InventoryBook
- quote_engine.QuoteEngine
- hedger.AutoHedger
- crb.CentralRiskBook
- backtest.run_backtest
- tca.attribute_pnl
- synthetic.generate_order_book_path
"""

from src.esmm import (
    backtest,
    crb,
    features,
    hedger,
    inventory,
    orderbook,
    persistence,
    quote_engine,
    schemas,
    synthetic,
    tca,
)

__all__ = [
    "backtest",
    "crb",
    "features",
    "hedger",
    "inventory",
    "orderbook",
    "persistence",
    "quote_engine",
    "schemas",
    "synthetic",
    "tca",
]
