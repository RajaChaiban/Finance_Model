"""Market data fetching for derivatives pricing."""

import logging
import yfinance as yf
import numpy as np
import pandas as pd
from typing import Optional, Dict, Tuple, List
from datetime import datetime, timedelta, date

from src.data.rate_conventions import (
    QuoteBasis,
    to_continuous_act365,
    treasury_basis_for_tenor_days,
)

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


def normalise_dividend_yield(yield_val: Optional[float]) -> float:
    """Normalise a Yahoo-style dividend yield to a unitless decimal.

    Yahoo's `dividendYield` field has historically returned percent on some
    endpoints (e.g. 1.5 meaning 1.5%) and decimal on others (e.g. 0.015 for
    the same stock). Silently feeding this into a Black-Scholes engine causes
    100x mispricing in the dividend term. This helper detects the scale and
    returns the canonical decimal form.

    Heuristic boundaries:
      - None → 0.0 (no-dividend stock)
      - val ≥ 0.20 → almost certainly percent (a 20%+ true yield is exceptional)
      - val < 0.20 → already decimal (real-world equities), leave alone

    Args:
        yield_val: Raw value from data source. None / negative / absurdly large
            inputs are handled defensively.

    Returns:
        Dividend yield as decimal (e.g. 0.015 for 1.5%).

    Raises:
        ValueError: For negative inputs or values that are ambiguously large
            after attempted normalisation (likely upstream data error).
    """
    if yield_val is None:
        return 0.0
    val = float(yield_val)
    if val < 0:
        raise ValueError(f"Dividend yield cannot be negative: got {val}")
    # Absurd-high guard: even interpreted as percent, ≥50% yield is a data
    # error, not a real yield. Catch upstream feed corruption.
    if val >= 50.0:
        raise ValueError(
            f"Dividend yield {val} too large to plausibly represent a yield "
            f"(≥50% even if treated as percent)."
        )
    if val >= 0.20:
        # Treat as percent. Log a warning at INFO so structuring desks can
        # spot upstream-feed scale flips.
        logger.info(
            f"Dividend yield {val} interpreted as percent → {val/100:.4f} decimal"
        )
        return val / 100.0
    return val


def get_dividend_yield(ticker: str) -> Optional[float]:
    """
    Fetch dividend yield for a ticker.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dividend yield as decimal (e.g., 0.015 for 1.5%), or 0.0 if unavailable.
        Output is normalised via :func:`normalise_dividend_yield` so callers
        always receive decimal regardless of upstream Yahoo-feed quirks.
    """
    try:
        stock = yf.Ticker(ticker)
        yield_val = stock.info.get("dividendYield")
        normalised = normalise_dividend_yield(yield_val)
        if yield_val is None:
            logger.warning(f"Could not fetch dividend yield for {ticker}, defaulting to 0")
        else:
            logger.info(f"Fetched dividend yield for {ticker}: {normalised:.4f} (raw={yield_val})")
        return normalised
    except Exception as e:
        logger.error(f"Error fetching dividend yield for {ticker}: {e}")
        return 0.0


def get_risk_free_rate(days_to_expiration: int) -> float:
    """
    Fetch US Treasury yield matching the option expiration and convert to the
    convention the pricing engine expects (continuously compounded, Actual/365).

    Yahoo's Treasury tickers quote yields in their native conventions:
      - ^IRX (13-week T-bill): money-market yield, Act/360 simple
      - ^TNX (10Y) / ^TYX (30Y): bond-equivalent yield (BEY), semi-annual

    Both are converted via rate_conventions.to_continuous_act365 so the value
    returned here is directly usable in Black-Scholes / binomial / MC engines.

    Args:
        days_to_expiration: Days until option expires

    Returns:
        Continuously compounded risk-free rate on Act/365 basis
    """
    try:
        # Map days to the closest-matching Treasury instrument
        if days_to_expiration <= 365:
            ticker = "^IRX"   # 13-week T-bill — best short-rate proxy Yahoo exposes
        else:
            ticker = "^TNX"   # 10Y note — BEY-quoted

        basis = treasury_basis_for_tenor_days(days_to_expiration)

        treasury = yf.Ticker(ticker)
        hist = treasury.history(period="1d")

        if hist.empty:
            logger.warning("Could not fetch Treasury yield, defaulting to 0.045 (continuous)")
            return 0.045

        raw_pct = float(hist["Close"].iloc[-1])           # Yahoo gives whole-percent, e.g. 5.30
        quoted_decimal = raw_pct / 100.0                  # → 0.0530 in its native basis
        r_continuous = to_continuous_act365(quoted_decimal, basis)

        logger.info(
            f"Fetched {ticker} yield {quoted_decimal:.4%} ({basis.value}) "
            f"→ continuous Act/365 {r_continuous:.4%} for {days_to_expiration}d tenor"
        )
        return r_continuous

    except Exception as e:
        logger.error(f"Error fetching risk-free rate: {e}, defaulting to 0.045 (continuous)")
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


def fetch_option_chain(
    ticker: str,
    max_expiries: int = 6,
    min_dte: int = 5,
    max_moneyness: float = 0.25,
    today: Optional[date] = None,
) -> Dict[date, pd.DataFrame]:
    """Fetch and clean a listed-option chain from Yahoo Finance.

    Pulls the first ``max_expiries`` listed expiries past ``min_dte`` days,
    keeps both calls and puts, and returns one DataFrame per expiry. Each
    row carries: ``strike``, ``mid``, ``bid``, ``ask``, ``option_type``
    (``'call'`` / ``'put'``), ``dte_days``, ``moneyness`` (= K/S - 1).

    Filters applied:
      - ``bid > 0`` (drop quotes that aren't actually live)
      - ``ask >= bid`` (drop crossed quotes from stale prints)
      - ``|moneyness| <= max_moneyness`` (drop the illiquid wings — far OTM
        won't invert cleanly and adds noise to the surface fit)
      - ``dte_days >= min_dte`` (drop the front-week which is dominated by
        gamma scalpers and isn't a clean smile signal)

    Args:
        ticker: Equity ticker (the project uses ``"SPY"``).
        max_expiries: Cap on number of expiries pulled. Yahoo offers ~30
            but the smile is well-determined by ~6 across the term.
        min_dte: Minimum days-to-expiry. Default 5 drops the front-week.
        max_moneyness: Strike filter, expressed as ``|K/S - 1|``.
        today: Reference date for DTE computation (None → today).
            Lets tests pin a deterministic reference date.

    Returns:
        Dict keyed by ``date`` (expiry), value = cleaned DataFrame.
        Returned dict is sorted by expiry ascending and contains only
        expiries with at least one quote surviving the filters.

    Notes:
        Designed to run inside the same retry/cache wrapper as the rest
        of the fetchers (``src/data/market_data.py``). On failure to
        contact Yahoo, returns an empty dict — callers must check.
    """
    today_date = today if today is not None else date.today()

    try:
        stock = yf.Ticker(ticker)
        spot = get_spot_price(ticker)
        if spot is None or spot <= 0:
            logger.warning(f"Cannot build option chain for {ticker}: no spot")
            return {}

        all_expiries = list(stock.options or [])
        if not all_expiries:
            logger.warning(f"No listed expiries returned for {ticker}")
            return {}
    except Exception as exc:
        logger.error(f"Failed to fetch option chain expiries for {ticker}: {exc}")
        return {}

    chain: Dict[date, pd.DataFrame] = {}
    for expiry_str in all_expiries:
        if len(chain) >= max_expiries:
            break
        try:
            expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
        except ValueError:
            logger.warning(f"Skipping malformed expiry '{expiry_str}'")
            continue

        dte = (expiry_date - today_date).days
        if dte < min_dte:
            continue

        try:
            opts = stock.option_chain(expiry_str)
        except Exception as exc:
            logger.warning(f"Failed to fetch chain for {ticker} {expiry_str}: {exc}")
            continue

        rows: List[pd.DataFrame] = []
        for side_df, side_name in ((opts.calls, "call"), (opts.puts, "put")):
            if side_df is None or len(side_df) == 0:
                continue
            df = side_df.loc[:, ["strike", "bid", "ask"]].copy()
            df["option_type"] = side_name
            rows.append(df)
        if not rows:
            continue

        merged = pd.concat(rows, ignore_index=True)
        merged = merged.dropna(subset=["strike", "bid", "ask"])
        merged = merged[(merged["bid"] > 0) & (merged["ask"] >= merged["bid"])]
        merged["mid"] = 0.5 * (merged["bid"] + merged["ask"])
        merged["dte_days"] = dte
        merged["moneyness"] = merged["strike"] / spot - 1.0
        merged = merged[merged["moneyness"].abs() <= max_moneyness]

        if len(merged) == 0:
            continue

        merged = merged.sort_values(["option_type", "strike"]).reset_index(drop=True)
        chain[expiry_date] = merged.loc[
            :, ["strike", "bid", "ask", "mid", "option_type", "dte_days", "moneyness"]
        ]

    if not chain:
        logger.warning(f"Option chain for {ticker} empty after filtering")
        return {}

    return dict(sorted(chain.items()))


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
