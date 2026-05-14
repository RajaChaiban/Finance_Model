"""IBKRAdapter — Interactive Brokers via TWS / IB Gateway and `ib_insync`.

What this gives you on the free paper-trading account:
  * Historical bid/ask bars at 1-second granularity for liquid US equities
    (via `reqHistoricalData` with whatToShow="BID" / "ASK")
  * Live quote subscriptions with REAL bid/ask sizes
  * Optionally L2 depth via `reqMktDepth` (subscription gated by exchange,
    but free for a wide list when commissions exceed $30/mo)

Why this adapter is the highest-fidelity free source:
  * Real historical BID/ASK bars — yfinance fabricates them, Tradier sandbox
    has no historical bid/ask tape, Alpaca free is 15-min delayed.
  * Same SDK can flip from `replay()` to live paper-trading orders.

Tradeoffs (be honest):
  * Requires TWS or IB Gateway running locally on port 7497 (paper) / 7496 (live).
  * Streaming requires asyncio; v1 implements replay only.
  * 30 min/symbol historical-bar limit per ~10 minutes (IBKR pacing rules).

Soft import: `ib_insync` is NOT a required dependency. Tests mock the entire
SDK so the suite runs without TWS or the library installed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Iterable, Iterator, Optional

from src.esmm.schemas import OrderBookLevel, OrderBookSnapshot

logger = logging.getLogger(__name__)


class IBKRAdapter:
    """Adapter over `ib_insync` against TWS or IB Gateway."""

    name = "ibkr"

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7497,  # 7497 = paper, 7496 = live
        client_id: int = 17,
        exchange: str = "SMART",
        currency: str = "USD",
        bar_size: str = "1 min",
        synthetic_size: float = 100.0,
        size_multiplier: float = 100.0,
        timeout: float = 15.0,
    ):
        """
        Args:
            host / port / client_id: TWS / IB Gateway connection.
            exchange: routing destination (SMART = SmartRouting).
            bar_size: ib_insync bar-size string ("1 secs", "1 min", "5 mins", ...).
            synthetic_size: fallback when IBKR doesn't surface bar volume.
            size_multiplier: applied to live quote bid/ask sizes (round-lots).
            timeout: HTTP/connect timeout in seconds.
        """
        self.host = host
        self.port = port
        self.client_id = client_id
        self.exchange = exchange
        self.currency = currency
        self.bar_size = bar_size
        self.synthetic_size = synthetic_size
        self.size_multiplier = size_multiplier
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Public Protocol API
    # ------------------------------------------------------------------

    def replay(
        self, symbol: str, start: datetime, end: datetime
    ) -> Iterable[OrderBookSnapshot]:
        """Replay BID/ASK historical bars between start/end, one snapshot per bar.

        Requests two historical-bar series (BID and ASK) and pairs them by
        timestamp. Bars that exist on only one side are dropped.
        """
        ib = self._connect()
        try:
            contract = self._build_contract(ib, symbol)
            duration = self._duration_str(start, end)
            end_str = end.strftime("%Y%m%d %H:%M:%S")

            bid_bars = ib.reqHistoricalData(
                contract,
                endDateTime=end_str,
                durationStr=duration,
                barSizeSetting=self.bar_size,
                whatToShow="BID",
                useRTH=False,
                formatDate=1,
            )
            ask_bars = ib.reqHistoricalData(
                contract,
                endDateTime=end_str,
                durationStr=duration,
                barSizeSetting=self.bar_size,
                whatToShow="ASK",
                useRTH=False,
                formatDate=1,
            )
            if not bid_bars or not ask_bars:
                raise ValueError(
                    f"IBKR returned no historical bars for {symbol} between "
                    f"{start} and {end}"
                )
            snaps = self._pair_bars_to_snapshots(symbol, bid_bars, ask_bars)
            if not snaps:
                raise ValueError(
                    f"IBKR returned bars for {symbol} but no bid/ask pair aligned"
                )
            return snaps
        finally:
            try:
                ib.disconnect()
            except Exception:  # noqa: BLE001 — never let teardown swallow data
                pass

    def stream(self, symbol: str) -> Iterator[OrderBookSnapshot]:
        """Live streaming requires an asyncio loop + IBKR's tick callbacks.

        Deferred to v2 — the paper-trading replay() path covers the immediate
        backtester use case.
        """
        raise NotImplementedError(
            "IBKRAdapter.stream is not implemented in v1. "
            "Use replay() for historical paper-trading backtests."
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _connect(self):
        """Lazy SDK import + connect. Raises ImportError if ib_insync missing."""
        try:
            from ib_insync import IB  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "IBKRAdapter requires ib_insync. Install with: pip install ib_insync"
            ) from exc
        ib = IB()
        try:
            ib.connect(
                host=self.host,
                port=self.port,
                clientId=self.client_id,
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001 — surface connect errors
            raise ValueError(
                f"IBKRAdapter failed to connect to {self.host}:{self.port}: {exc}"
            ) from exc
        return ib

    def _build_contract(self, ib, symbol: str):
        """Build and qualify a Stock contract for `symbol`."""
        from ib_insync import Stock  # type: ignore

        contract = Stock(symbol, self.exchange, self.currency)
        try:
            ib.qualifyContracts(contract)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"IBKR could not qualify contract for {symbol}: {exc}"
            ) from exc
        return contract

    @staticmethod
    def _duration_str(start: datetime, end: datetime) -> str:
        """Translate a [start, end] window into IBKR's duration grammar.

        IBKR units: 'S' seconds, 'D' days, 'W' weeks, 'M' months, 'Y' years.
        Pacing rules cap individual requests, so callers should keep windows
        modest (intraday is fine; multi-day requests should be chunked).
        """
        seconds = max(1, int((end - start).total_seconds()))
        if seconds < 86_400:
            return f"{seconds} S"
        days = (seconds + 86_399) // 86_400
        return f"{days} D"

    def _pair_bars_to_snapshots(
        self, symbol: str, bid_bars, ask_bars
    ) -> list[OrderBookSnapshot]:
        """Align BID and ASK bars by timestamp and emit a snapshot per pair."""
        ask_by_date = {self._bar_ts(b): b for b in ask_bars if self._bar_ts(b) is not None}

        snaps: list[OrderBookSnapshot] = []
        for bid_bar in bid_bars:
            ts = self._bar_ts(bid_bar)
            if ts is None:
                continue
            ask_bar = ask_by_date.get(ts)
            if ask_bar is None:
                continue
            bid_price = float(getattr(bid_bar, "close", 0.0) or 0.0)
            ask_price = float(getattr(ask_bar, "close", 0.0) or 0.0)
            if bid_price <= 0 or ask_price <= 0 or ask_price <= bid_price:
                continue
            bid_size = float(getattr(bid_bar, "volume", 0.0) or 0.0)
            ask_size = float(getattr(ask_bar, "volume", 0.0) or 0.0)
            if bid_size <= 0:
                bid_size = self.synthetic_size
            if ask_size <= 0:
                ask_size = self.synthetic_size
            snaps.append(
                OrderBookSnapshot(
                    ts=ts,
                    symbol=symbol,
                    bids=[OrderBookLevel(price=bid_price, size=bid_size)],
                    asks=[OrderBookLevel(price=ask_price, size=ask_size)],
                )
            )
        return snaps

    @staticmethod
    def _bar_ts(bar) -> Optional[float]:
        """Extract a Unix-epoch float from an ib_insync BarData.date attribute."""
        d = getattr(bar, "date", None)
        if d is None:
            return None
        if hasattr(d, "timestamp"):
            try:
                return d.timestamp()
            except (TypeError, ValueError):
                return None
        if isinstance(d, (int, float)):
            return float(d)
        if isinstance(d, str):
            for fmt in (
                "%Y%m%d %H:%M:%S",
                "%Y%m%d  %H:%M:%S",  # IBKR sometimes double-spaces
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S",
                "%Y%m%d",
            ):
                try:
                    return datetime.strptime(d, fmt).timestamp()
                except ValueError:
                    continue
        return None
