"""Routing logic: select the right pricing engine based on option type.

Uses QuantLib as primary pricing engine (production-grade, battle-tested).
Falls back to manual implementations if QuantLib unavailable.
"""

from typing import Callable, Tuple
from . import black_scholes, monte_carlo_lsm, knockout, asian, lookback

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
        "asian_call": (
            _make_asian_pricer("call"),
            _make_asian_greeks("call"),
            "QuantLib (Asian, Geometric Closed-Form / Arithmetic MC+CV)",
        ),
        "asian_put": (
            _make_asian_pricer("put"),
            _make_asian_greeks("put"),
            "QuantLib (Asian, Geometric Closed-Form / Arithmetic MC+CV)",
        ),
        "lookback_call": (
            _make_lookback_pricer("call"),
            _make_lookback_greeks("call"),
            "QuantLib (Lookback, Analytical)",
        ),
        "lookback_put": (
            _make_lookback_pricer("put"),
            _make_lookback_greeks("put"),
            "QuantLib (Lookback, Analytical)",
        ),
    }

    if option_type not in routing_table:
        valid = list(routing_table.keys())
        raise ValueError(f"Unknown option_type: {option_type}\nValid types: {valid}")

    return routing_table[option_type]


# Surface-handle kwargs that EVERY QL engine in this codebase accepts. Other
# kwargs (n_steps, monitoring, barrier_kind, averaging_*, lookback_type,
# variance_reduction, ...) are engine-specific and are forwarded by the
# per-product wrapper closures below using their named parameters — NOT
# through this filter — to avoid passing a flag to an engine that doesn't
# take it (e.g. n_steps to price_knockout_ql).
#
# When adding a NEW kwarg that ALL QL engines should see (rare — vol_handle
# and use_local_vol_pde are the only ones today), add it here.
_QL_SURFACE_KWARGS = ("vol_handle", "use_local_vol_pde")


def _ql_kwargs(kwargs):
    """Forward only surface-related kwargs that every QL engine accepts.

    Engine-specific kwargs (monitoring, barrier_kind, n_steps, etc.) are
    passed explicitly by the per-product wrappers below — keeping this
    filter narrow prevents accidental cross-engine kwarg leakage.
    """
    out = {}
    if kwargs.get("vol_handle") is not None:
        out["vol_handle"] = kwargs["vol_handle"]
    if kwargs.get("use_local_vol_pde"):
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
    def greeks(S, K, r, sigma, T, q, n_steps=201, n_paths=5000,
               variance_reduction="antithetic", **kwargs):
        if QUANTLIB_AVAILABLE:
            return quantlib_engine.greeks_ql(
                S, K, r, sigma, T, q, option_type=opt, is_american=True,
                n_steps=n_steps,
                **_ql_kwargs(kwargs),
            )
        return monte_carlo_lsm.greeks_american(
            S, K, r, sigma, T, q, n_paths=n_paths, n_steps=n_steps,
            variance_reduction=variance_reduction, option_type=opt,
        )
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


def _make_asian_pricer(opt: str) -> Callable:
    """Build an Asian pricer. Pulls averaging_method/averaging_frequency from kwargs.

    Defaults: averaging_method='geometric', averaging_frequency='daily'.
    """
    def pricer(S, K, r, sigma, T, q, n_paths=50000, **kwargs):
        averaging_method = kwargs.get("averaging_method") or "geometric"
        averaging_frequency = kwargs.get("averaging_frequency") or "daily"
        return asian.price_asian(
            S, K, r, sigma, T, q,
            option_type=opt,
            averaging_method=averaging_method,
            averaging_frequency=averaging_frequency,
            n_paths=n_paths,
        )
    return pricer


def _make_asian_greeks(opt: str) -> Callable:
    def greeks(S, K, r, sigma, T, q, n_paths=20000, **kwargs):
        averaging_method = kwargs.get("averaging_method") or "geometric"
        averaging_frequency = kwargs.get("averaging_frequency") or "daily"
        return asian.greeks_asian(
            S, K, r, sigma, T, q,
            option_type=opt,
            averaging_method=averaging_method,
            averaging_frequency=averaging_frequency,
            n_paths=n_paths,
        )
    return greeks


def _make_lookback_pricer(opt: str) -> Callable:
    """Build a lookback pricer. Pulls lookback_type from kwargs.

    Defaults: lookback_type='fixed'. For floating-strike, K is interpreted
    as the running extremum (caller passes K=S for a fresh option).
    """
    def pricer(S, K, r, sigma, T, q, **kwargs):
        lookback_type = kwargs.get("lookback_type") or "fixed"
        return lookback.price_lookback(
            S, K, r, sigma, T, q,
            option_type=opt, lookback_type=lookback_type,
        )
    return pricer


def _make_lookback_greeks(opt: str) -> Callable:
    def greeks(S, K, r, sigma, T, q, **kwargs):
        lookback_type = kwargs.get("lookback_type") or "fixed"
        return lookback.greeks_lookback(
            S, K, r, sigma, T, q,
            option_type=opt, lookback_type=lookback_type,
        )
    return greeks


def route_with_engine(option_type: str, engine: str = "auto") -> Tuple[Callable, Callable, str]:
    """Route honoring an explicit engine selector.

    engine='auto' (default) reproduces the existing route() behaviour.
    For american_*, engine='mc' forces monte_carlo_lsm regardless of QL.

    engine='analytic'|'tree'|'fdm' are CURRENTLY ALIASES for 'auto' — the
    plumbing to dispatch on these labels has not been wired yet, but the
    schema accepts them so client code can opt into a specific method
    without breaking when the wiring lands. Today they all collapse to
    the QL default. A deprecation warning would be worse UX than silent
    aliasing because the labels are not actually wrong, just imprecise.

    Returns:
        (pricer_func, greeks_func, description)

    Raises:
        ValueError: If engine is not a recognised selector.
    """
    if engine == "auto":
        return route(option_type)

    if engine == "mc" and option_type in ("american_call", "american_put"):
        opt = option_type.split("_")[1]

        def pricer(S, K, r, sigma, T, q, n_paths=10000, n_steps=90,
                   variance_reduction="none", **kwargs):
            return monte_carlo_lsm.price_american(
                S, K, r, sigma, T, q, n_paths, n_steps, variance_reduction,
                option_type=opt,
            )

        def greeks(S, K, r, sigma, T, q, n_paths=10000, n_steps=90,
                   variance_reduction="none", **kwargs):
            # Forward the same MC settings the price call used so greeks["price"]
            # matches the price endpoint to MC noise.
            return monte_carlo_lsm.greeks_american(
                S, K, r, sigma, T, q,
                n_paths=n_paths, n_steps=n_steps,
                variance_reduction=variance_reduction,
                option_type=opt,
            )

        return pricer, greeks, "Monte Carlo LSM (American, forced)"

    if engine in ("analytic", "tree", "fdm"):
        # Phase 1: these all collapse to the QL default for now.
        return route(option_type)

    raise ValueError(
        f"Unknown engine: {engine!r}. Valid values: auto|analytic|tree|mc|fdm"
    )
