"""YFinanceAdapter — replays historical Yahoo bars as synthetic L1 snapshots.

Yahoo doesn't expose a historical bid/ask tape, so we reconstruct L1 from
1-minute OHLC bars: mid = close, half-spread is a parameter (default 5 bps
of mid), each side gets `base_size` shares of synthetic depth.

Why this is still useful:
  * The MID path is REAL historical SPY/AAPL/etc. prices — the GBM is gone.
  * Regime detection (rv_fast, momentum, trending) sees real-world structure.
  * The MM engine is exercised against actual intraday vol clustering.

Limitations (be honest):
  * Half-spread is fabricated. The TCA spread_capture bucket is therefore
    *upper-bounded* by your half-spread choice — a real adapter (Alpaca,
    Tradier, IBKR) would surface real bid/ask sizes.
  * Yahoo bars are delayed ~15 min on the free path.
  * Streaming is a polling fallback (every `poll_seconds`); Yahoo provides
    no proper WebSocket.

API:
    YFinanceAdapter().replay("SPY", start=dt(...), end=dt(...))
        → list of OrderBookSnapshot, one per 1-min bar.

The yfinance import is deferred to method-call time so the adapter module is
import-safe even on machines that haven't installed the library (the test
suite mocks it out).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Iterable, Iterator

from src.esmm.schemas import OrderBookLevel, OrderBookSnapshot

logger = logging.getLogger(__name__)


class YFinanceAdapter:
    """Adapter over yfinance.Ticker.history (1-min bars).

    Fabricates an L1 book around each bar's close so the engine can run.
    """

    name = "yfinance"

    def __init__(
        self,
        half_spread_bps: float = 5.0,
        synthetic_size: float = 200.0,
        interval: str = "1m",
        levels: int = 1,
        tick_size: float = 0.01,
    ):
        self.half_spread_bps = half_spread_bps
        self.synthetic_size = synthetic_size
        self.interval = interval
        self.levels = levels
        self.tick_size = tick_size

    def replay(
        self, symbol: str, start: datetime, end: datetime
    ) -> Iterable[OrderBookSnapshot]:
        """Pull `interval` bars between start/end and yield one snapshot per bar."""
        import yfinance as yf  # type: ignore

        ticker = yf.Ticker(symbol)
        bars = ticker.history(
            start=start,
            end=end,
            interval=self.interval,
            auto_adjust=False,
            prepost=False,
            actions=False,
        )
        if bars.empty:
            raise ValueError(
                f"yfinance returned no {self.interval} bars for {symbol} "
                f"between {start} and {end}"
            )

        snapshots: list[OrderBookSnapshot] = []
        for ts_idx, row in bars.iterrows():
            ts_unix = ts_idx.timestamp() if hasattr(ts_idx, "timestamp") else float(ts_idx)
            close = float(row["Close"])
            if close <= 0 or close != close:  # NaN check
                continue
            snapshots.append(self._build_snapshot(symbol, ts_unix, close))

        if not snapshots:
            raise ValueError(
                f"yfinance returned bars for {symbol} but none had a usable close"
            )
        return snapshots

    def stream(
        self,
        symbol: str,
        poll_seconds: float = 60.0,
        max_snaps: int | None = None,
    ) -> Iterator[OrderBookSnapshot]:
        """Polling pseudo-stream — fetch `info` every `poll_seconds`.

        Yields real bid/ask if Yahoo's info dict carries them, otherwise
        falls back to the close-based synthetic book.

        Use `max_snaps` to bound the iteration (tests, CI).
        """
        import yfinance as yf  # type: ignore

        ticker = yf.Ticker(symbol)
        count = 0
        while True:
            try:
                info = ticker.fast_info
                bid = float(getattr(info, "bid", 0.0) or 0.0)
                ask = float(getattr(info, "ask", 0.0) or 0.0)
                last = float(getattr(info, "last_price", 0.0) or 0.0)
            except Exception as exc:  # noqa: BLE001 — Yahoo is rate-flaky
                logger.warning("yfinance live poll failed: %s", exc)
                time.sleep(poll_seconds)
                continue

            ts = time.time()
            if bid > 0 and ask > bid:
                snap = OrderBookSnapshot(
                    ts=ts,
                    symbol=symbol,
                    bids=[OrderBookLevel(price=bid, size=self.synthetic_size)],
                    asks=[OrderBookLevel(price=ask, size=self.synthetic_size)],
                )
            elif last > 0:
                snap = self._build_snapshot(symbol, ts, last)
            else:
                time.sleep(poll_seconds)
                continue

            yield snap
            count += 1
            if max_snaps is not None and count >= max_snaps:
                return
            time.sleep(poll_seconds)

    def _build_snapshot(
        self, symbol: str, ts: float, mid: float
    ) -> OrderBookSnapshot:
        """Construct an L1 (or shallow L`levels`) book around a single mid price."""
        half = mid * self.half_spread_bps * 1e-4
        # Quantise to tick_size so levels don't collide after rounding.
        half = max(half, self.tick_size)
        best_bid = round((mid - half) / self.tick_size) * self.tick_size
        best_ask = round((mid + half) / self.tick_size) * self.tick_size
        if best_ask <= best_bid:
            best_ask = best_bid + self.tick_size

        bids: list[OrderBookLevel] = []
        asks: list[OrderBookLevel] = []
        for k in range(self.levels):
            bids.append(
                OrderBookLevel(
                    price=round(best_bid - k * self.tick_size, 4),
                    size=self.synthetic_size,
                )
            )
            asks.append(
                OrderBookLevel(
                    price=round(best_ask + k * self.tick_size, 4),
                    size=self.synthetic_size,
                )
            )
        return OrderBookSnapshot(ts=ts, symbol=symbol, bids=bids, asks=asks)
