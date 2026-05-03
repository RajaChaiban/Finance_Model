"""Market data fetching and calibration."""

import logging
import threading
import time
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class MarketDataCache:
    """In-memory cache for market data with TTL support.

    Thread-safe: a single ``RLock`` guards the underlying dict so concurrent
    FastAPI request handlers cannot race on get/set/del. Without this,
    ``del self._cache[key]`` from one thread can collide with another's
    ``self._cache[key]`` read and raise ``RuntimeError: dictionary changed
    size during iteration``.
    """

    def __init__(self, ttl_seconds: int = 3600):
        """Initialize cache.

        Args:
            ttl_seconds: Time-to-live for cached values (default 1 hour)
        """
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[str, tuple] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[Dict]:
        """Get cached value if not stale.

        Args:
            key: Cache key (e.g., "SPY_market_params")

        Returns:
            Cached value or None if not found or expired
        """
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            value, timestamp = entry
            if datetime.now().timestamp() - timestamp > self.ttl_seconds:
                del self._cache[key]
                return None
            return value

    def set(self, key: str, value: Dict) -> None:
        """Store value in cache with timestamp.

        Args:
            key: Cache key
            value: Value to cache
        """
        with self._lock:
            self._cache[key] = (value, datetime.now().timestamp())

    def clear(self) -> None:
        """Clear all cached values."""
        with self._lock:
            self._cache.clear()


# A 1-hour TTL on the unified bundle is too long for spot (which moves every
# second) but appropriate for dividend yield. Splitting the cache by data type
# is a deeper refactor — for now we use the shorter of the relevant horizons
# (60s) so spot stays fresh; the cost is one extra yfinance call per minute
# for the same ticker, which is well within rate limits.
_market_cache = MarketDataCache(ttl_seconds=60)


def fetch_market_params(
    ticker: str,
    auto_fetch: bool = True,
    max_retries: int = 3,
    timeout: int = 10
) -> Dict[str, Optional[float]]:
    """Fetch market parameters for a ticker from Yahoo Finance.

    Implements retry logic with exponential backoff, caching, and timeouts.
    Falls back to None values if all retries exhausted.

    Args:
        ticker: Stock ticker (e.g., "SPY", "QQQ")
        auto_fetch: If True, attempt to fetch from yfinance
        max_retries: Number of retry attempts (exponential backoff)
        timeout: Timeout in seconds for each request

    Returns:
        Dict with keys: spot_price, dividend_yield, volatility_30d, volatility_90d, source
        source: "cache", "api", or "fallback"
    """
    params = {
        "spot_price": None,
        "dividend_yield": None,
        "volatility_30d": None,
        "volatility_90d": None,
        "source": "fallback",
    }

    if not auto_fetch:
        logger.info(f"Market data fetch disabled for {ticker}")
        return params

    # Check cache first
    cache_key = f"{ticker}_market_params"
    cached_params = _market_cache.get(cache_key)
    if cached_params:
        logger.info(f"Retrieved {ticker} market data from cache (TTL not exceeded)")
        cached_params["source"] = "cache"
        return cached_params

    try:
        import yfinance as yf
    except ImportError:
        logger.warning(f"yfinance not installed. Using config values only.")
        return params

    # Retry loop with exponential backoff
    for attempt in range(max_retries):
        try:
            logger.info(f"Fetching market data for {ticker}: attempt {attempt + 1}/{max_retries}")

            # Fetch stock data with timeout. yfinance >= 0.2.x accepts
            # ``timeout=`` on .history(); pass it through so a hung DNS or
            # network issue does not lock a request handler indefinitely.
            stock = yf.Ticker(ticker)

            # Spot price (most recent close).
            # auto_adjust=True so the close is consistent with the long-history
            # call below (we don't want adjusted vs unadjusted closes to drift).
            hist = stock.history(period="1d", auto_adjust=True, timeout=timeout)
            if not hist.empty:
                params["spot_price"] = float(hist["Close"].iloc[-1])
                logger.debug(f"Fetched spot price: ${params['spot_price']:.2f}")

            # Dividend yield. The yfinance scaling for this field has flipped
            # historically across versions: 0.2.x returned percent (1.14 →
            # 1.14%), some 0.2.32+ builds return decimal (0.0114 → 1.14%).
            # Smell-test: a "yield" above 0.20 (20% — higher than any real
            # equity dividend) almost certainly means the field is already
            # in percent form and we should rescale; below 0.20 we treat as
            # decimal directly. A genuine 20% dividend is a corp-action
            # outlier worth flagging anyway.
            info = stock.info
            raw_div = info.get("dividendYield")
            if raw_div:
                raw_div_f = float(raw_div)
                if raw_div_f > 0.20:
                    # almost certainly percent-encoded — rescale
                    params["dividend_yield"] = raw_div_f / 100.0
                    logger.debug(
                        f"yfinance dividendYield {raw_div_f} interpreted as percent "
                        f"-> decimal {params['dividend_yield']:.4%}"
                    )
                else:
                    params["dividend_yield"] = raw_div_f
                    logger.debug(
                        f"yfinance dividendYield {raw_div_f} interpreted as decimal "
                        f"-> {params['dividend_yield']:.4%}"
                    )

            # Historical volatility. auto_adjust=True is critical: without it,
            # dividend ex-dates and stock splits show up as huge log-returns
            # and manufacture fake volatility. A 2:1 split day produces a
            # log(0.5) ≈ -69% return that on its own annualises to ~110%
            # spurious vol. With auto_adjust, the price series is back-
            # adjusted for both, leaving only true returns.
            hist_long = stock.history(period="6mo", auto_adjust=True, timeout=timeout)
            if len(hist_long) > 1:
                returns = np.log(hist_long["Close"] / hist_long["Close"].shift(1)).dropna()

                # The keys are named "30d / 90d" but the windows are TRADING-
                # day counts: tail(30) on a daily-business-day yfinance series
                # selects the last 30 trading days (~6 calendar weeks).
                # Annualisation uses √252 trading days/year, so the units are
                # consistent. ``.std()`` defaults to ddof=1 (sample std);
                # for σ estimation under GBM the textbook is ddof=0 with the
                # sample mean fixed at 0 — the bias for N=30 is ~1.7% and
                # within the noise of typical IV/realized-vol divergence.
                if len(returns) >= 30:
                    vol_30 = returns.tail(30).std() * np.sqrt(252)
                    params["volatility_30d"] = float(vol_30)
                    logger.debug(f"Computed 30-trading-day volatility: {params['volatility_30d']:.2%}")

                if len(returns) >= 90:
                    vol_90 = returns.tail(90).std() * np.sqrt(252)
                    params["volatility_90d"] = float(vol_90)
                    logger.debug(f"Computed 90-trading-day volatility: {params['volatility_90d']:.2%}")

            params["source"] = "api"
            logger.info(f"Successfully fetched market data for {ticker}")

            # Cache the successful result
            _market_cache.set(cache_key, {k: v for k, v in params.items() if k != "source"})

            return params

        except Exception as e:
            wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
            if attempt < max_retries - 1:
                logger.warning(
                    f"Market data fetch failed for {ticker} (attempt {attempt + 1}): {e}. "
                    f"Retrying in {wait_time}s..."
                )
                time.sleep(wait_time)
            else:
                logger.error(
                    f"Failed to fetch market data for {ticker} after {max_retries} attempts. "
                    f"Using config values as fallback."
                )

    return params


def compute_historical_vol(
    ticker: str,
    window: int = 90,
    max_retries: int = 3,
    timeout: int = 10,
) -> Optional[float]:
    """Compute historical volatility from past returns.

    Implements retry logic with exponential backoff.

    Args:
        ticker: Stock ticker
        window: Number of days of returns to use. Note: this is a
            BUSINESS-DAY count when applied to ``returns.tail(window)`` since
            yfinance returns one row per trading day. The annualisation uses
            ``√252`` (trading days/year), so ``window`` of 252 is a 1-year
            sample.
        max_retries: Number of retry attempts
        timeout: Per-request timeout in seconds (forwarded to yfinance).

    Returns:
        Annualized volatility, or None if fetch fails
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed. Cannot compute historical volatility.")
        return None

    for attempt in range(max_retries):
        try:
            logger.debug(f"Computing historical volatility for {ticker} ({window}-day window): attempt {attempt + 1}/{max_retries}")

            stock = yf.Ticker(ticker)
            # auto_adjust=True so dividend ex-dates and splits don't show up
            # as enormous log-returns that inflate the realized-vol estimate.
            hist = stock.history(period="6mo", auto_adjust=True, timeout=timeout)

            if len(hist) < window:
                logger.warning(f"Insufficient data for {ticker} ({len(hist)} days < {window} days)")
                return None

            returns = np.log(hist["Close"] / hist["Close"].shift(1)).dropna()
            vol = returns.tail(window).std() * np.sqrt(252)

            logger.debug(f"Computed {window}-day volatility for {ticker}: {vol:.2%}")
            return float(vol)

        except Exception as e:
            wait_time = 2 ** attempt
            if attempt < max_retries - 1:
                logger.warning(
                    f"Failed to compute volatility for {ticker} (attempt {attempt + 1}): {e}. "
                    f"Retrying in {wait_time}s..."
                )
                time.sleep(wait_time)
            else:
                logger.error(
                    f"Failed to compute historical volatility for {ticker} after {max_retries} attempts."
                )
                return None

    return None
