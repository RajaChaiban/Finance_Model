"""Routing logic: select the right pricing engine based on option type.

Uses QuantLib as primary pricing engine (production-grade, battle-tested).
Falls back to manual implementations if QuantLib unavailable.
"""

from typing import Callable, Tuple
from . import black_scholes, monte_carlo_lsm, knockout

try:
    from . import quantlib_engine
    QUANTLIB_AVAILABLE = True
except (ImportError, RuntimeError):
    QUANTLIB_AVAILABLE = False


def route(option_type: str) -> Tuple[Callable, Callable, str]:
    """Route to the appropriate pricing engine based on option type.

    - European options -> Black-Scholes / QuantLib analytic (instant)
    - American options -> QuantLib binomial / Monte Carlo LSM
    - Knockout options -> Reiner-Rubinstein / QuantLib barrier (instant)

    Returns:
        (pricer_func, greeks_func, description)
    """
    routing_table = {
        "european_put": (
            _make_european_pricer("put"),
            _make_european_greeks("put"),
            "QuantLib (European, Analytical)" if QUANTLIB_AVAILABLE else "Black-Scholes (European, Analytical)",
        ),
        "european_call": (
            _make_european_pricer("call"),
            _make_european_greeks("call"),
            "QuantLib (European, Analytical)" if QUANTLIB_AVAILABLE else "Black-Scholes (European, Analytical)",
        ),
        "american_put": (
            _make_american_pricer("put"),
            _make_american_greeks("put"),
            "QuantLib (American, Binomial Tree)" if QUANTLIB_AVAILABLE else "Monte Carlo LSM (American)",
        ),
        "american_call": (
            _make_american_pricer("call"),
            _make_american_greeks("call"),
            "QuantLib (American, Binomial Tree)" if QUANTLIB_AVAILABLE else "Monte Carlo LSM (American)",
        ),
        "knockout_call": (
            _make_knockout_pricer("call"),
            _make_knockout_greeks("call"),
            "QuantLib (Barrier, Analytical)" if QUANTLIB_AVAILABLE else "Reiner-Rubinstein (Analytical)",
        ),
        "knockout_put": (
            _make_knockout_pricer("put"),
            _make_knockout_greeks("put"),
            "QuantLib (Barrier, Analytical)" if QUANTLIB_AVAILABLE else "Reiner-Rubinstein (Analytical)",
        ),
    }

    if option_type not in routing_table:
        valid = list(routing_table.keys())
        raise ValueError(f"Unknown option_type: {option_type}\nValid types: {valid}")

    return routing_table[option_type]


def _make_european_pricer(opt: str) -> Callable:
    def pricer(S, K, r, sigma, T, q, **kwargs):
        if QUANTLIB_AVAILABLE:
            # Use the analytic European engine via greeks_ql for the price
            res = quantlib_engine.greeks_ql(S, K, r, sigma, T, q, option_type=opt, is_american=False)
            return res["price"], 0.0, None
        return black_scholes.price_european(S, K, r, sigma, T, q, opt), 0.0, None
    return pricer


def _make_european_greeks(opt: str) -> Callable:
    def greeks(S, K, r, sigma, T, q, **kwargs):
        if QUANTLIB_AVAILABLE:
            return quantlib_engine.greeks_ql(S, K, r, sigma, T, q, option_type=opt, is_american=False)
        return black_scholes.greeks_european(S, K, r, sigma, T, q, opt)
    return greeks


def _make_american_pricer(opt: str) -> Callable:
    def pricer(S, K, r, sigma, T, q, n_paths=10000, n_steps=90, variance_reduction="none", **kwargs):
        if QUANTLIB_AVAILABLE:
            return quantlib_engine.price_american_ql(S, K, r, sigma, T, q, n_steps=n_steps, option_type=opt)
        return monte_carlo_lsm.price_american(S, K, r, sigma, T, q, n_paths, n_steps, variance_reduction)
    return pricer


def _make_american_greeks(opt: str) -> Callable:
    def greeks(S, K, r, sigma, T, q, **kwargs):
        if QUANTLIB_AVAILABLE:
            return quantlib_engine.greeks_ql(S, K, r, sigma, T, q, option_type=opt, is_american=True)
        return monte_carlo_lsm.greeks_american(S, K, r, sigma, T, q)
    return greeks


def _make_knockout_pricer(opt: str) -> Callable:
    def pricer(S, K, r, sigma, T, q, barrier_level=None, **kwargs):
        if barrier_level is None:
            raise ValueError(f"knockout_{opt} requires barrier_level parameter")
        if QUANTLIB_AVAILABLE:
            return quantlib_engine.price_knockout_ql(S, K, barrier_level, r, sigma, T, q, opt)
        price, _, _, _ = knockout.price_knockout(S, K, barrier_level, r, sigma, T, q, opt)
        return price, 0.0, None
    return pricer


def _make_knockout_greeks(opt: str) -> Callable:
    def greeks(S, K, r, sigma, T, q, barrier_level=None, **kwargs):
        if barrier_level is None:
            raise ValueError(f"knockout_{opt} greeks require barrier_level parameter")
        if QUANTLIB_AVAILABLE:
            return quantlib_engine.greeks_knockout_ql(S, K, barrier_level, r, sigma, T, q, opt)
        return knockout.greeks_knockout(S, K, barrier_level, r, sigma, T, q, opt)
    return greeks
