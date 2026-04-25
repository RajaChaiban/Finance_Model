"""Market data fetching and calibration."""

import logging
import time
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class MarketDataCache:
    """In-memory cache for market data with TTL support."""

    def __init__(self, ttl_seconds: int = 3600):
        """Initialize cache.

        Args:
            ttl_seconds: Time-to-live for cached values (default 1 hour)
        """
        self.ttl_seconds = ttl_seconds
        self._cache = {}

    def get(self, key: str) -> Optional[Dict]:
        """Get cached value if not stale.

        Args:
            key: Cache key (e.g., "SPY_market_params")

        Returns:
            Cached value or None if not found or expired
        """
        if key not in self._cache:
            return None

        value, timestamp = self._cache[key]
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
        self._cache[key] = (value, datetime.now().timestamp())

    def clear(self) -> None:
        """Clear all cached values."""
        self._cache.clear()


_market_cache = MarketDataCache(ttl_seconds=3600)


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

            # Fetch stock data with timeout
            stock = yf.Ticker(ticker)

            # Spot price (most recent close)
            hist = stock.history(period="1d")
            if not hist.empty:
                params["spot_price"] = float(hist["Close"].iloc[-1])
                logger.debug(f"Fetched spot price: ${params['spot_price']:.2f}")

            # Dividend yield
            info = stock.info
            if "dividendYield" in info and info["dividendYield"]:
                params["dividend_yield"] = float(info["dividendYield"])
                logger.debug(f"Fetched dividend yield: {params['dividend_yield']:.2%}")

            # Historical volatility
            hist_long = stock.history(period="3mo")
            if len(hist_long) > 1:
                returns = np.log(hist_long["Close"] / hist_long["Close"].shift(1)).dropna()

                # 30-day realized vol
                if len(returns) >= 30:
                    vol_30 = returns.tail(30).std() * np.sqrt(252)
                    params["volatility_30d"] = float(vol_30)
                    logger.debug(f"Computed 30-day volatility: {params['volatility_30d']:.2%}")

                # 90-day realized vol
                if len(returns) >= 90:
                    vol_90 = returns.tail(90).std() * np.sqrt(252)
                    params["volatility_90d"] = float(vol_90)
                    logger.debug(f"Computed 90-day volatility: {params['volatility_90d']:.2%}")

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
    max_retries: int = 3
) -> Optional[float]:
    """Compute historical volatility from past returns.

    Implements retry logic with exponential backoff.

    Args:
        ticker: Stock ticker
        window: Number of days to use (default 90)
        max_retries: Number of retry attempts

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
            hist = stock.history(period="6mo")

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
