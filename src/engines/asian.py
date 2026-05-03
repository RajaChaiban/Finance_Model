"""Asian option pricing via QuantLib.

Fixed-strike, average-price Asians:
  * Geometric averaging  → AnalyticDiscreteGeometricAveragePriceAsianEngine
                           (exact closed-form for discretely sampled fixings).
  * Arithmetic averaging → MCDiscreteArithmeticAPEngine with the geometric
                           Asian as a control variate (Kemna-Vorst 1990).

Conventions match ``monte_carlo_lsm.py`` / ``quantlib_engine.py``:
  - Day count: Actual/365 Fixed
  - Calendar:  NYSE
  - Theta:     per-calendar-day, sign convention ∂V/∂t (forward in time)
  - Vega/Rho:  per 1% absolute σ / r
  - MC seed:   42 (fixed, so bump-reprice Greeks share random numbers — CRN)

The third element of the ``price_asian`` return tuple (paths) is always
``None``: the QL MC engine encapsulates path generation in C++.
"""

import QuantLib as ql
from typing import List, Optional, Tuple

from . import black_scholes
from ._ql_session import ql_locked, days_from_T as _days_from_T


_CALENDAR = ql.UnitedStates(ql.UnitedStates.NYSE)
_DAY_COUNT = ql.Actual365Fixed()
_DEFAULT_SEED = 42


def _build_process(S: float, r: float, sigma: float, q: float,
                   today: ql.Date) -> ql.GeneralizedBlackScholesProcess:
    spot = ql.QuoteHandle(ql.SimpleQuote(S))
    r_ts = ql.YieldTermStructureHandle(ql.FlatForward(today, r, _DAY_COUNT))
    q_ts = ql.YieldTermStructureHandle(ql.FlatForward(today, q, _DAY_COUNT))
    v_ts = ql.BlackVolTermStructureHandle(
        ql.BlackConstantVol(today, _CALENDAR, sigma, _DAY_COUNT)
    )
    return ql.GeneralizedBlackScholesProcess(spot, q_ts, r_ts, v_ts)


def _build_fixing_schedule(today: ql.Date, maturity: ql.Date,
                           frequency: str) -> List[ql.Date]:
    """Build a list of fixing dates between today (exclusive) and maturity (inclusive).

    daily   → every NYSE business day (excludes weekends/holidays)
    weekly  → every 7 calendar days (rolled to next business day)
    monthly → every 1 calendar month (rolled to next business day)

    Always includes ``maturity`` as the last fixing.
    """
    freq = frequency.lower()
    if freq == "daily":
        period = ql.Period(1, ql.Days)
    elif freq == "weekly":
        period = ql.Period(1, ql.Weeks)
    elif freq == "monthly":
        period = ql.Period(1, ql.Months)
    else:
        raise ValueError(
            f"averaging_frequency must be 'daily'|'weekly'|'monthly', got {frequency!r}"
        )

    schedule: List[ql.Date] = []
    d = _CALENDAR.advance(today, period)
    while d < maturity:
        schedule.append(d)
        d = _CALENDAR.advance(d, period)
    if not schedule or schedule[-1] != maturity:
        schedule.append(maturity)
    return schedule


def _resolve_payoff(option_type: str, K: float) -> ql.PlainVanillaPayoff:
    opt_lower = option_type.lower()
    if opt_lower == "call":
        return ql.PlainVanillaPayoff(ql.Option.Call, K)
    if opt_lower == "put":
        return ql.PlainVanillaPayoff(ql.Option.Put, K)
    raise ValueError(f"option_type must be 'call' or 'put', got {option_type!r}")


@ql_locked
def price_asian(S: float, K: float, r: float, sigma: float, T: float,
                q: float = 0.0, option_type: str = "call",
                averaging_method: str = "geometric",
                averaging_frequency: str = "daily",
                n_paths: int = 50000,
                evaluation_date: "Optional[ql.Date]" = None,
                maturity_date: "Optional[ql.Date]" = None,
                ) -> Tuple[float, float, None]:
    """Price a fixed-strike, average-price Asian option.

    Args:
        S, K, r, sigma, T, q: standard option parameters
        option_type: 'call' or 'put'
        averaging_method: 'geometric' (closed form) or 'arithmetic' (MC + geo CV)
        averaging_frequency: 'daily' | 'weekly' | 'monthly'
        n_paths: MC sample count (arithmetic only)
        evaluation_date: Override today's date (for theta/aged-trade revaluation).
        maturity_date: Pin the maturity to a specific date instead of inferring
            ``today + days_from_T(T)``. Used by ``greeks_asian`` so theta-by-
            date-advance keeps the contract definition (maturity + fixing
            schedule structure) constant — bumping T directly would also drop
            a fixing from the schedule, which conflates time decay with a
            contract change.

    Returns:
        (price, std_error, None). std_error == 0.0 for the geometric closed form.
    """
    method = averaging_method.lower()
    if method not in ("geometric", "arithmetic"):
        raise ValueError(
            f"averaging_method must be 'geometric' or 'arithmetic', got {averaging_method!r}"
        )

    today = evaluation_date if evaluation_date is not None else ql.Date.todaysDate()
    ql.Settings.instance().evaluationDate = today
    maturity = maturity_date if maturity_date is not None else today + _days_from_T(T)

    payoff = _resolve_payoff(option_type, K)
    exercise = ql.EuropeanExercise(maturity)
    fixing_dates = _build_fixing_schedule(today, maturity, averaging_frequency)

    if method == "geometric":
        # runningAccumulator = 1.0 (multiplicative identity), pastFixings = 0
        option = ql.DiscreteAveragingAsianOption(
            ql.Average.Geometric, 1.0, 0, fixing_dates, payoff, exercise,
        )
        process = _build_process(S, r, sigma, q, today)
        engine = ql.AnalyticDiscreteGeometricAveragePriceAsianEngine(process)
        option.setPricingEngine(engine)
        return float(option.NPV()), 0.0, None

    # method == "arithmetic"
    # runningAccumulator = 0.0 (additive identity), pastFixings = 0
    option = ql.DiscreteAveragingAsianOption(
        ql.Average.Arithmetic, 0.0, 0, fixing_dates, payoff, exercise,
    )
    process = _build_process(S, r, sigma, q, today)
    # MCDiscreteArithmeticAPEngine signature (QL Python bindings):
    #   (process, traits, brownianBridge, antitheticVariate, controlVariate,
    #    requiredSamples, requiredTolerance, maxSamples, seed)
    # We want requiredSamples to govern, not requiredTolerance — pass a deliberately
    # loose tolerance so the engine never short-circuits before consuming n_paths,
    # and a maxSamples that matches requiredSamples (no overshoot).
    engine = ql.MCDiscreteArithmeticAPEngine(
        process,
        "pseudorandom",
        False,            # brownianBridge
        False,            # antitheticVariate
        True,             # controlVariate (geometric Asian as CV)
        int(n_paths),     # requiredSamples
        1e6,              # requiredTolerance — effectively disabled
        int(n_paths),     # maxSamples
        _DEFAULT_SEED,
    )
    option.setPricingEngine(engine)
    price = float(option.NPV())
    try:
        std_error = float(option.errorEstimate())
    except RuntimeError:
        std_error = 0.0
    return price, std_error, None


@ql_locked
def greeks_asian(S: float, K: float, r: float, sigma: float, T: float,
                 q: float = 0.0, option_type: str = "call",
                 averaging_method: str = "geometric",
                 averaging_frequency: str = "daily",
                 n_paths: int = 20000) -> dict:
    """Bump-and-reprice Greeks for Asian options.

    Geometric uses the closed-form engine each reprice (cheap → tight tol).
    Arithmetic uses MC with seed=42 each reprice (CRN → low Greek noise).

    Theta is computed by advancing the evaluation date by 1 calendar day with
    the maturity DATE held constant. Bumping ``T`` directly used to also
    shrink the fixing-schedule length by one fixing — that is a contract
    change, not time decay, and produced the wrong-sign theta a flow desk
    would chase as a ghost greek.

    Vega and rho use central differences (was forward); cost of one extra
    reprice each is negligible against the O(h²) accuracy gain.
    """
    # Pin the contract definition: pick today + maturity date once, reuse
    # them for every bump so spot/vol/rate Greeks see the SAME contract.
    today = ql.Date.todaysDate()
    maturity = today + _days_from_T(T)

    def px(S_, r_, sigma_, T_unused_, q_) -> float:
        p, _, _ = price_asian(
            S_, K, r_, sigma_, T, q_,  # T forwarded but maturity_date overrides
            option_type=option_type,
            averaging_method=averaging_method,
            averaging_frequency=averaging_frequency,
            n_paths=n_paths,
            evaluation_date=today,
            maturity_date=maturity,
        )
        return p

    price_base = px(S, r, sigma, T, q)

    h = S * 0.01
    p_up = px(S + h, r, sigma, T, q)
    p_dn = px(S - h, r, sigma, T, q)
    delta = (p_up - p_dn) / (2 * h)
    gamma = (p_up - 2 * price_base + p_dn) / (h * h)

    # Central difference for vega — O(h²) vs forward's O(h).
    vol_bump = 0.01
    p_vu = px(S, r, sigma + vol_bump, T, q)
    p_vd = px(S, r, sigma - vol_bump, T, q)
    vega = (p_vu - p_vd) / (2 * vol_bump) / 100.0

    # Theta: advance eval date by 1 day, keep maturity. p_tomorrow uses the
    # SAME contract (same maturity date, same fixing schedule) seen from one
    # day later — pure time-passage effect.
    p_tomorrow, _, _ = price_asian(
        S, K, r, sigma, T, q,
        option_type=option_type,
        averaging_method=averaging_method,
        averaging_frequency=averaging_frequency,
        n_paths=n_paths,
        evaluation_date=today + 1,
        maturity_date=maturity,
    )
    theta = p_tomorrow - price_base

    # Central difference for rho.
    rate_bump = 0.01
    p_ru = px(S, r + rate_bump, sigma, T, q)
    p_rd = px(S, r - rate_bump, sigma, T, q)
    rho = (p_ru - p_rd) / (2 * rate_bump) / 100.0

    european_price = black_scholes.price_european(S, K, r, sigma, T, q, option_type)
    asian_premium = price_base - european_price

    return {
        "delta": float(delta),
        "gamma": float(gamma),
        "vega": float(vega),
        "theta": float(theta),
        "rho": float(rho),
        "price": float(price_base),
        "european_price": float(european_price),
        "asian_discount_pct": float(
            (european_price - price_base) / european_price * 100 if european_price > 0 else 0
        ),
    }
