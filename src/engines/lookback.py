"""Lookback option pricing via QuantLib closed-form engines.

Two flavours:
  * Fixed-strike    → AnalyticContinuousFixedLookbackEngine
                      (Conze-Viswanathan 1991). Payoff:
                        call: max(S_max - K, 0),  put: max(K - S_min, 0)
  * Floating-strike → AnalyticContinuousFloatingLookbackEngine
                      (Goldman-Sosin-Gatto 1979). Payoff:
                        call: S_T - S_min,  put: S_max - S_T
                      (always non-negative by construction).

The ``K`` parameter in the public API has different meanings:
  * For ``lookback_type='fixed'``, K is the strike (PlainVanillaPayoff).
  * For ``lookback_type='floating'``, K is the running extremum (S_min for
    a call, S_max for a put). For a fresh option, callers should pass K=S.
This dual interpretation keeps the platform's PricingRequest schema uniform.

Conventions match the rest of ``src/engines/``:
  - Day count: Actual/365 Fixed
  - Calendar:  NYSE
  - Theta:     per-calendar-day (forward in time)
  - Vega/Rho:  per 1% absolute σ / r
"""

import QuantLib as ql
from typing import Tuple

from . import black_scholes


_CALENDAR = ql.UnitedStates(ql.UnitedStates.NYSE)
_DAY_COUNT = ql.Actual365Fixed()


def _days_from_T(T: float) -> int:
    return max(int(T * 365.0 + 0.5), 1)


def _build_process(S: float, r: float, sigma: float, q: float,
                   today: ql.Date) -> ql.GeneralizedBlackScholesProcess:
    spot = ql.QuoteHandle(ql.SimpleQuote(S))
    r_ts = ql.YieldTermStructureHandle(ql.FlatForward(today, r, _DAY_COUNT))
    q_ts = ql.YieldTermStructureHandle(ql.FlatForward(today, q, _DAY_COUNT))
    v_ts = ql.BlackVolTermStructureHandle(
        ql.BlackConstantVol(today, _CALENDAR, sigma, _DAY_COUNT)
    )
    return ql.GeneralizedBlackScholesProcess(spot, q_ts, r_ts, v_ts)


def _ql_option_type(option_type: str) -> int:
    o = option_type.lower()
    if o == "call":
        return ql.Option.Call
    if o == "put":
        return ql.Option.Put
    raise ValueError(f"option_type must be 'call' or 'put', got {option_type!r}")


def price_lookback(S: float, K: float, r: float, sigma: float, T: float,
                   q: float = 0.0, option_type: str = "call",
                   lookback_type: str = "fixed") -> Tuple[float, float, None]:
    """Price a lookback option (fixed or floating strike).

    Args:
        S, r, sigma, T, q: standard option parameters
        K: strike (fixed) OR running extremum (floating, see module docstring)
        option_type: 'call' or 'put'
        lookback_type: 'fixed' or 'floating'

    Returns:
        (price, std_error, None). std_error == 0.0 (analytic).
    """
    lt = lookback_type.lower()
    if lt not in ("fixed", "floating"):
        raise ValueError(
            f"lookback_type must be 'fixed' or 'floating', got {lookback_type!r}"
        )

    today = ql.Date.todaysDate()
    ql.Settings.instance().evaluationDate = today
    maturity = today + _days_from_T(T)

    process = _build_process(S, r, sigma, q, today)
    exercise = ql.EuropeanExercise(maturity)
    qopt = _ql_option_type(option_type)

    if lt == "fixed":
        # Fixed-strike: PlainVanillaPayoff(K), with running min/max == S for fresh option.
        # QL signature: ContinuousFixedLookbackOption(minmax, payoff, exercise)
        payoff = ql.PlainVanillaPayoff(qopt, K)
        # For a fresh fixed-strike call, S_max = S; for a fresh put, S_min = S.
        # Caller can override via K? No — K is the strike here, so use S.
        minmax = float(S)
        option = ql.ContinuousFixedLookbackOption(minmax, payoff, exercise)
        engine = ql.AnalyticContinuousFixedLookbackEngine(process)
    else:
        # Floating-strike: FloatingTypePayoff, K reused as running extremum.
        # QL signature: ContinuousFloatingLookbackOption(minmax, payoff, exercise)
        payoff = ql.FloatingTypePayoff(qopt)
        minmax = float(K)
        option = ql.ContinuousFloatingLookbackOption(minmax, payoff, exercise)
        engine = ql.AnalyticContinuousFloatingLookbackEngine(process)

    option.setPricingEngine(engine)
    return float(option.NPV()), 0.0, None


def greeks_lookback(S: float, K: float, r: float, sigma: float, T: float,
                    q: float = 0.0, option_type: str = "call",
                    lookback_type: str = "fixed") -> dict:
    """Bump-and-reprice Greeks for lookback options.

    Closed-form engine each reprice → Greeks are deterministic (no MC noise).
    Bump steps match ``quantlib_engine.py``: ±1% relative S for delta/gamma,
    ±1% absolute σ for vega, ±1% absolute r for rho, +1 calendar day for theta.
    """
    def px(S_, r_, sigma_, T_, q_) -> float:
        p, _, _ = price_lookback(
            S_, K, r_, sigma_, T_, q_,
            option_type=option_type, lookback_type=lookback_type,
        )
        return p

    price_base = px(S, r, sigma, T, q)

    h = S * 0.01
    p_up = px(S + h, r, sigma, T, q)
    p_dn = px(S - h, r, sigma, T, q)
    delta = (p_up - p_dn) / (2 * h)
    gamma = (p_up - 2 * price_base + p_dn) / (h * h)

    vol_bump = 0.01
    p_vu = px(S, r, sigma + vol_bump, T, q)
    vega = (p_vu - price_base) / vol_bump / 100.0

    T_down = max(T - 1.0 / 365.0, 0.001)
    p_t_down = px(S, r, sigma, T_down, q)
    theta = p_t_down - price_base

    rate_bump = 0.01
    p_ru = px(S, r + rate_bump, sigma, T, q)
    rho = (p_ru - price_base) / rate_bump / 100.0

    # Compare to vanilla European at same K (only meaningful for fixed-strike).
    european_price = black_scholes.price_european(S, K, r, sigma, T, q, option_type)
    lookback_premium = price_base - european_price

    return {
        "delta": float(delta),
        "gamma": float(gamma),
        "vega": float(vega),
        "theta": float(theta),
        "rho": float(rho),
        "price": float(price_base),
        "european_price": float(european_price),
        "lookback_premium": float(lookback_premium),
    }
