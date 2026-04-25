"""Market data fetching and calibration."""

import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Optional


def fetch_market_params(ticker: str, auto_fetch: bool = True) -> Dict[str, float]:
    """Fetch market parameters for a ticker from Yahoo Finance.

    Falls back to None values if yfinance unavailable, which allows the config
    values to be used as defaults.

    Args:
        ticker: Stock ticker (e.g., "SPY", "QQQ")
        auto_fetch: If True, attempt to fetch from yfinance

    Returns:
        Dict with keys: spot_price, dividend_yield, volatility_30d, volatility_90d
    """
    params = {
        "spot_price": None,
        "dividend_yield": None,
        "volatility_30d": None,
        "volatility_90d": None,
    }

    if not auto_fetch:
        return params

    try:
        import yfinance as yf
    except ImportError:
        print(f"Warning: yfinance not installed. Using config values.")
        return params

    try:
        # Fetch stock data
        stock = yf.Ticker(ticker)

        # Spot price (most recent close)
        hist = stock.history(period="1d")
        if not hist.empty:
            params["spot_price"] = float(hist["Close"].iloc[-1])

        # Dividend yield
        info = stock.info
        if "dividendYield" in info and info["dividendYield"]:
            params["dividend_yield"] = float(info["dividendYield"])

        # Historical volatility
        hist_long = stock.history(period="3mo")
        if len(hist_long) > 1:
            returns = np.log(hist_long["Close"] / hist_long["Close"].shift(1)).dropna()

            # 30-day realized vol
            if len(returns) >= 30:
                vol_30 = returns.tail(30).std() * np.sqrt(252)
                params["volatility_30d"] = float(vol_30)

            # 90-day realized vol
            if len(returns) >= 90:
                vol_90 = returns.tail(90).std() * np.sqrt(252)
                params["volatility_90d"] = float(vol_90)

    except Exception as e:
        print(f"Warning: Failed to fetch market data for {ticker}: {e}")

    return params


def compute_historical_vol(ticker: str, window: int = 90) -> Optional[float]:
    """Compute historical volatility from past returns.

    Args:
        ticker: Stock ticker
        window: Number of days to use (default 90)

    Returns:
        Annualized volatility, or None if fetch fails
    """
    try:
        import yfinance as yf
    except ImportError:
        return None

    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="6mo")

        if len(hist) < window:
            return None

        returns = np.log(hist["Close"] / hist["Close"].shift(1)).dropna()
        vol = returns.tail(window).std() * np.sqrt(252)

        return float(vol)

    except Exception:
        return None
