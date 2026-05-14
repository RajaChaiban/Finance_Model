"""The DataAdapter Protocol — the one contract every data source must satisfy.

The engine (quote_engine, backtest, tca, agent loop) only knows how to read
a sequence of OrderBookSnapshots. This file defines what that sequence must
look like, in protocol form, so adapters are duck-typed and don't need to
inherit from a base class.

Adapters expose two paths:

    replay(symbol, start, end)  — historical, deterministic, finite
                                  (the only path the backtester uses today)
    stream(symbol)              — live, possibly infinite
                                  (for the future live-trading mode)

Invariants every implementation MUST uphold (the OrderBookSnapshot validator
enforces sort order on construction; the rest is the adapter's contract):
    1. Snapshots emitted in NON-DECREASING `ts` order.
    2. `bids` strictly descending, `asks` strictly ascending (validator).
    3. Symbol field matches the symbol argument.
    4. No silent gaps: if data is missing, raise — don't pad with stale ticks.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Iterator, Protocol, runtime_checkable

from src.esmm.schemas import OrderBookSnapshot


@runtime_checkable
class DataAdapter(Protocol):
    """Contract every market-data source must implement.

    Adapters are NOT required to inherit from this — Python's structural
    typing means anything with these methods satisfies the protocol.
    `runtime_checkable` makes `isinstance(x, DataAdapter)` work.
    """

    name: str  # short identifier, used in logs and the API response payload

    def replay(
        self, symbol: str, start: datetime, end: datetime
    ) -> Iterable[OrderBookSnapshot]:
        """Yield every snapshot for `symbol` between `start` and `end`.

        Raises:
            ValueError: if the source has no data for the window
            ConnectionError: if the upstream call fails after retries
        """
        ...

    def stream(self, symbol: str) -> Iterator[OrderBookSnapshot]:
        """Yield snapshots indefinitely as new market data arrives.

        Implementations that don't support streaming (e.g. EOD-only sources)
        should raise NotImplementedError; the engine will only call replay().
        """
        ...
