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
            _make_barrier_pricer("call", kind="out"),
            _make_barrier_greeks("call", kind="out"),
            "QuantLib (Barrier, Analytical)" if QUANTLIB_AVAILABLE else "Reiner-Rubinstein (Analytical)",
        ),
        "knockout_put": (
            _make_barrier_pricer("put", kind="out"),
            _make_barrier_greeks("put", kind="out"),
            "QuantLib (Barrier, Analytical)" if QUANTLIB_AVAILABLE else "Reiner-Rubinstein (Analytical)",
        ),
        "knockin_call": (
            _make_barrier_pricer("call", kind="in"),
            _make_barrier_greeks("call", kind="in"),
            "QuantLib (Barrier, Analytical)" if QUANTLIB_AVAILABLE else "Reiner-Rubinstein (Analytical, KI via parity)",
        ),
        "knockin_put": (
            _make_barrier_pricer("put", kind="in"),
            _make_barrier_greeks("put", kind="in"),
            "QuantLib (Barrier, Analytical)" if QUANTLIB_AVAILABLE else "Reiner-Rubinstein (Analytical, KI via parity)",
        ),
    }

    if option_type not in routing_table:
        valid = list(routing_table.keys())
        raise ValueError(f"Unknown option_type: {option_type}\nValid types: {valid}")

    return routing_table[option_type]


def _ql_kwargs(kwargs):
    """Subset of kwargs the QL engines understand (forward; drop the rest)."""
    out = {}
    if "vol_handle" in kwargs and kwargs["vol_handle"] is not None:
        out["vol_handle"] = kwargs["vol_handle"]
    if "use_local_vol_pde" in kwargs and kwargs["use_local_vol_pde"]:
        out["use_local_vol_pde"] = True
    return out


def _make_european_pricer(opt: str) -> Callable:
    def pricer(S, K, r, sigma, T, q, **kwargs):
        if QUANTLIB_AVAILABLE:
            res = quantlib_engine.greeks_ql(
                S, K, r, sigma, T, q, option_type=opt, is_american=False,
                **_ql_kwargs(kwargs),
            )
            return res["price"], 0.0, None
        return black_scholes.price_european(S, K, r, sigma, T, q, opt), 0.0, None
    return pricer


def _make_european_greeks(opt: str) -> Callable:
    def greeks(S, K, r, sigma, T, q, **kwargs):
        if QUANTLIB_AVAILABLE:
            return quantlib_engine.greeks_ql(
                S, K, r, sigma, T, q, option_type=opt, is_american=False,
                **_ql_kwargs(kwargs),
            )
        return black_scholes.greeks_european(S, K, r, sigma, T, q, opt)
    return greeks


def _make_american_pricer(opt: str) -> Callable:
    def pricer(S, K, r, sigma, T, q, n_paths=10000, n_steps=90, variance_reduction="none", **kwargs):
        if QUANTLIB_AVAILABLE:
            return quantlib_engine.price_american_ql(
                S, K, r, sigma, T, q, n_steps=n_steps, option_type=opt,
                **_ql_kwargs(kwargs),
            )
        return monte_carlo_lsm.price_american(S, K, r, sigma, T, q, n_paths, n_steps, variance_reduction,
                                              option_type=opt)
    return pricer


def _make_american_greeks(opt: str) -> Callable:
    def greeks(S, K, r, sigma, T, q, **kwargs):
        if QUANTLIB_AVAILABLE:
            return quantlib_engine.greeks_ql(
                S, K, r, sigma, T, q, option_type=opt, is_american=True,
                **_ql_kwargs(kwargs),
            )
        return monte_carlo_lsm.greeks_american(S, K, r, sigma, T, q, option_type=opt)
    return greeks


def _make_barrier_pricer(opt: str, kind: str = "out") -> Callable:
    """Build a barrier pricer for the given exercise side and KO/KI kind.

    QuantLib path: forwards ``barrier_kind`` to ``price_knockout_ql``.
    Fallback path (no QuantLib): KO via Reiner-Rubinstein; KI via no-arb
    parity ``KI = Vanilla − KO`` (same K, B, T, σ, q, r).
    """
    if kind not in ("out", "in"):
        raise ValueError(f"kind must be 'out' or 'in', got {kind!r}")

    def pricer(S, K, r, sigma, T, q, barrier_level=None, monitoring="continuous", **kwargs):
        if barrier_level is None:
            raise ValueError(f"knock{kind}_{opt} requires barrier_level parameter")
        if QUANTLIB_AVAILABLE:
            return quantlib_engine.price_knockout_ql(
                S, K, barrier_level, r, sigma, T, q, opt,
                monitoring=monitoring, barrier_kind=kind,
                **_ql_kwargs(kwargs),
            )
        # Fallback: Reiner-Rubinstein KO + parity for KI.
        ko_price, _, _, _ = knockout.price_knockout(
            S, K, barrier_level, r, sigma, T, q, opt, monitoring=monitoring,
        )
        if kind == "out":
            return ko_price, 0.0, None
        vanilla_price = black_scholes.price_european(S, K, r, sigma, T, q, opt)
        return vanilla_price - ko_price, 0.0, None

    return pricer


def _make_barrier_greeks(opt: str, kind: str = "out") -> Callable:
    """Build a barrier Greeks function. Falls back to KO Greeks + parity for KI."""
    if kind not in ("out", "in"):
        raise ValueError(f"kind must be 'out' or 'in', got {kind!r}")

    def greeks(S, K, r, sigma, T, q, barrier_level=None, monitoring="continuous", **kwargs):
        if barrier_level is None:
            raise ValueError(f"knock{kind}_{opt} greeks require barrier_level parameter")
        if QUANTLIB_AVAILABLE:
            return quantlib_engine.greeks_knockout_ql(
                S, K, barrier_level, r, sigma, T, q, opt,
                monitoring=monitoring, barrier_kind=kind,
                **_ql_kwargs(kwargs),
            )
        ko_g = knockout.greeks_knockout(
            S, K, barrier_level, r, sigma, T, q, opt, monitoring=monitoring,
        )
        if kind == "out":
            return ko_g
        # KI Greek = vanilla Greek − KO Greek (linearity of differentiation).
        vanilla_g = black_scholes.greeks_european(S, K, r, sigma, T, q, opt)
        return {k: vanilla_g.get(k, 0.0) - ko_g.get(k, 0.0) for k in ko_g.keys()}

    return greeks
