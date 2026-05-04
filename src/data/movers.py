"""Batch market-mover fetching for the Vol Desk dashboard.

Fetches a curated universe of tickers in one yfinance call, ranks them by
percent change and 30-day historical volatility, and returns an aggregated
payload for the front-end ticker strip and movers grid.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

INDEX_TICKERS: List[str] = ["SPY", "QQQ", "IWM", "DIA", "^VIX"]

DEFAULT_UNIVERSE: List[str] = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLRE", "XLB", "XLC",
    "AMD", "COIN", "PLTR", "SHOP", "NFLX",
]

_movers_cache: Dict[str, tuple] = {}
_CACHE_TTL_SECONDS = 60


def _cache_get(key: str) -> Optional[Dict]:
    if key not in _movers_cache:
        return None
    value, ts = _movers_cache[key]
    if (datetime.now(timezone.utc).timestamp() - ts) > _CACHE_TTL_SECONDS:
        return None
    return value


def _cache_set(key: str, value: Dict) -> None:
    _movers_cache[key] = (value, datetime.now(timezone.utc).timestamp())


def _safe_float(x) -> Optional[float]:
    try:
        f = float(x)
        if np.isnan(f) or np.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _summarize(ticker: str, closes: List[float]) -> Optional[Dict]:
    """Build a summary record from a ticker's close-price series."""
    if len(closes) < 2:
        return None
    last = _safe_float(closes[-1])
    prev = _safe_float(closes[-2])
    if last is None or prev is None or prev == 0:
        return None

    change_pct = (last - prev) / prev * 100.0

    hv30: Optional[float] = None
    if len(closes) >= 31:
        tail = np.asarray(closes[-31:], dtype=float)
        if np.all(tail > 0):
            log_returns = np.diff(np.log(tail))
            if log_returns.size >= 2:
                hv30 = float(log_returns.std(ddof=1) * np.sqrt(252))

    spark = [round(float(c), 4) for c in closes[-30:] if _safe_float(c) is not None]

    return {
        "ticker": ticker,
        "price": round(last, 4),
        "change_pct": round(change_pct, 4),
        "hv30": round(hv30, 4) if hv30 is not None else None,
        "spark": spark,
    }


def fetch_movers_batch(tickers: List[str]) -> Dict[str, List[float]]:
    """Fetch close-price series for a batch of tickers.

    Returns mapping ticker -> list of last ~31 daily closes (or empty list on failure).
    Single yfinance.download call to minimize round-trips.
    """
    series: Dict[str, List[float]] = {t: [] for t in tickers}

    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed; movers batch returning empty.")
        return series

    try:
        df = yf.download(
            tickers=" ".join(tickers),
            period="2mo",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        logger.error(f"yfinance batch download failed: {e}")
        return series

    if df is None or df.empty:
        return series

    for t in tickers:
        try:
            if (t,) in df.columns or t in df.columns.get_level_values(0):
                closes = df[t]["Close"].dropna().tolist()
                series[t] = closes
        except Exception as e:
            logger.debug(f"Failed to extract {t} from batch: {e}")

    return series


def get_movers_payload(universe: str = "default") -> Dict:
    """Return the indices + gainers/losers/volatile payload."""
    cache_key = f"movers:{universe}"
    cached = _cache_get(cache_key)
    if cached is not None:
        out = dict(cached)
        out["source"] = "cache"
        return out

    tickers = INDEX_TICKERS + DEFAULT_UNIVERSE
    series = fetch_movers_batch(tickers)

    summaries: List[Dict] = []
    for t, closes in series.items():
        s = _summarize(t, closes)
        if s is not None:
            summaries.append(s)

    indices = [s for s in summaries if s["ticker"] in INDEX_TICKERS]
    indices.sort(key=lambda s: INDEX_TICKERS.index(s["ticker"]))

    stocks = [s for s in summaries if s["ticker"] not in INDEX_TICKERS]
    gainers = sorted(stocks, key=lambda s: s["change_pct"], reverse=True)[:10]
    losers = sorted(stocks, key=lambda s: s["change_pct"])[:10]
    volatile = sorted(
        [s for s in stocks if s["hv30"] is not None],
        key=lambda s: s["hv30"],
        reverse=True,
    )[:10]

    payload = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "indices": indices,
        "gainers": gainers,
        "losers": losers,
        "volatile": volatile,
        "source": "api",
    }

    _cache_set(cache_key, {k: v for k, v in payload.items() if k != "source"})
    return payload
