"""Market data fetching for derivatives pricing."""

import logging
import yfinance as yf
import numpy as np
from typing import Optional, Dict, Tuple
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def get_spot_price(ticker: str) -> Optional[float]:
    """
    Fetch current spot price for a ticker from Yahoo Finance.

    Args:
        ticker: Stock ticker symbol (e.g., 'SPY', 'QQQ')

    Returns:
        Current spot price as float, or None if fetch fails
    """
    try:
        stock = yf.Ticker(ticker)
        price = stock.info.get("currentPrice")

        if price is None:
            # Fallback: try historical data
            hist = stock.history(period="1d")
            if not hist.empty:
                price = hist["Close"].iloc[-1]

        if price is not None:
            logger.info(f"Fetched spot price for {ticker}: ${price:.2f}")
            return float(price)
        else:
            logger.warning(f"Could not fetch spot price for {ticker}")
            return None

    except Exception as e:
        logger.error(f"Error fetching spot price for {ticker}: {e}")
        return None


def get_dividend_yield(ticker: str) -> Optional[float]:
    """
    Fetch dividend yield for a ticker.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dividend yield as decimal (e.g., 0.015 for 1.5%), or None if unavailable
    """
    try:
        stock = yf.Ticker(ticker)
        yield_val = stock.info.get("dividendYield")

        if yield_val is not None:
            logger.info(f"Fetched dividend yield for {ticker}: {yield_val:.4f}")
            return float(yield_val)
        else:
            logger.warning(f"Could not fetch dividend yield for {ticker}, defaulting to 0")
            return 0.0

    except Exception as e:
        logger.error(f"Error fetching dividend yield for {ticker}: {e}")
        return 0.0


def get_risk_free_rate(days_to_expiration: int) -> float:
    """
    Fetch US Treasury yield matching the option expiration.

    Args:
        days_to_expiration: Days until option expires

    Returns:
        Risk-free rate as decimal (e.g., 0.045 for 4.5%)
    """
    try:
        # Map days to Treasury instrument
        if days_to_expiration <= 90:
            ticker = "^IRX"  # 13-week T-Bill (3-month)
        elif days_to_expiration <= 180:
            ticker = "^TNX"  # 10-year Treasury (proxy for 6-month)
        elif days_to_expiration <= 365:
            ticker = "^TNX"  # 10-year Treasury
        else:
            ticker = "^TNX"  # 10-year Treasury for longer terms

        # Fetch latest Treasury yield
        treasury = yf.Ticker(ticker)
        hist = treasury.history(period="1d")

        if not hist.empty:
            rate = float(hist["Close"].iloc[-1]) / 100  # Convert percentage to decimal
            logger.info(f"Fetched risk-free rate for {days_to_expiration} days: {rate:.4f}")
            return rate
        else:
            logger.warning(f"Could not fetch Treasury yield, defaulting to 0.045")
            return 0.045

    except Exception as e:
        logger.error(f"Error fetching risk-free rate: {e}, defaulting to 0.045")
        return 0.045


def get_historical_volatility(
    ticker: str, lookback_days: int = 252
) -> Optional[float]:
    """
    Calculate historical volatility from price data.

    Args:
        ticker: Stock ticker symbol
        lookback_days: Number of days to look back (default 252 = 1 year)

    Returns:
        Annualized volatility as decimal (e.g., 0.25 for 25%)
    """
    try:
        stock = yf.Ticker(ticker)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=lookback_days + 30)

        hist = stock.history(start=start_date, end=end_date)

        if hist.empty or len(hist) < 20:
            logger.warning(f"Insufficient data for {ticker}, defaulting to 0.20")
            return 0.20

        # Calculate log returns
        prices = hist["Close"].values
        log_returns = np.diff(np.log(prices))

        # Calculate annualized volatility
        daily_volatility = np.std(log_returns)
        annual_volatility = daily_volatility * np.sqrt(252)

        logger.info(f"Calculated historical volatility for {ticker}: {annual_volatility:.4f}")
        return float(annual_volatility)

    except Exception as e:
        logger.error(f"Error calculating historical volatility for {ticker}: {e}")
        return 0.20


def get_dividend_info(ticker: str) -> Dict:
    """
    Fetch dividend information for a stock.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dict with next_dividend_date, next_dividend_amount, and ex_dividend_date
    """
    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        next_div_date = info.get("exDividendDate")
        next_div_amount = info.get("lastDividendValue", 0)

        result = {
            "next_dividend_date": (
                datetime.fromtimestamp(next_div_date).isoformat()
                if next_div_date
                else None
            ),
            "next_dividend_amount": float(next_div_amount) if next_div_amount else 0.0,
        }

        logger.info(f"Fetched dividend info for {ticker}: {result}")
        return result

    except Exception as e:
        logger.error(f"Error fetching dividend info for {ticker}: {e}")
        return {
            "next_dividend_date": None,
            "next_dividend_amount": 0.0,
        }
