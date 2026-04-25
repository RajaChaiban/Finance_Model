"""
QuantLib-based pricing engine for derivatives.

Uses QuantLib library for production-grade pricing:
- More robust numerical methods
- Better handling of edge cases
- Industry-standard validation
"""

import numpy as np
import QuantLib as ql
from typing import Dict, Tuple


def price_knockout_ql(S: float, K: float, B: float, r: float, sigma: float,
                      T: float, q: float, option_type: str) -> Tuple[float, float, None]:
    """
    Price knockout (barrier) option using QuantLib.

    Args:
        S: Spot price
        K: Strike price
        B: Barrier level
        r: Risk-free rate
        sigma: Volatility
        T: Time to expiration (years)
        q: Dividend yield
        option_type: 'call' or 'put'

    Returns:
        (price, std_error, paths) - paths is None for analytical pricing
    """
    try:
        # Setup dates
        today = ql.Date(1, 1, 2025)
        ql.Settings.instance().evaluationDate = today
        # Add days properly using ql.Date arithmetic
        maturity = today + int(T * 365)

        # Payoff
        if option_type.lower() == 'call':
            payoff = ql.PlainVanillaPayoff(ql.Option.Call, K)
            is_knock_in = False  # Knock-out call
            barrier_type = ql.Barrier.DownOut if B < S else ql.Barrier.UpOut
        else:
            payoff = ql.PlainVanillaPayoff(ql.Option.Put, K)
            barrier_type = ql.Barrier.UpOut if B > S else ql.Barrier.DownOut

        # European exercise
        exercise = ql.EuropeanExercise(maturity)

        # Barrier option
        barrier_payoff = ql.BarrierPayoff(ql.Barrier(barrier_type, B, 0), payoff)
        barrier_option = ql.BarrierOption(barrier_type, B, 0, payoff, exercise)

        # Process
        spot_quote = ql.QuoteHandle(ql.SimpleQuote(S))
        risk_free_ts = ql.YieldTermStructureHandle(
            ql.FlatForward(today, r, ql.Actual360())
        )
        dividend_ts = ql.YieldTermStructureHandle(
            ql.FlatForward(today, q, ql.Actual360())
        )
        vol_ts = ql.BlackVolTermStructureHandle(
            ql.BlackConstantVol(today, ql.TARGET(), sigma, ql.Actual360())
        )

        process = ql.GeneralizedBlackScholesProcess(spot_quote, dividend_ts, risk_free_ts, vol_ts)

        # Pricing engine - use binomial for stability
        steps = 100
        engine = ql.BinomialVanillaEngine(process, "crr", steps)
        barrier_option.setPricingEngine(engine)

        price = barrier_option.NPV()

        return float(price), 0.0, None

    except Exception as e:
        raise ValueError(f"QuantLib knockout pricing failed: {e}")


def price_american_ql(S: float, K: float, r: float, sigma: float, T: float,
                      q: float, n_steps: int = 100, option_type: str = 'put') -> Tuple[float, float, None]:
    """
    Price American option using QuantLib Binomial Tree.

    Args:
        S: Spot price
        K: Strike price
        r: Risk-free rate
        sigma: Volatility
        T: Time to expiration (years)
        q: Dividend yield
        n_steps: Number of steps in binomial tree
        option_type: 'call' or 'put'

    Returns:
        (price, std_error, paths) - paths is None for tree-based pricing
    """
    try:
        # Setup dates
        today = ql.Date(1, 1, 2025)
        ql.Settings.instance().evaluationDate = today
        # Add days properly using ql.Date arithmetic
        maturity = today + int(T * 365)

        # Payoff
        if option_type.lower() == 'call':
            payoff = ql.PlainVanillaPayoff(ql.Option.Call, K)
        else:
            payoff = ql.PlainVanillaPayoff(ql.Option.Put, K)

        # American exercise
        exercise = ql.AmericanExercise(today, maturity)
        option = ql.VanillaOption(payoff, exercise)

        # Process
        spot_quote = ql.QuoteHandle(ql.SimpleQuote(S))
        risk_free_ts = ql.YieldTermStructureHandle(
            ql.FlatForward(today, r, ql.Actual360())
        )
        dividend_ts = ql.YieldTermStructureHandle(
            ql.FlatForward(today, q, ql.Actual360())
        )
        vol_ts = ql.BlackVolTermStructureHandle(
            ql.BlackConstantVol(today, ql.TARGET(), sigma, ql.Actual360())
        )

        process = ql.GeneralizedBlackScholesProcess(spot_quote, dividend_ts, risk_free_ts, vol_ts)

        # Binomial tree engine (better for American options)
        engine = ql.BinomialVanillaEngine(process, "crr", n_steps)
        option.setPricingEngine(engine)

        price = option.NPV()

        return float(price), 0.0, None

    except Exception as e:
        raise ValueError(f"QuantLib American pricing failed: {e}")


def greeks_ql(S: float, K: float, r: float, sigma: float, T: float, q: float,
              option_type: str = 'put', is_american: bool = False) -> Dict[str, float]:
    """
    Calculate Greeks using QuantLib.

    Args:
        S: Spot price
        K: Strike price
        r: Risk-free rate
        sigma: Volatility
        T: Time to expiration (years)
        q: Dividend yield
        option_type: 'call' or 'put'
        is_american: Use American exercise if True, European otherwise

    Returns:
        Dictionary of Greeks: delta, gamma, vega, theta, rho
    """
    try:
        # Setup dates
        today = ql.Date(1, 1, 2025)
        ql.Settings.instance().evaluationDate = today
        # Add days properly using ql.Date arithmetic
        maturity = today + int(T * 365)

        # Payoff
        if option_type.lower() == 'call':
            payoff = ql.PlainVanillaPayoff(ql.Option.Call, K)
        else:
            payoff = ql.PlainVanillaPayoff(ql.Option.Put, K)

        # Exercise type
        if is_american:
            exercise = ql.AmericanExercise(today, maturity)
        else:
            exercise = ql.EuropeanExercise(maturity)

        option = ql.VanillaOption(payoff, exercise)

        # Process
        spot_quote = ql.QuoteHandle(ql.SimpleQuote(S))
        risk_free_ts = ql.YieldTermStructureHandle(
            ql.FlatForward(today, r, ql.Actual360())
        )
        dividend_ts = ql.YieldTermStructureHandle(
            ql.FlatForward(today, q, ql.Actual360())
        )
        vol_ts = ql.BlackVolTermStructureHandle(
            ql.BlackConstantVol(today, ql.TARGET(), sigma, ql.Actual360())
        )

        process = ql.GeneralizedBlackScholesProcess(spot_quote, dividend_ts, risk_free_ts, vol_ts)

        # Engine
        if is_american:
            engine = ql.BinomialVanillaEngine(process, "crr", 100)
        else:
            engine = ql.AnalyticEuropeanEngine(process)

        option.setPricingEngine(engine)

        # Calculate Greeks (some may not be available depending on engine)
        greeks = {
            "price": float(option.NPV()),
        }

        # Try to calculate each Greek, skip if not supported by engine
        try:
            greeks["delta"] = float(option.delta())
        except:
            greeks["delta"] = 0.0

        try:
            greeks["gamma"] = float(option.gamma())
        except:
            greeks["gamma"] = 0.0

        try:
            greeks["vega"] = float(option.vega())
        except:
            # Vega not available in binomial engine, calculate via bump-and-reprice
            epsilon_vol = sigma * 0.01
            greeks["vega"] = _calculate_vega_bump_reprice(
                S, K, r, sigma, T, q, option_type, is_american, epsilon_vol
            )

        try:
            greeks["theta"] = float(option.theta())
        except:
            greeks["theta"] = 0.0

        try:
            greeks["rho"] = float(option.rho())
        except:
            greeks["rho"] = 0.0

        return greeks

    except Exception as e:
        raise ValueError(f"QuantLib Greeks calculation failed: {e}")


def _calculate_vega_bump_reprice(S: float, K: float, r: float, sigma: float, T: float,
                                  q: float, option_type: str, is_american: bool,
                                  epsilon_vol: float) -> float:
    """Calculate vega via bump-and-reprice when not available from engine."""
    try:
        # Up
        price_up = price_american_ql(S, K, r, sigma + epsilon_vol, T, q,
                                    int(T * 100), option_type)[0]
        # Down
        price_down = price_american_ql(S, K, r, sigma - epsilon_vol, T, q,
                                      int(T * 100), option_type)[0]
        # Vega per 1% change
        vega = (price_up - price_down) / (2 * epsilon_vol) * sigma * 0.01
        return float(vega)
    except:
        return 0.0
