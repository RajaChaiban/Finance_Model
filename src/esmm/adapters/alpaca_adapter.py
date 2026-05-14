"""AlpacaAdapter — real historical SIP NBBO quotes via Alpaca Markets.

Alpaca's `StockHistoricalDataClient.get_stock_quotes()` returns tick-level
bid/ask/size pairs (SIP-consolidated NBBO, 15-min-delayed on the free
"Basic" plan). Each tick becomes one `OrderBookSnapshot` with a single
L1 level on each side — depth is unavailable on the free tier.

Why this one is worth a soft-dep:
  * Real bid/ask **sizes** (yfinance fabricates them).
  * Real historical timestamps with nanosecond precision.
  * Same SDK + auth drives Alpaca's paper-trading endpoint, so the *same*
    config can flip from backtest to live-paper.

Soft import: the `alpaca-py` SDK is NOT a required dependency. Importing
this adapter module is always safe; calling its methods will raise an
informative ImportError if the SDK is missing.

Credentials: read at call time from env (`ALPACA_API_KEY`, `ALPACA_SECRET_KEY`)
unless explicitly passed to the constructor. Tests pass dummy strings so they
never touch the network — the SDK itself is mocked end-to-end.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Iterable, Iterator, Optional

from src.esmm.schemas import OrderBookLevel, OrderBookSnapshot

logger = logging.getLogger(__name__)


class AlpacaAdapter:
    """Replays historical SIP NBBO quotes for a single symbol."""

    name = "alpaca"

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        feed: str = "iex",
        size_multiplier: float = 100.0,
    ):
        """
        Args:
            api_key / secret_key: Alpaca credentials. Default: read from env
                ALPACA_API_KEY / ALPACA_SECRET_KEY at first use.
            feed: "iex" (free) or "sip" (paid). Free tier delivers IEX-only.
            size_multiplier: Alpaca returns bid/ask SIZE in round-lots (1=100sh)
                for most US equities. We multiply to get shares — override to 1.0
                if your downstream model expects round-lots.
        """
        self.api_key = api_key
        self.secret_key = secret_key
        self.feed = feed
        self.size_multiplier = size_multiplier

    def _client(self):
        """Lazy SDK import + auth. Raises ImportError if alpaca-py missing."""
        try:
            from alpaca.data.historical import StockHistoricalDataClient  # type: ignore
        except ImportError as exc:  # pragma: no cover — exercised only when SDK absent
            raise ImportError(
                "AlpacaAdapter requires alpaca-py. Install with: pip install alpaca-py"
            ) from exc

        key = self.api_key or os.getenv("ALPACA_API_KEY")
        secret = self.secret_key or os.getenv("ALPACA_SECRET_KEY")
        if not key or not secret:
            raise ValueError(
                "AlpacaAdapter requires api_key+secret_key (constructor args or "
                "ALPACA_API_KEY / ALPACA_SECRET_KEY env vars)"
            )
        return StockHistoricalDataClient(api_key=key, secret_key=secret)

    def replay(
        self, symbol: str, start: datetime, end: datetime
    ) -> Iterable[OrderBookSnapshot]:
        """Yield one OrderBookSnapshot per historical NBBO tick in [start, end].

        Raises:
            ValueError: empty response or no usable quotes
            ImportError: alpaca-py not installed
        """
        from alpaca.data.requests import StockQuotesRequest  # type: ignore
        from alpaca.data.enums import DataFeed  # type: ignore

        client = self._client()
        req = StockQuotesRequest(
            symbol_or_symbols=symbol,
            start=start,
            end=end,
            feed=DataFeed.IEX if self.feed.lower() == "iex" else DataFeed.SIP,
        )
        try:
            response = client.get_stock_quotes(req)
        except Exception as exc:  # noqa: BLE001 — surface SDK errors as ValueError
            raise ValueError(f"alpaca-py replay failed: {exc}") from exc

        quotes = response.data.get(symbol) if hasattr(response, "data") else []
        if not quotes:
            raise ValueError(
                f"alpaca returned no quotes for {symbol} between {start} and {end}"
            )

        snapshots: list[OrderBookSnapshot] = []
        for q in quotes:
            snap = self._quote_to_snapshot(symbol, q)
            if snap is not None:
                snapshots.append(snap)

        if not snapshots:
            raise ValueError(
                f"alpaca returned quotes for {symbol} but none were well-formed "
                f"(non-positive prices or crossed/locked NBBO)"
            )
        return snapshots

    def stream(self, symbol: str) -> Iterator[OrderBookSnapshot]:
        """Live streaming is not implemented in v1 — would require Alpaca's
        websocket data client and asyncio plumbing. Use replay() for now."""
        raise NotImplementedError(
            "AlpacaAdapter.stream is not implemented yet. "
            "Use replay() for historical backtests."
        )

    def _quote_to_snapshot(self, symbol: str, q) -> Optional[OrderBookSnapshot]:
        """Convert one Alpaca Quote object to a validated OrderBookSnapshot."""
        bid = float(getattr(q, "bid_price", 0.0) or 0.0)
        ask = float(getattr(q, "ask_price", 0.0) or 0.0)
        bid_sz = float(getattr(q, "bid_size", 0.0) or 0.0) * self.size_multiplier
        ask_sz = float(getattr(q, "ask_size", 0.0) or 0.0) * self.size_multiplier
        ts_obj = getattr(q, "timestamp", None)
        if ts_obj is None or bid <= 0 or ask <= 0 or ask <= bid:
            # Drop ticks with no timestamp, non-positive prices, or locked/crossed
            # NBBO. These do appear in the SIP tape during halts/auctions.
            return None
        try:
            ts = ts_obj.timestamp() if hasattr(ts_obj, "timestamp") else float(ts_obj)
        except (TypeError, ValueError):
            return None
        return OrderBookSnapshot(
            ts=ts,
            symbol=symbol,
            bids=[OrderBookLevel(price=bid, size=bid_sz)],
            asks=[OrderBookLevel(price=ask, size=ask_sz)],
        )
