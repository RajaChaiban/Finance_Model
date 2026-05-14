"""Data adapters that produce OrderBookSnapshot streams for the eSMM engine.

The whole engine (quote, backtest, TCA, agent loop) treats the *source* of
snapshots as opaque. Anything that satisfies the DataAdapter Protocol can be
plugged in.

Today's adapters:
    SyntheticAdapter   — wraps src.esmm.synthetic.generate_order_book_path
                         (no network, no key, deterministic — the default)
    YFinanceAdapter    — replays 1-minute Yahoo bars as synthetic L1 snapshots
                         (real prices, free, no key, ~15-min delayed)

Future:
    AlpacaAdapter      — historical NBBO quotes (free with brokerage signup)
    TradierAdapter     — options-chain ingest (free sandbox token)
    DatabentoAdapter   — true L2 / MBP-10 (paid, $125 trial credits)
    IBKRAdapter        — TWS / IB Gateway, L1+L2+options (free paper acct)
"""

from src.esmm.adapters.alpaca_adapter import AlpacaAdapter
from src.esmm.adapters.base import DataAdapter
from src.esmm.adapters.synthetic_adapter import SyntheticAdapter
from src.esmm.adapters.yfinance_adapter import YFinanceAdapter

__all__ = [
    "AlpacaAdapter",
    "DataAdapter",
    "SyntheticAdapter",
    "YFinanceAdapter",
]
