"""TradierAdapter — Tradier sandbox / production REST API.

Tradier's sandbox returns delayed L1 quotes (real `bid`/`ask`/`bidsize`/`asksize`)
and 1-minute time-sales bars for free with a sandbox token. The production
endpoint with a funded brokerage gives real-time WebSocket streaming, but the
free sandbox is plenty for a backtester.

Endpoints used:
  GET /v1/markets/timesales?symbol&interval=1min&start&end  — historical bars
  GET /v1/markets/quotes?symbols                            — current quote

Free credentials: sign up at https://developer.tradier.com → get a sandbox
Bearer token. No funding required.

Quote shape per Tradier API:
    { "quotes": { "quote": {
        "bid": 499.99, "ask": 500.01,
        "bidsize": 5, "asksize": 8,
        "last": 500.00, ...
    }}}

Replay strategy: like YFinanceAdapter, Tradier's `timesales` returns OHLC bars,
not a historical bid/ask tape. We fabricate L1 around each bar's close. The
LIVE path (stream) uses real bid/ask from `/markets/quotes`.

Auth: read `TRADIER_TOKEN` from env if not passed; `TRADIER_BASE_URL` defaults
to the sandbox.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Iterable, Iterator, Optional

import httpx

from src.esmm.schemas import OrderBookLevel, OrderBookSnapshot

logger = logging.getLogger(__name__)

SANDBOX_BASE = "https://sandbox.tradier.com/v1"
PRODUCTION_BASE = "https://api.tradier.com/v1"


class TradierAdapter:
    """Adapter over Tradier's REST API (sandbox by default)."""

    name = "tradier"

    def __init__(
        self,
        token: Optional[str] = None,
        base_url: Optional[str] = None,
        half_spread_bps: float = 5.0,
        synthetic_size: float = 200.0,
        size_multiplier: float = 100.0,
        timeout: float = 10.0,
    ):
        """
        Args:
            token: Tradier Bearer token. Defaults to env TRADIER_TOKEN.
            base_url: API base. Defaults to env TRADIER_BASE_URL, then sandbox.
            half_spread_bps: fabricated half-spread used by `replay()` (which
                pulls bar OHLC, not a real bid/ask tape).
            synthetic_size: fabricated size on each side for `replay()`.
            size_multiplier: applied to Tradier-reported bidsize/asksize on
                live quotes (Tradier reports round-lots; 1 lot = 100 shares).
            timeout: HTTP timeout in seconds.
        """
        self.token = token
        self.base_url = base_url
        self.half_spread_bps = half_spread_bps
        self.synthetic_size = synthetic_size
        self.size_multiplier = size_multiplier
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Public Protocol API
    # ------------------------------------------------------------------

    def replay(
        self, symbol: str, start: datetime, end: datetime
    ) -> Iterable[OrderBookSnapshot]:
        """Replay 1-min bars between start and end, fabricating L1 around close.

        Raises ValueError on empty payload.
        """
        params = {
            "symbol": symbol,
            "interval": "1min",
            "start": start.strftime("%Y-%m-%d %H:%M"),
            "end": end.strftime("%Y-%m-%d %H:%M"),
            "session_filter": "open",
        }
        payload = self._get("/markets/timesales", params=params)
        series = (payload.get("series") or {}).get("data")
        if not series:
            raise ValueError(
                f"tradier returned no timesales for {symbol} between {start} and {end}"
            )
        # `data` is a list when multiple bars; a single dict when one bar.
        if isinstance(series, dict):
            series = [series]

        snapshots: list[OrderBookSnapshot] = []
        for bar in series:
            close = bar.get("close")
            ts_str = bar.get("time")
            if close is None or ts_str is None:
                continue
            try:
                close_f = float(close)
            except (TypeError, ValueError):
                continue
            if close_f <= 0:
                continue
            ts = self._parse_ts(ts_str)
            snap = self._build_synthetic_l1(symbol, ts, close_f)
            if snap is not None:
                snapshots.append(snap)

        if not snapshots:
            raise ValueError(
                f"tradier returned bars for {symbol} but none were well-formed"
            )
        return snapshots

    def stream(
        self,
        symbol: str,
        poll_seconds: float = 1.0,
        max_snaps: Optional[int] = None,
    ) -> Iterator[OrderBookSnapshot]:
        """Polling pseudo-stream — hit `/markets/quotes` every `poll_seconds`."""
        count = 0
        while True:
            quote = self._fetch_quote(symbol)
            if quote is not None:
                yield quote
                count += 1
                if max_snaps is not None and count >= max_snaps:
                    return
            time.sleep(poll_seconds)

    def quote(self, symbol: str) -> OrderBookSnapshot:
        """One-shot quote (public helper for callers that don't want a generator)."""
        q = self._fetch_quote(symbol)
        if q is None:
            raise ValueError(f"tradier returned no usable quote for {symbol}")
        return q

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_auth(self) -> tuple[str, str]:
        token = self.token or os.getenv("TRADIER_TOKEN")
        base = self.base_url or os.getenv("TRADIER_BASE_URL", SANDBOX_BASE)
        if not token:
            raise ValueError(
                "TradierAdapter requires token (constructor arg or TRADIER_TOKEN env)"
            )
        return token, base

    def _get(self, path: str, params: dict) -> dict:
        token, base = self._resolve_auth()
        url = base + path
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        try:
            resp = httpx.get(url, params=params, headers=headers, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise ValueError(f"tradier request failed: {exc}") from exc
        if resp.status_code != 200:
            raise ValueError(
                f"tradier returned {resp.status_code}: {resp.text[:200]}"
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise ValueError(f"tradier response not JSON: {resp.text[:200]}") from exc

    def _fetch_quote(self, symbol: str) -> Optional[OrderBookSnapshot]:
        payload = self._get("/markets/quotes", params={"symbols": symbol})
        quotes = payload.get("quotes")
        if not quotes:
            return None
        q = quotes.get("quote") if isinstance(quotes, dict) else None
        if isinstance(q, list):
            q = q[0] if q else None
        if not q:
            return None
        try:
            bid = float(q.get("bid", 0.0) or 0.0)
            ask = float(q.get("ask", 0.0) or 0.0)
            bid_sz = float(q.get("bidsize", 0.0) or 0.0) * self.size_multiplier
            ask_sz = float(q.get("asksize", 0.0) or 0.0) * self.size_multiplier
        except (TypeError, ValueError):
            return None
        if bid <= 0 or ask <= 0 or ask <= bid:
            return None
        if bid_sz <= 0:
            bid_sz = self.synthetic_size
        if ask_sz <= 0:
            ask_sz = self.synthetic_size
        return OrderBookSnapshot(
            ts=time.time(),
            symbol=symbol,
            bids=[OrderBookLevel(price=bid, size=bid_sz)],
            asks=[OrderBookLevel(price=ask, size=ask_sz)],
        )

    def _build_synthetic_l1(
        self, symbol: str, ts: float, mid: float
    ) -> Optional[OrderBookSnapshot]:
        """Fabricate an L1 book around a single close price (replay only)."""
        tick_size = 0.01
        half = max(mid * self.half_spread_bps * 1e-4, tick_size)
        bid = round((mid - half) / tick_size) * tick_size
        ask = round((mid + half) / tick_size) * tick_size
        if ask <= bid:
            ask = bid + tick_size
        return OrderBookSnapshot(
            ts=ts,
            symbol=symbol,
            bids=[OrderBookLevel(price=bid, size=self.synthetic_size)],
            asks=[OrderBookLevel(price=ask, size=self.synthetic_size)],
        )

    @staticmethod
    def _parse_ts(ts_str: str) -> float:
        """Tradier timesales returns ISO-ish strings like '2026-05-14T14:30:00'.

        Returns Unix epoch seconds.
        """
        # Several Tradier formats observed in the wild; try the common ones.
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M"):
            try:
                return datetime.strptime(ts_str, fmt).timestamp()
            except ValueError:
                continue
        # Last-ditch: numeric epoch (Tradier sometimes returns ms)
        try:
            v = float(ts_str)
            return v / 1000.0 if v > 1e12 else v
        except (TypeError, ValueError):
            raise ValueError(f"unparseable Tradier timestamp: {ts_str!r}")
