"""Routing logic: select the right pricing engine based on option type."""

from typing import Callable, Tuple
from . import black_scholes, monte_carlo_lsm, knockout


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
        # European options -> Black-Scholes
        "european_put": (
            _european_put_pricer,
            black_scholes.greeks_european,
            "Black-Scholes (European, Analytical)"
        ),
        "european_call": (
            _european_call_pricer,
            black_scholes.greeks_european,
            "Black-Scholes (European, Analytical)"
        ),

        # American options -> Monte Carlo LSM
        "american_put": (
            _american_put_pricer,
            monte_carlo_lsm.greeks_american,
            "Monte Carlo LSM (American, Bump-and-Reprice Greeks)"
        ),
        "american_call": (
            _american_call_pricer,
            monte_carlo_lsm.greeks_american,
            "Monte Carlo LSM (American, Bump-and-Reprice Greeks)"
        ),

        # Knockout options -> Analytical barrier formula
        "knockout_call": (
            _knockout_call_pricer,
            knockout.greeks_knockout,
            "Merton Barrier Formula (Analytical)"
        ),
        "knockout_put": (
            _knockout_put_pricer,
            knockout.greeks_knockout,
            "Merton Barrier Formula (Analytical)"
        ),
    }

    if option_type not in routing_table:
        valid = list(routing_table.keys())
        raise ValueError(f"Unknown option_type: {option_type}\nValid types: {valid}")

    return routing_table[option_type]


def _european_put_pricer(S, K, r, sigma, T, q, **kwargs):
    """Wrapper for European put pricing."""
    price = black_scholes.price_european(S, K, r, sigma, T, q, "put")
    return price, 0.0, None


def _european_call_pricer(S, K, r, sigma, T, q, **kwargs):
    """Wrapper for European call pricing."""
    price = black_scholes.price_european(S, K, r, sigma, T, q, "call")
    return price, 0.0, None


def _american_put_pricer(S, K, r, sigma, T, q, n_paths=10000, n_steps=90,
                         variance_reduction="none", **kwargs):
    """Wrapper for American put pricing."""
    return monte_carlo_lsm.price_american(
        S, K, r, sigma, T, q, n_paths, n_steps, variance_reduction
    )


def _american_call_pricer(S, K, r, sigma, T, q, n_paths=10000, n_steps=90,
                          variance_reduction="none", **kwargs):
    """Wrapper for American call pricing.

    Note: American calls on non-dividend-paying stock are same as European.
    """
    return monte_carlo_lsm.price_american(
        S, K, r, sigma, T, q, n_paths, n_steps, variance_reduction
    )


def _knockout_call_pricer(S, K, r, sigma, T, q, barrier_level=None, **kwargs):
    """Wrapper for knockout call pricing."""
    if barrier_level is None:
        raise ValueError("knockout_call requires barrier_level parameter")

    price, vanilla, adj, lamb = knockout.price_knockout(
        S, K, barrier_level, r, sigma, T, q, "call"
    )
    return price, 0.0, None


def _knockout_put_pricer(S, K, r, sigma, T, q, barrier_level=None, **kwargs):
    """Wrapper for knockout put pricing."""
    if barrier_level is None:
        raise ValueError("knockout_put requires barrier_level parameter")

    price, vanilla, adj, lamb = knockout.price_knockout(
        S, K, barrier_level, r, sigma, T, q, "put"
    )
    return price, 0.0, None
