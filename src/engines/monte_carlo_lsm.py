"""American option pricing via QuantLib's Monte Carlo Longstaff-Schwartz engine.

Replaces the previous hand-rolled NumPy LSM. Public function signatures are
unchanged so the router and existing call sites continue to work. QuantLib
is now a hard dependency of this module — there is no NumPy fallback.

Conventions match ``quantlib_engine.py``:
  - Day count: Actual/365 Fixed
  - Calendar:  NYSE (US equity options)
  - Theta:     per-calendar-day, sign convention ∂V/∂t (forward in time)
  - Vega/Rho:  per 1% absolute σ / r
  - The third returned element of ``price_american`` (``paths``) is now
    ``None`` — QuantLib's MC engine does not expose path arrays.
"""

import QuantLib as ql
from typing import Tuple
from . import black_scholes


_CALENDAR = ql.UnitedStates(ql.UnitedStates.NYSE)
_DAY_COUNT = ql.Actual365Fixed()
_DEFAULT_SEED = 42  # fixed seed → common random numbers across bump-reprice Greeks


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


def price_american(S: float, K: float, r: float, sigma: float, T: float, q: float = 0,
                   n_paths: int = 10000, n_steps: int = 90,
                   variance_reduction: str = "none",
                   option_type: str = "put") -> Tuple[float, float, None]:
    """Price American option via ``ql.MCAmericanEngine`` (Longstaff-Schwartz).

    Args:
        S, K, r, sigma, T, q: Standard option parameters
        n_paths:    Monte Carlo sample count (mapped to ``requiredSamples``)
        n_steps:    Time-grid resolution (mapped to ``timeSteps``)
        variance_reduction: "none" or "antithetic"
        option_type: "call" or "put"

    Returns:
        ``(price, std_error, None)``. ``paths`` is no longer materialised —
        the QuantLib engine works on the C++ side without exposing the
        underlying path matrix.
    """
    opt_lower = option_type.lower()
    if opt_lower not in ("call", "put"):
        raise ValueError("option_type must be 'call' or 'put'")

    today = ql.Date.todaysDate()
    ql.Settings.instance().evaluationDate = today
    maturity = today + _days_from_T(T)

    payoff = ql.PlainVanillaPayoff(
        ql.Option.Call if opt_lower == "call" else ql.Option.Put, K
    )
    exercise = ql.AmericanExercise(today, maturity)
    option = ql.VanillaOption(payoff, exercise)

    process = _build_process(S, r, sigma, q, today)

    antithetic = variance_reduction == "antithetic"
    # polynomOrder=3 (cubic monomial basis) matches the prior np.polyfit(...,3)
    # behaviour. Production desks often prefer Laguerre for ill-conditioned
    # ITM regressions; switch ``polynomType`` to ``ql.LsmBasisSystem.Laguerre``
    # if you see regression instability on deep-ITM strikes.
    engine = ql.MCAmericanEngine(
        process,
        "pseudorandom",
        timeSteps=n_steps,
        antitheticVariate=antithetic,
        requiredSamples=n_paths,
        seed=_DEFAULT_SEED,
        polynomOrder=3,
        polynomType=ql.LsmBasisSystem.Monomial,
    )
    option.setPricingEngine(engine)

    price = float(option.NPV())
    try:
        std_error = float(option.errorEstimate())
    except RuntimeError:
        # errorEstimate() can raise on extremely small sample counts; fall back to 0.
        std_error = 0.0
    return price, std_error, None


def greeks_american(S: float, K: float, r: float, sigma: float, T: float, q: float = 0,
                    n_paths: int = 5000, n_steps: int = 45,
                    option_type: str = "put") -> dict:
    """Bump-and-reprice Greeks with common random numbers (CRN).

    Each reprice uses the same seed and timestep grid, so the underlying
    Brownian increments are identical across bumps. Differences therefore
    reflect parameter sensitivity rather than MC noise — this is the
    standard MC Greeks technique. Theta has partial CRN (T changes the
    time grid) and is intrinsically the noisiest Greek under MC.
    """
    def px(S_: float, r_: float, sigma_: float, T_: float, q_: float) -> float:
        p, _, _ = price_american(
            S_, K, r_, sigma_, T_, q_,
            n_paths=n_paths, n_steps=n_steps,
            variance_reduction="antithetic",
            option_type=option_type,
        )
        return p

    price_base = px(S, r, sigma, T, q)

    # Delta / Gamma: central-difference in spot, h = 1% of S
    h = S * 0.01
    p_up = px(S + h, r, sigma, T, q)
    p_dn = px(S - h, r, sigma, T, q)
    delta = (p_up - p_dn) / (2 * h)
    gamma = (p_up - 2 * price_base + p_dn) / (h * h)

    # Vega: 1 vol-point bump, expressed per 1% absolute σ
    vol_bump = 0.01
    p_vu = px(S, r, sigma + vol_bump, T, q)
    vega = (p_vu - price_base) / vol_bump / 100.0

    # Theta: per-calendar-day forward (matches QuantLib convention).
    # T_down = T − 1/365 means "one day later"; for long options p_t_down < p_base
    # → theta negative, as expected.
    T_down = max(T - 1.0 / 365.0, 0.001)
    p_t_down = px(S, r, sigma, T_down, q)
    theta = p_t_down - price_base

    # Rho: 1bp bump, expressed per 1% absolute r
    rate_bump = 0.01
    p_ru = px(S, r + rate_bump, sigma, T, q)
    rho = (p_ru - price_base) / rate_bump / 100.0

    european_price = black_scholes.price_european(S, K, r, sigma, T, q, option_type)
    early_exercise_premium = price_base - european_price

    return {
        "delta": float(delta),
        "gamma": float(gamma),
        "vega": float(vega),
        "theta": float(theta),
        "rho": float(rho),
        "price": float(price_base),
        "early_exercise_premium": float(early_exercise_premium),
        "early_exercise_premium_pct": float(
            early_exercise_premium / european_price * 100 if european_price > 0 else 0
        ),
    }
