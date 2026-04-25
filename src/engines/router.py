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


def _european_greeks_pricer(S, K, r, sigma, T, q, **kwargs):
    """Calculate Greeks for European options using QuantLib."""
    return quantlib_engine.greeks_ql(S, K, r, sigma, T, q,
                                     option_type=kwargs.get('option_type', 'put'),
                                     is_american=False)


def _american_greeks_pricer(S, K, r, sigma, T, q, **kwargs):
    """Calculate Greeks for American options using QuantLib."""
    return quantlib_engine.greeks_ql(S, K, r, sigma, T, q,
                                     option_type=kwargs.get('option_type', 'put'),
                                     is_american=True)


def _knockout_greeks_pricer(S, K, r, sigma, T, q, **kwargs):
    """Calculate Greeks for knockout options using QuantLib."""
    return quantlib_engine.greeks_ql(S, K, r, sigma, T, q,
                                     option_type=kwargs.get('option_type', 'put'),
                                     is_american=False)


def route(option_type: str) -> Tuple[Callable, Callable, str]:
    """Route to the appropriate pricing engine based on option type.

    Uses the decision tree from pricing_pipeline_guide.md:
    - European options -> Black-Scholes (analytical, instant)
    - American options -> Monte Carlo LSM (1-10s, high accuracy)
    - Knockout options -> Analytical barrier formula (instant)

    Args:
        option_type: Type of option (american_put, european_call, etc.)

    Returns:
        (pricer_func, greeks_func, description) where:
        - pricer_func: function to call for pricing
        - greeks_func: function to call for Greeks
        - description: human-readable description of method

    Raises:
        ValueError: If option_type is not recognized
    """
    routing_table = {
        # European options -> QuantLib (or Black-Scholes if unavailable)
        "european_put": (
            _european_put_pricer,
            _european_greeks_pricer if QUANTLIB_AVAILABLE else black_scholes.greeks_european,
            f"QuantLib (European, Analytical)" if QUANTLIB_AVAILABLE else "Black-Scholes (European, Analytical)"
        ),
        "european_call": (
            _european_call_pricer,
            _european_greeks_pricer if QUANTLIB_AVAILABLE else black_scholes.greeks_european,
            f"QuantLib (European, Analytical)" if QUANTLIB_AVAILABLE else "Black-Scholes (European, Analytical)"
        ),

        # American options -> QuantLib Binomial (or Monte Carlo LSM if unavailable)
        "american_put": (
            _american_put_pricer,
            _american_greeks_pricer if QUANTLIB_AVAILABLE else monte_carlo_lsm.greeks_american,
            "QuantLib (American, Binomial Tree)" if QUANTLIB_AVAILABLE else "Monte Carlo LSM (American, Bump-and-Reprice Greeks)"
        ),
        "american_call": (
            _american_call_pricer,
            _american_greeks_pricer if QUANTLIB_AVAILABLE else monte_carlo_lsm.greeks_american,
            "QuantLib (American, Binomial Tree)" if QUANTLIB_AVAILABLE else "Monte Carlo LSM (American, Bump-and-Reprice Greeks)"
        ),

        # Knockout options -> QuantLib Barrier (or analytical formula if unavailable)
        "knockout_call": (
            _knockout_call_pricer,
            _knockout_greeks_pricer if QUANTLIB_AVAILABLE else knockout.greeks_knockout,
            "QuantLib (Knockout/Barrier, Analytical)" if QUANTLIB_AVAILABLE else "Merton Barrier Formula (Analytical)"
        ),
        "knockout_put": (
            _knockout_put_pricer,
            _knockout_greeks_pricer if QUANTLIB_AVAILABLE else knockout.greeks_knockout,
            "QuantLib (Knockout/Barrier, Analytical)" if QUANTLIB_AVAILABLE else "Merton Barrier Formula (Analytical)"
        ),
    }

    if option_type not in routing_table:
        valid = list(routing_table.keys())
        raise ValueError(f"Unknown option_type: {option_type}\nValid types: {valid}")

    return routing_table[option_type]


def _european_put_pricer(S, K, r, sigma, T, q, **kwargs):
    """Wrapper for European put pricing."""
    if QUANTLIB_AVAILABLE:
        return quantlib_engine.price_american_ql(S, K, r, sigma, T, q, n_steps=100,
                                                 option_type='put')
    else:
        price = black_scholes.price_european(S, K, r, sigma, T, q, "put")
        return price, 0.0, None


def _european_call_pricer(S, K, r, sigma, T, q, **kwargs):
    """Wrapper for European call pricing."""
    if QUANTLIB_AVAILABLE:
        return quantlib_engine.price_american_ql(S, K, r, sigma, T, q, n_steps=100,
                                                 option_type='call')
    else:
        price = black_scholes.price_european(S, K, r, sigma, T, q, "call")
        return price, 0.0, None


def _american_put_pricer(S, K, r, sigma, T, q, n_paths=10000, n_steps=90,
                         variance_reduction="none", **kwargs):
    """Wrapper for American put pricing."""
    if QUANTLIB_AVAILABLE:
        return quantlib_engine.price_american_ql(S, K, r, sigma, T, q, n_steps=n_steps,
                                                 option_type='put')
    else:
        return monte_carlo_lsm.price_american(
            S, K, r, sigma, T, q, n_paths, n_steps, variance_reduction
        )


def _american_call_pricer(S, K, r, sigma, T, q, n_paths=10000, n_steps=90,
                          variance_reduction="none", **kwargs):
    """Wrapper for American call pricing."""
    if QUANTLIB_AVAILABLE:
        return quantlib_engine.price_american_ql(S, K, r, sigma, T, q, n_steps=n_steps,
                                                 option_type='call')
    else:
        return monte_carlo_lsm.price_american(
            S, K, r, sigma, T, q, n_paths, n_steps, variance_reduction
        )


def _knockout_call_pricer(S, K, r, sigma, T, q, barrier_level=None, **kwargs):
    """Wrapper for knockout call pricing."""
    if barrier_level is None:
        raise ValueError("knockout_call requires barrier_level parameter")

    if QUANTLIB_AVAILABLE:
        return quantlib_engine.price_knockout_ql(S, K, barrier_level, r, sigma, T, q, 'call')
    else:
        price, vanilla, adj, lamb = knockout.price_knockout(
            S, K, barrier_level, r, sigma, T, q, "call"
        )
        return price, 0.0, None


def _knockout_put_pricer(S, K, r, sigma, T, q, barrier_level=None, **kwargs):
    """Wrapper for knockout put pricing."""
    if barrier_level is None:
        raise ValueError("knockout_put requires barrier_level parameter")

    if QUANTLIB_AVAILABLE:
        return quantlib_engine.price_knockout_ql(S, K, barrier_level, r, sigma, T, q, 'put')
    else:
        price, vanilla, adj, lamb = knockout.price_knockout(
            S, K, barrier_level, r, sigma, T, q, "put"
        )
        return price, 0.0, None
