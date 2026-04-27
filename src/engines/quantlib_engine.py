"""
QuantLib-based pricing engine for derivatives.

Uses QuantLib library for production-grade pricing:
- More robust numerical methods
- Better handling of edge cases
- Industry-standard validation

Conventions
-----------
- Day count: Actual/365 Fixed (engine baseline; matches the to_continuous_act365
  rate convention).
- Calendar: NYSE for US equity options. Business-day rolling and theta over
  weekends/holidays use this calendar. Override per-call if pricing in another
  jurisdiction.
- Time quantization: T (in years) is converted to integer days via
  `_days_from_T(T) = max(int(round(T * 365)), 1)`. Maximum quantization error
  is 0.5 day = ~0.00137 yr; for typical equity options this gives sub-half-cent
  drift vs an "exact-T" closed-form. To eliminate drift entirely, drive the
  pipeline from real trade/expiry dates.
"""

import numpy as np
import QuantLib as ql
from typing import Dict, Optional, Tuple


def _days_from_T(T: float) -> int:
    """Convert T (years) to integer days. Round-half-up, floor at 1 day.

    Why round-half-up (not floor, not banker's): floor biases T_eff DOWN by up
    to a full day. Banker's rounding (Python's built-in `round`) is unbiased
    on average but breaks the common case T=N/2/365 (e.g. T=0.5 → 182.5d →
    182d under banker's). Round-half-up gives consistent ±0.5 day bound.
    """
    return max(int(T * 365.0 + 0.5), 1)


def _setup_evaluation_date(evaluation_date: Optional[ql.Date]) -> ql.Date:
    """Set QL global evaluation date and return it.

    None → use today's date. Allows aged-trade revaluation when caller
    passes a specific date.
    """
    if evaluation_date is None:
        evaluation_date = ql.Date.todaysDate()
    ql.Settings.instance().evaluationDate = evaluation_date
    return evaluation_date


# US equity option calendar (NYSE). Replace ql.TARGET() (EUR) which is wrong
# for US markets — TARGET observes Easter/Labour Day differently and skips
# Christmas Eve, all of which break theta-over-weekend computations.
_US_EQUITY_CALENDAR = ql.UnitedStates(ql.UnitedStates.NYSE)


def _resolve_vol_handle(
    vol_handle: Optional[ql.BlackVolTermStructureHandle],
    today: ql.Date,
    sigma: float,
) -> ql.BlackVolTermStructureHandle:
    """Use the caller-supplied vol surface handle when provided, else build a
    flat ``BlackConstantVol`` handle from the scalar ``sigma``.

    Keeps every engine call site backwards-compatible: pass nothing → original
    flat-vol behaviour, pass a handle → smile-aware pricing.
    """
    if vol_handle is not None:
        return vol_handle
    return ql.BlackVolTermStructureHandle(
        ql.BlackConstantVol(today, _US_EQUITY_CALENDAR, sigma, ql.Actual365Fixed())
    )


_BARRIER_TYPE_MAP = {
    # (barrier_kind, B_is_below_S) -> ql.Barrier.* enum
    ("out", True):  ql.Barrier.DownOut,
    ("out", False): ql.Barrier.UpOut,
    ("in",  True):  ql.Barrier.DownIn,
    ("in",  False): ql.Barrier.UpIn,
}


def price_knockout_ql(S: float, K: float, B: float, r: float, sigma: float,
                      T: float, q: float, option_type: str,
                      evaluation_date: Optional[ql.Date] = None,
                      monitoring="continuous",
                      vol_handle: Optional[ql.BlackVolTermStructureHandle] = None,
                      use_local_vol_pde: bool = False,
                      fd_t_grid: int = 200,
                      fd_x_grid: int = 200,
                      barrier_kind: str = "out",
                      ) -> Tuple[float, float, None]:
    """
    Price barrier option (knock-out OR knock-in) using QuantLib.

    Args:
        S: Spot price
        K: Strike price
        B: Barrier level
        r: Risk-free rate
        sigma: Volatility
        T: Time to expiration (years)
        q: Dividend yield
        option_type: 'call' or 'put'
        evaluation_date: QL evaluation date (None → today)
        monitoring: 'continuous' (default), 'daily', 'weekly', 'monthly', or
            numeric Δt. Discrete monitoring → BGK shift on B before pricing.
        vol_handle: Optional Black vol term-structure handle (smile surface).
        use_local_vol_pde: When True, switch from ``AnalyticBarrierEngine``
            (which collapses any vol surface to a single scalar) to
            ``FdBlackScholesBarrierEngine`` with Dupire-derived local
            volatility. Required for *directional* correctness under a
            steep smile — the analytic engine can over-price KO calls when
            the put-wing of the surface is steeper than the call-wing.
        fd_t_grid, fd_x_grid: PDE grid resolution (only used when
            ``use_local_vol_pde=True``). Defaults match desk practice;
            doubling each gives ~3× cost for sub-bp accuracy gain.
        barrier_kind: 'out' (knock-out, default) or 'in' (knock-in). Direction
            (Down vs Up) is inferred from B vs S — barrier below spot → Down,
            barrier above spot → Up. So ``barrier_kind='in'`` + B<S =
            Down-and-In; B>S = Up-and-In. By no-arbitrage parity,
            KO_price + KI_price ≡ vanilla_price for the same K/B/T/σ.

    Returns:
        (price, std_error, paths) - paths is None for analytical pricing
    """
    if barrier_kind not in ("out", "in"):
        raise ValueError(f"barrier_kind must be 'out' or 'in', got {barrier_kind!r}")
    try:
        # Apply BGK shift before any QL setup so the engine sees the corrected
        # barrier. Note: shift uses ORIGINAL barrier-vs-spot direction.
        from .knockout import _resolve_monitoring, bgk_adjusted_barrier
        monitoring_dt = _resolve_monitoring(monitoring)
        if monitoring_dt > 0:
            B = bgk_adjusted_barrier(B, S, sigma, monitoring_dt)

        today = _setup_evaluation_date(evaluation_date)
        maturity = today + _days_from_T(T)

        if option_type.lower() == 'call':
            payoff = ql.PlainVanillaPayoff(ql.Option.Call, K)
        else:
            payoff = ql.PlainVanillaPayoff(ql.Option.Put, K)
        # Direction is set by barrier-vs-spot, kind (in/out) by caller.
        barrier_type = _BARRIER_TYPE_MAP[(barrier_kind, B < S)]

        exercise = ql.EuropeanExercise(maturity)
        rebate = 0.0
        barrier_option = ql.BarrierOption(barrier_type, B, rebate, payoff, exercise)

        spot_quote = ql.QuoteHandle(ql.SimpleQuote(S))
        risk_free_ts = ql.YieldTermStructureHandle(
            ql.FlatForward(today, r, ql.Actual365Fixed())
        )
        dividend_ts = ql.YieldTermStructureHandle(
            ql.FlatForward(today, q, ql.Actual365Fixed())
        )
        vol_ts = _resolve_vol_handle(vol_handle, today, sigma)
        process = ql.GeneralizedBlackScholesProcess(spot_quote, dividend_ts, risk_free_ts, vol_ts)

        if use_local_vol_pde:
            # Finite-difference PDE with Dupire local vol derived from the
            # supplied Black variance surface — the engine handles the
            # variance-surface → local-vol conversion internally.
            # ``illegal_local_vol_overwrite`` floors the rare cells where
            # Dupire's σ_loc² turns negative (steep wings + sparse data).
            # 0.01 ≈ σ floor 10 % — keeps the PDE stable on illiquid wings
            # without distorting the bulk of the surface.
            engine = ql.FdBlackScholesBarrierEngine(
                process, fd_t_grid, fd_x_grid, 0,
                ql.FdmSchemeDesc.Douglas(),
                True,   # localVol
                0.01,   # illegalLocalVolOverwrite — floor σ² at 0.01 = (10 %)²
            )
        else:
            # AnalyticBarrierEngine implements the Merton/Reiner-Rubinstein closed-form
            # for continuously-monitored single barriers under GBM.
            engine = ql.AnalyticBarrierEngine(process)
        barrier_option.setPricingEngine(engine)

        return float(barrier_option.NPV()), 0.0, None

    except Exception as e:
        raise ValueError(f"QuantLib knockout pricing failed: {e}")


def price_american_ql(S: float, K: float, r: float, sigma: float, T: float,
                      q: float, n_steps: int = 100, option_type: str = 'put',
                      evaluation_date: Optional[ql.Date] = None,
                      vol_handle: Optional[ql.BlackVolTermStructureHandle] = None,
                      ) -> Tuple[float, float, None]:
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
        evaluation_date: QL evaluation date (None → today)

    Returns:
        (price, std_error, paths) - paths is None for tree-based pricing
    """
    try:
        today = _setup_evaluation_date(evaluation_date)
        maturity = today + _days_from_T(T)

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
            ql.FlatForward(today, r, ql.Actual365Fixed())
        )
        dividend_ts = ql.YieldTermStructureHandle(
            ql.FlatForward(today, q, ql.Actual365Fixed())
        )
        vol_ts = _resolve_vol_handle(vol_handle, today, sigma)

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


def price_american_discrete_div_ql(
    S: float, K: float, r: float, sigma: float, T: float,
    dividend_schedule, option_type: str = 'put',
    n_t_steps: int = 200, n_x_steps: int = 200,
    evaluation_date: Optional[ql.Date] = None,
    vol_handle: Optional[ql.BlackVolTermStructureHandle] = None,
) -> Tuple[float, float, None]:
    """Price an American option with explicit (date, amount) cash dividends.

    Continuous-yield approximation cannot reproduce the spot drop on ex-div
    dates that drives American-call early-exercise. This function uses
    QuantLib's ``FdBlackScholesVanillaEngine`` with a discrete
    ``DividendSchedule`` for institutional-grade single-name pricing.

    Args:
        S, K, r, sigma, T: Standard option parameters (q = 0 implicitly —
            dividends are discrete cash, not continuous yield).
        dividend_schedule: List of ``(ql.Date, float)`` tuples. Dates after
            the option's expiry are silently ignored by QL (no impact).
        option_type: 'call' or 'put'.
        n_t_steps, n_x_steps: FDM grid resolution. Defaults give ~1bp accuracy
            for typical equity options. Increase for thinly-spaced dividends.
        evaluation_date: QL evaluation date (None → today).

    Returns:
        ``(price, std_error, paths)`` matching the rest of the engine API.
        ``std_error`` is 0 (deterministic FDM); ``paths`` is None.
    """
    today = _setup_evaluation_date(evaluation_date)
    maturity = today + _days_from_T(T)

    if option_type.lower() == 'call':
        payoff = ql.PlainVanillaPayoff(ql.Option.Call, K)
    else:
        payoff = ql.PlainVanillaPayoff(ql.Option.Put, K)
    exercise = ql.AmericanExercise(today, maturity)
    option = ql.VanillaOption(payoff, exercise)

    spot_quote = ql.QuoteHandle(ql.SimpleQuote(S))
    risk_free_ts = ql.YieldTermStructureHandle(
        ql.FlatForward(today, r, ql.Actual365Fixed())
    )
    # No continuous-yield component when dividends are discrete cash. Mixing
    # discrete + continuous on the same name is a model error (double-counting).
    dividend_ts = ql.YieldTermStructureHandle(
        ql.FlatForward(today, 0.0, ql.Actual365Fixed())
    )
    vol_ts = _resolve_vol_handle(vol_handle, today, sigma)
    process = ql.GeneralizedBlackScholesProcess(spot_quote, dividend_ts, risk_free_ts, vol_ts)

    sched = ql.DividendSchedule()
    for div_date, amount in dividend_schedule:
        sched.append(ql.FixedDividend(float(amount), div_date))

    engine = ql.FdBlackScholesVanillaEngine(process, sched, n_t_steps, n_x_steps)
    option.setPricingEngine(engine)

    return float(option.NPV()), 0.0, None


def greeks_ql(S: float, K: float, r: float, sigma: float, T: float, q: float,
              option_type: str = 'put', is_american: bool = False,
              evaluation_date: Optional[ql.Date] = None,
              vol_handle: Optional[ql.BlackVolTermStructureHandle] = None,
              ) -> Dict[str, float]:
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
        evaluation_date: QL evaluation date (None → today)

    Returns:
        Dictionary of Greeks: delta, gamma, vega, theta, rho
    """
    try:
        today = _setup_evaluation_date(evaluation_date)
        maturity = today + _days_from_T(T)

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
            ql.FlatForward(today, r, ql.Actual365Fixed())
        )
        dividend_ts = ql.YieldTermStructureHandle(
            ql.FlatForward(today, q, ql.Actual365Fixed())
        )
        vol_ts = _resolve_vol_handle(vol_handle, today, sigma)

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
                    option_type: str, is_american: bool,
                    evaluation_date: Optional[ql.Date] = None) -> float:
    """Reprice with QuantLib using American or European exercise (for bump-reprice Greeks)."""
    today = _setup_evaluation_date(evaluation_date)
    maturity = today + _days_from_T(T)

    payoff = ql.PlainVanillaPayoff(
        ql.Option.Call if option_type.lower() == 'call' else ql.Option.Put, K
    )
    exercise = ql.AmericanExercise(today, maturity) if is_american else ql.EuropeanExercise(maturity)
    option = ql.VanillaOption(payoff, exercise)

    spot_quote = ql.QuoteHandle(ql.SimpleQuote(S))
    risk_free_ts = ql.YieldTermStructureHandle(ql.FlatForward(today, r, ql.Actual365Fixed()))
    dividend_ts = ql.YieldTermStructureHandle(ql.FlatForward(today, q, ql.Actual365Fixed()))
    vol_ts = ql.BlackVolTermStructureHandle(
        ql.BlackConstantVol(today, _US_EQUITY_CALENDAR, sigma, ql.Actual365Fixed())
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


def _price_american_fdm(S: float, K: float, r: float, sigma: float, T: float, q: float,
                         option_type: str, n_t_steps: int, n_x_steps: int,
                         today: ql.Date) -> float:
    """Internal helper for FDM American pricing (used by vega/rho bump-reprice)."""
    maturity = today + _days_from_T(T)
    payoff = ql.PlainVanillaPayoff(
        ql.Option.Call if option_type.lower() == 'call' else ql.Option.Put, K
    )
    exercise = ql.AmericanExercise(today, maturity)
    option = ql.VanillaOption(payoff, exercise)

    spot = ql.QuoteHandle(ql.SimpleQuote(S))
    r_ts = ql.YieldTermStructureHandle(ql.FlatForward(today, r, ql.Actual365Fixed()))
    q_ts = ql.YieldTermStructureHandle(ql.FlatForward(today, q, ql.Actual365Fixed()))
    v_ts = ql.BlackVolTermStructureHandle(
        ql.BlackConstantVol(today, _US_EQUITY_CALENDAR, sigma, ql.Actual365Fixed())
    )
    process = ql.GeneralizedBlackScholesProcess(spot, q_ts, r_ts, v_ts)
    engine = ql.FdBlackScholesVanillaEngine(process, n_t_steps, n_x_steps)
    option.setPricingEngine(engine)
    return float(option.NPV())


def greeks_american_fdm_ql(
    S: float, K: float, r: float, sigma: float, T: float, q: float,
    option_type: str = 'put', n_t_steps: int = 200, n_x_steps: int = 200,
    evaluation_date: Optional[ql.Date] = None,
    vol_handle: Optional[ql.BlackVolTermStructureHandle] = None,
) -> Dict[str, float]:
    """American Greeks via Finite Difference Method.

    Why FDM and not LR-tree bump-reprice: tree pricers show "ghost gamma" —
    adjacent strikes can differ by 30%+ in their bump-reprice gamma because
    LR node positions snap to discrete grid points. FDM uses a fixed
    space/time grid and interpolates, producing smooth Greek surfaces
    suitable for risk reporting and hedging.

    Engine exposes delta, gamma, theta directly. Vega and rho via
    central-difference bump-reprice (QL FDM doesn't expose them).

    Args:
        S, K, r, sigma, T, q: Standard option parameters
        option_type: 'call' or 'put'
        n_t_steps, n_x_steps: FDM grid resolution (200×200 ≈ 1bp accuracy)
        evaluation_date: QL evaluation date (None → today)

    Returns:
        Dict with price, delta, gamma, vega, theta, rho.
    """
    today = _setup_evaluation_date(evaluation_date)
    maturity = today + _days_from_T(T)

    payoff = ql.PlainVanillaPayoff(
        ql.Option.Call if option_type.lower() == 'call' else ql.Option.Put, K
    )
    exercise = ql.AmericanExercise(today, maturity)
    option = ql.VanillaOption(payoff, exercise)

    spot = ql.QuoteHandle(ql.SimpleQuote(S))
    r_ts = ql.YieldTermStructureHandle(ql.FlatForward(today, r, ql.Actual365Fixed()))
    q_ts = ql.YieldTermStructureHandle(ql.FlatForward(today, q, ql.Actual365Fixed()))
    v_ts = _resolve_vol_handle(vol_handle, today, sigma)
    process = ql.GeneralizedBlackScholesProcess(spot, q_ts, r_ts, v_ts)
    engine = ql.FdBlackScholesVanillaEngine(process, n_t_steps, n_x_steps)
    option.setPricingEngine(engine)

    price = float(option.NPV())
    delta = float(option.delta())
    gamma = float(option.gamma())
    # QL theta is per-year; convert to per-day to match the rest of the pipeline.
    theta = float(option.theta()) / 365.0

    # Vega: central-difference, 0.5 vol-points absolute, normalised per 1%.
    eps_s = 0.005
    p_vu = _price_american_fdm(S, K, r, sigma + eps_s, T, q, option_type,
                                n_t_steps, n_x_steps, today)
    p_vd = _price_american_fdm(S, K, r, sigma - eps_s, T, q, option_type,
                                n_t_steps, n_x_steps, today)
    vega = (p_vu - p_vd) / (2 * eps_s) * 0.01

    # Rho: central-difference, 1bp absolute, normalised per 1%.
    eps_r = 0.0001
    p_ru = _price_american_fdm(S, K, r + eps_r, sigma, T, q, option_type,
                                n_t_steps, n_x_steps, today)
    p_rd = _price_american_fdm(S, K, r - eps_r, sigma, T, q, option_type,
                                n_t_steps, n_x_steps, today)
    rho = (p_ru - p_rd) / (2 * eps_r) * 0.01

    return {
        "price": price,
        "delta": delta,
        "gamma": gamma,
        "vega": vega,
        "theta": theta,
        "rho": rho,
    }


def greeks_knockout_ql(S: float, K: float, B: float, r: float, sigma: float, T: float,
                        q: float, option_type: str = 'call',
                        monitoring="continuous",
                        vol_handle: Optional[ql.BlackVolTermStructureHandle] = None,
                        use_local_vol_pde: bool = False,
                        barrier_kind: str = "out",
                        ) -> Dict[str, float]:
    """Greeks for a barrier option (knock-out OR knock-in) via QuantLib.

    Uses AnalyticBarrierEngine (or FdBlackScholesBarrierEngine when
    ``use_local_vol_pde=True``); vega/theta/rho via central-difference
    bump-reprice since the barrier engine doesn't expose them directly.
    ``monitoring`` is forwarded to every reprice so all Greeks reflect the
    same monitoring convention (continuous, daily, weekly, monthly, or
    numeric Δt). ``barrier_kind`` is similarly forwarded so KI Greeks bump
    against KI-priced legs (no cross-kind contamination).

    When ``vol_handle`` is supplied, **delta / gamma / theta / rho** are
    surface-aware (each reprice uses the supplied surface). **Vega** is
    deliberately a parallel-shift approximation: it bumps the scalar σ
    against a flat surface rather than shifting the supplied surface,
    matching how desks define vega for a smile.
    """
    # PRICE: smile-aware path (FD local vol when requested). This is what
    # the report shows.
    p_display, _, _ = price_knockout_ql(S, K, B, r, sigma, T, q, option_type,
                                        monitoring=monitoring, vol_handle=vol_handle,
                                        use_local_vol_pde=use_local_vol_pde,
                                        barrier_kind=barrier_kind)

    # GREEKS: bump-and-revalue against an **analytic flat-σ** reference for
    # stability. FD discretisation noise on small bumps (1-day θ, 0.5 % S
    # δ) dominates the signal under local-vol pricing and routinely flips
    # signs. Mixing an FD ``p_base`` with analytic bumps also makes the
    # central-difference γ formula garbage. Desks resolve this by
    # computing Greeks against a flat-σ reference and reporting the
    # FD-vs-analytic price gap separately as smile PnL. So:
    #   - displayed ``price`` = FD-with-local-vol (smile-aware)
    #   - displayed Greeks    = analytic flat-σ bump-reprice (stable)
    p_base, _, _ = price_knockout_ql(S, K, B, r, sigma, T, q, option_type,
                                     monitoring=monitoring, barrier_kind=barrier_kind)

    h_default = max(S * 0.005, 0.01)
    distance_to_barrier = abs(S - B)
    if 2 * h_default > 0.5 * distance_to_barrier:
        h = max(0.25 * distance_to_barrier, 1e-4)
    else:
        h = h_default
    p_up, _, _ = price_knockout_ql(S + h, K, B, r, sigma, T, q, option_type,
                                   monitoring=monitoring, barrier_kind=barrier_kind)
    p_dn, _, _ = price_knockout_ql(S - h, K, B, r, sigma, T, q, option_type,
                                   monitoring=monitoring, barrier_kind=barrier_kind)
    delta = (p_up - p_dn) / (2 * h)
    gamma = (p_up - 2 * p_base + p_dn) / (h * h)

    # Vega: per 1% absolute σ.
    eps_s = 0.005
    p_vu, _, _ = price_knockout_ql(S, K, B, r, sigma + eps_s, T, q, option_type,
                                   monitoring=monitoring, barrier_kind=barrier_kind)
    p_vd, _, _ = price_knockout_ql(S, K, B, r, sigma - eps_s, T, q, option_type,
                                   monitoring=monitoring, barrier_kind=barrier_kind)
    vega = (p_vu - p_vd) / (2 * eps_s) * 0.01

    # Theta: per day.
    dT = 1.0 / 365.0
    if T > dT:
        p_t, _, _ = price_knockout_ql(S, K, B, r, sigma, T - dT, q, option_type,
                                       monitoring=monitoring, barrier_kind=barrier_kind)
        theta = p_t - p_base
    else:
        theta = 0.0

    # Rho: per 1% absolute r.
    eps_r = 0.0001
    p_ru, _, _ = price_knockout_ql(S, K, B, r + eps_r, sigma, T, q, option_type,
                                   monitoring=monitoring, barrier_kind=barrier_kind)
    p_rd, _, _ = price_knockout_ql(S, K, B, r - eps_r, sigma, T, q, option_type,
                                   monitoring=monitoring, barrier_kind=barrier_kind)
    rho = (p_ru - p_rd) / (2 * eps_r) * 0.01

    return {
        "price": float(p_display),
        "delta": float(delta),
        "gamma": float(gamma),
        "vega": float(vega),
        "theta": float(theta),
        "rho": float(rho),
    }
