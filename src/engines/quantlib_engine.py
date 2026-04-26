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
        today = ql.Date(1, 1, 2025)
        ql.Settings.instance().evaluationDate = today
        maturity = today + max(int(T * 365), 1)

        if option_type.lower() == 'call':
            payoff = ql.PlainVanillaPayoff(ql.Option.Call, K)
            barrier_type = ql.Barrier.DownOut if B < S else ql.Barrier.UpOut
        else:
            payoff = ql.PlainVanillaPayoff(ql.Option.Put, K)
            barrier_type = ql.Barrier.UpOut if B > S else ql.Barrier.DownOut

        exercise = ql.EuropeanExercise(maturity)
        rebate = 0.0
        barrier_option = ql.BarrierOption(barrier_type, B, rebate, payoff, exercise)

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

        # AnalyticBarrierEngine implements the Merton/Reiner-Rubinstein closed-form
        # for continuously-monitored single barriers under GBM.
        engine = ql.AnalyticBarrierEngine(process)
        barrier_option.setPricingEngine(engine)

        return float(barrier_option.NPV()), 0.0, None

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
        # Leisen-Reimer tree converges as O(1/N^2), much smoother than CRR.
        # n_steps must be odd for LR to centre on the strike — round up if needed.
        steps = n_steps if n_steps % 2 == 1 else n_steps + 1
        engine = ql.BinomialVanillaEngine(process, "lr", max(steps, 51))
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
            engine = ql.BinomialVanillaEngine(process, "lr", 201)
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
            # QuantLib vega is per 1.0 absolute change in σ; divide by 100 for per-1%
            greeks["vega"] = float(option.vega()) / 100.0
        except:
            greeks["vega"] = _calculate_vega_bump_reprice(
                S, K, r, sigma, T, q, option_type, is_american
            )

        try:
            # QuantLib theta is per-year decay; divide by 365 for daily
            greeks["theta"] = float(option.theta()) / 365.0
        except:
            greeks["theta"] = _calculate_theta_bump_reprice(
                S, K, r, sigma, T, q, option_type, is_american
            )

        try:
            # QuantLib rho is per 1.0 absolute change in r; divide by 100 for per-1%
            greeks["rho"] = float(option.rho()) / 100.0
        except:
            greeks["rho"] = _calculate_rho_bump_reprice(
                S, K, r, sigma, T, q, option_type, is_american
            )

        return greeks

    except Exception as e:
        raise ValueError(f"QuantLib Greeks calculation failed: {e}")


def _price_for_bump(S: float, K: float, r: float, sigma: float, T: float, q: float,
                    option_type: str, is_american: bool) -> float:
    """Reprice with QuantLib using American or European exercise (for bump-reprice Greeks)."""
    today = ql.Date(1, 1, 2025)
    ql.Settings.instance().evaluationDate = today
    maturity = today + max(int(T * 365), 1)

    payoff = ql.PlainVanillaPayoff(
        ql.Option.Call if option_type.lower() == 'call' else ql.Option.Put, K
    )
    exercise = ql.AmericanExercise(today, maturity) if is_american else ql.EuropeanExercise(maturity)
    option = ql.VanillaOption(payoff, exercise)

    spot_quote = ql.QuoteHandle(ql.SimpleQuote(S))
    risk_free_ts = ql.YieldTermStructureHandle(ql.FlatForward(today, r, ql.Actual360()))
    dividend_ts = ql.YieldTermStructureHandle(ql.FlatForward(today, q, ql.Actual360()))
    vol_ts = ql.BlackVolTermStructureHandle(
        ql.BlackConstantVol(today, ql.TARGET(), sigma, ql.Actual360())
    )
    process = ql.GeneralizedBlackScholesProcess(spot_quote, dividend_ts, risk_free_ts, vol_ts)

    if is_american:
        engine = ql.BinomialVanillaEngine(process, "lr", 201)
    else:
        engine = ql.AnalyticEuropeanEngine(process)
    option.setPricingEngine(engine)
    return float(option.NPV())


def _calculate_vega_bump_reprice(S: float, K: float, r: float, sigma: float, T: float,
                                  q: float, option_type: str, is_american: bool) -> float:
    """Vega via central-difference bump-reprice. Returns per 1% absolute σ move."""
    try:
        epsilon = 0.005  # 0.5 vol-points absolute
        price_up = _price_for_bump(S, K, r, sigma + epsilon, T, q, option_type, is_american)
        price_down = _price_for_bump(S, K, r, sigma - epsilon, T, q, option_type, is_american)
        return float((price_up - price_down) / (2 * epsilon) * 0.01)
    except Exception:
        return 0.0


def _calculate_theta_bump_reprice(S: float, K: float, r: float, sigma: float, T: float,
                                   q: float, option_type: str, is_american: bool) -> float:
    """Theta via forward-difference bump-reprice. Returns per-day decay."""
    try:
        dT = 1.0 / 365.0
        if T <= dT:
            return 0.0
        price_now = _price_for_bump(S, K, r, sigma, T, q, option_type, is_american)
        price_tomorrow = _price_for_bump(S, K, r, sigma, T - dT, q, option_type, is_american)
        return float(price_tomorrow - price_now)
    except Exception:
        return 0.0


def _calculate_rho_bump_reprice(S: float, K: float, r: float, sigma: float, T: float,
                                 q: float, option_type: str, is_american: bool) -> float:
    """Rho via central-difference bump-reprice. Returns per 1% absolute r move."""
    try:
        epsilon = 0.0001  # 1bp
        price_up = _price_for_bump(S, K, r + epsilon, sigma, T, q, option_type, is_american)
        price_down = _price_for_bump(S, K, r - epsilon, sigma, T, q, option_type, is_american)
        return float((price_up - price_down) / (2 * epsilon) * 0.01)
    except Exception:
        return 0.0


def greeks_knockout_ql(S: float, K: float, B: float, r: float, sigma: float, T: float,
                        q: float, option_type: str = 'call') -> Dict[str, float]:
    """Greeks for a continuously-monitored knock-out option via QuantLib.

    Uses AnalyticBarrierEngine; vega/theta/rho via central-difference bump-reprice
    since the barrier engine doesn't expose them directly.
    """
    p_base, _, _ = price_knockout_ql(S, K, B, r, sigma, T, q, option_type)

    # Delta and gamma via spot bump (AnalyticBarrierEngine.delta/gamma can be unreliable)
    h = max(S * 0.005, 0.01)
    p_up, _, _ = price_knockout_ql(S + h, K, B, r, sigma, T, q, option_type)
    p_dn, _, _ = price_knockout_ql(S - h, K, B, r, sigma, T, q, option_type)
    delta = (p_up - p_dn) / (2 * h)
    gamma = (p_up - 2 * p_base + p_dn) / (h * h)

    # Vega: per 1% absolute σ
    eps_s = 0.005
    p_vu, _, _ = price_knockout_ql(S, K, B, r, sigma + eps_s, T, q, option_type)
    p_vd, _, _ = price_knockout_ql(S, K, B, r, sigma - eps_s, T, q, option_type)
    vega = (p_vu - p_vd) / (2 * eps_s) * 0.01

    # Theta: per day
    dT = 1.0 / 365.0
    if T > dT:
        p_t, _, _ = price_knockout_ql(S, K, B, r, sigma, T - dT, q, option_type)
        theta = p_t - p_base
    else:
        theta = 0.0

    # Rho: per 1% absolute r
    eps_r = 0.0001
    p_ru, _, _ = price_knockout_ql(S, K, B, r + eps_r, sigma, T, q, option_type)
    p_rd, _, _ = price_knockout_ql(S, K, B, r - eps_r, sigma, T, q, option_type)
    rho = (p_ru - p_rd) / (2 * eps_r) * 0.01

    return {
        "price": float(p_base),
        "delta": float(delta),
        "gamma": float(gamma),
        "vega": float(vega),
        "theta": float(theta),
        "rho": float(rho),
    }
