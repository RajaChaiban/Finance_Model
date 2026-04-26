"""
Backtesting engine: Historical validation of structured products.

Answers: "How would this structure have performed historically?"
Validates payoff logic and Greeks against real market data.
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Tuple, List, Optional
import logging
import yfinance as yf
from src.engines import quantlib_engine

logger = logging.getLogger(__name__)


class BacktestMetrics:
    """Container for backtest performance metrics."""

    def __init__(self, prices: np.ndarray, payoffs: np.ndarray, dates: List[str]):
        self.prices = prices
        self.payoffs = payoffs
        self.dates = dates
        self.pnl = payoffs - prices[0]  # P&L vs initial premium
        self.returns = self.pnl / prices[0] * 100  # Return %

        # Calculate metrics
        self.total_return = self.returns[-1]
        self.hit_rate = np.sum(self.pnl > 0) / len(self.pnl) * 100
        self.avg_pnl = np.mean(self.pnl)
        self.max_pnl = np.max(self.pnl)
        self.min_pnl = np.min(self.pnl)
        self.max_drawdown = self._calculate_max_drawdown()
        self.sharpe_ratio = self._calculate_sharpe()
        self.sortino_ratio = self._calculate_sortino()
        self.win_loss_ratio = self._calculate_win_loss_ratio()

    def _calculate_max_drawdown(self) -> float:
        """Calculate maximum drawdown from peak."""
        cummax = np.maximum.accumulate(self.pnl)
        drawdown = (self.pnl - cummax) / np.abs(cummax + 0.001)
        return float(np.min(drawdown) * 100)

    def _calculate_sharpe(self, risk_free_rate: float = 0.02) -> float:
        """Calculate Sharpe ratio (annualized)."""
        daily_returns = np.diff(self.pnl) / (np.abs(self.prices[:-1]) + 0.001)
        excess_returns = daily_returns - (risk_free_rate / 252)
        if np.std(excess_returns) == 0:
            return 0.0
        return float((np.mean(excess_returns) / np.std(excess_returns)) * np.sqrt(252))

    def _calculate_sortino(self, risk_free_rate: float = 0.02) -> float:
        """Calculate Sortino ratio (penalizes downside volatility)."""
        daily_returns = np.diff(self.pnl) / (np.abs(self.prices[:-1]) + 0.001)
        excess_returns = daily_returns - (risk_free_rate / 252)
        downside = daily_returns[daily_returns < 0]
        if len(downside) == 0 or np.std(downside) == 0:
            return 0.0
        return float((np.mean(excess_returns) / np.std(downside)) * np.sqrt(252))

    def _calculate_win_loss_ratio(self) -> float:
        """Calculate win/loss ratio."""
        wins = np.sum(self.pnl > 0)
        losses = np.sum(self.pnl < 0)
        if losses == 0:
            return float(wins) if wins > 0 else 0.0
        return float(wins / losses)

    def to_dict(self) -> Dict:
        """Convert to dictionary for reporting."""
        return {
            "total_return": self.total_return,
            "hit_rate": self.hit_rate,
            "avg_pnl": self.avg_pnl,
            "max_pnl": self.max_pnl,
            "min_pnl": self.min_pnl,
            "max_drawdown": self.max_drawdown,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "win_loss_ratio": self.win_loss_ratio,
        }

    def __repr__(self):
        return f"""
BacktestMetrics
  Total Return:    {self.total_return:+.2f}%
  Hit Rate:        {self.hit_rate:.1f}% (profitable days)
  Avg P&L:         ${self.avg_pnl:.2f}
  Max P&L:         ${self.max_pnl:.2f}
  Min P&L:         ${self.min_pnl:.2f}
  Max Drawdown:    {self.max_drawdown:.2f}%
  Sharpe Ratio:    {self.sharpe_ratio:.2f}
  Sortino Ratio:   {self.sortino_ratio:.2f}
  Win/Loss Ratio:  {self.win_loss_ratio:.2f}
"""


class BacktestEngine:
    """Historical backtesting for derivatives structures."""

    def __init__(self, ticker: str = "SPY", start_date: str = None, end_date: str = None):
        """
        Initialize backtester.

        Args:
            ticker: Underlying ticker (default SPY)
            start_date: Start date (YYYY-MM-DD). If None, uses 5 years back
            end_date: End date (YYYY-MM-DD). If None, uses today
        """
        self.ticker = ticker
        self.end_date = end_date or datetime.now().strftime("%Y-%m-%d")

        if start_date is None:
            self.start_date = (datetime.now() - timedelta(days=365*5)).strftime("%Y-%m-%d")
        else:
            self.start_date = start_date

        self.prices = None
        self.dates = None
        self._load_data()

    def _load_data(self):
        """Load historical price data."""
        logger.info(f"Loading {self.ticker} data from {self.start_date} to {self.end_date}")

        try:
            data = yf.download(self.ticker, start=self.start_date, end=self.end_date,
                             progress=False, interval='1d')
            if data.empty:
                raise ValueError(f"No data found for {self.ticker}")

            self.prices = data['Adj Close'].values
            self.dates = data.index.strftime('%Y-%m-%d').tolist()
            logger.info(f"Loaded {len(self.prices)} trading days")

        except Exception as e:
            logger.error(f"Failed to load data: {e}")
            raise

    def backtest_american_put(self, K: float, T: float, r: float = 0.045,
                              q: float = 0.015, sigma: float = None,
                              entry_date: str = None) -> Tuple[BacktestMetrics, Dict]:
        """
        Backtest an American put option.

        Args:
            K: Strike price
            T: Initial time to expiration (years)
            r: Risk-free rate
            q: Dividend yield
            sigma: Historical volatility. If None, calculates from data
            entry_date: Entry date (YYYY-MM-DD). If None, uses first date

        Returns:
            (BacktestMetrics, performance_dict) with daily P&L and prices
        """
        # Find entry point
        if entry_date is None:
            entry_idx = 0
        else:
            try:
                entry_idx = self.dates.index(entry_date)
            except ValueError:
                raise ValueError(f"Entry date {entry_date} not found in data")

        # Calculate historical volatility if not provided
        if sigma is None:
            returns = np.diff(np.log(self.prices[entry_idx:entry_idx+252]))
            sigma = np.std(returns) * np.sqrt(252)
            logger.info(f"Calculated historical volatility: {sigma:.2%}")

        entry_price = self.prices[entry_idx]

        # Price option at entry
        try:
            premium, _, _ = quantlib_engine.price_american_ql(
                S=entry_price, K=K, r=r, sigma=sigma, T=T,
                q=q, n_steps=int(T*100), option_type='put'
            )
        except Exception as e:
            logger.error(f"Failed to price option: {e}")
            raise

        logger.info(f"Entry: {self.dates[entry_idx]} | Price: ${entry_price:.2f} | Premium: ${premium:.2f}")

        # Replay: for each subsequent date, calculate intrinsic value
        prices_list = []
        payoffs_list = []
        dates_list = []
        days_remaining_list = []

        for i in range(entry_idx, len(self.prices)):
            current_price = self.prices[i]
            current_date = self.dates[i]
            days_elapsed = (i - entry_idx)
            days_remaining = max(0, int(T * 365) - days_elapsed)

            # Intrinsic value of put
            intrinsic = max(0, K - current_price)

            prices_list.append(premium)
            payoffs_list.append(intrinsic)
            dates_list.append(current_date)
            days_remaining_list.append(days_remaining)

        prices_array = np.array(prices_list)
        payoffs_array = np.array(payoffs_list)

        # Calculate metrics
        metrics = BacktestMetrics(prices_array, payoffs_array, dates_list)

        performance = {
            "dates": dates_list,
            "underlying_prices": self.prices[entry_idx:entry_idx+len(dates_list)].tolist(),
            "premium": prices_list,
            "intrinsic_value": payoffs_list,
            "pnl": metrics.pnl.tolist(),
            "returns": metrics.returns.tolist(),
            "days_remaining": days_remaining_list,
            "entry_date": self.dates[entry_idx],
            "entry_price": entry_price,
            "strike": K,
            "initial_premium": premium,
        }

        return metrics, performance

    def backtest_knockout_put(self, K: float, B: float, T: float,
                              r: float = 0.045, q: float = 0.015,
                              sigma: float = None,
                              entry_date: str = None) -> Tuple[BacktestMetrics, Dict]:
        """
        Backtest a knockout (barrier) put option.

        Args:
            K: Strike price
            B: Barrier level (down-out for puts)
            T: Initial time to expiration (years)
            r: Risk-free rate
            q: Dividend yield
            sigma: Historical volatility
            entry_date: Entry date

        Returns:
            (BacktestMetrics, performance_dict) with knockout events tracked
        """
        # Find entry point
        if entry_date is None:
            entry_idx = 0
        else:
            try:
                entry_idx = self.dates.index(entry_date)
            except ValueError:
                raise ValueError(f"Entry date {entry_date} not found in data")

        # Calculate historical volatility if not provided
        if sigma is None:
            returns = np.diff(np.log(self.prices[entry_idx:entry_idx+252]))
            sigma = np.std(returns) * np.sqrt(252)

        entry_price = self.prices[entry_idx]

        # Price option at entry
        try:
            premium, _, _ = quantlib_engine.price_knockout_ql(
                S=entry_price, K=K, B=B, r=r, sigma=sigma, T=T, q=q, option_type='put'
            )
        except Exception as e:
            logger.error(f"Failed to price knockout option: {e}")
            raise

        logger.info(f"Entry: {self.dates[entry_idx]} | Price: ${entry_price:.2f} | Premium: ${premium:.2f} | Barrier: ${B:.2f}")

        # Replay: track knockout event
        prices_list = []
        payoffs_list = []
        dates_list = []
        knockout_status = []
        knocked_out = False
        knockout_date = None

        for i in range(entry_idx, len(self.prices)):
            current_price = self.prices[i]
            current_date = self.dates[i]

            # Check knockout condition (down-out for puts: price breaches barrier)
            if current_price <= B and not knocked_out:
                knocked_out = True
                knockout_date = current_date

            # Calculate payoff
            if knocked_out:
                intrinsic = 0  # Knockout occurred, option worth zero
                ko_status = "KNOCKED OUT"
            else:
                intrinsic = max(0, K - current_price)
                ko_status = "ACTIVE"

            prices_list.append(premium)
            payoffs_list.append(intrinsic)
            dates_list.append(current_date)
            knockout_status.append(ko_status)

        prices_array = np.array(prices_list)
        payoffs_array = np.array(payoffs_list)

        metrics = BacktestMetrics(prices_array, payoffs_array, dates_list)

        performance = {
            "dates": dates_list,
            "underlying_prices": self.prices[entry_idx:entry_idx+len(dates_list)].tolist(),
            "premium": prices_list,
            "intrinsic_value": payoffs_list,
            "pnl": metrics.pnl.tolist(),
            "returns": metrics.returns.tolist(),
            "knockout_status": knockout_status,
            "knocked_out": knocked_out,
            "knockout_date": knockout_date,
            "entry_date": self.dates[entry_idx],
            "entry_price": entry_price,
            "strike": K,
            "barrier": B,
            "initial_premium": premium,
        }

        return metrics, performance
