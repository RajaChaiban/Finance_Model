"""Routing logic: select the right pricing engine based on option type.

Uses QuantLib as primary pricing engine (production-grade, battle-tested).
Falls back to manual implementations if QuantLib unavailable.
"""

from typing import Callable, Tuple
from . import black_scholes, monte_carlo_lsm, knockout, asian, lookback
from . import digitals, variance_swap as _var_swap, multi_asset_mc, autocallable as _autocall

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
            # Price comes from the LR binomial tree; Greeks come from FDM
            # (FdBlackScholesVanillaEngine) for smooth risk surfaces. The
            # label must mention BOTH engines so report consumers don't
            # misread the source of the Greeks.
            "QuantLib (American, LR Tree price / FDM Greeks)" if QUANTLIB_AVAILABLE else "Monte Carlo LSM (American)",
        ),
        "american_call": (
            _make_american_pricer("call"),
            _make_american_greeks("call"),
            "QuantLib (American, LR Tree price / FDM Greeks)" if QUANTLIB_AVAILABLE else "Monte Carlo LSM (American)",
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
        # ---------- Phase 7 — Senior-structurer product additions ----------
        "digital_call": (
            _make_digital_pricer("call"),
            _make_digital_greeks("call"),
            "Digital cash-or-nothing call (Black-Scholes closed-form)",
        ),
        "digital_put": (
            _make_digital_pricer("put"),
            _make_digital_greeks("put"),
            "Digital cash-or-nothing put (Black-Scholes closed-form)",
        ),
        "phoenix_autocall": (
            _make_phoenix_pricer(),
            _make_phoenix_greeks(),
            "Phoenix autocallable (multi-asset MC, worst-of basket)",
        ),
        "worst_of_put": (
            _make_worst_of_pricer("put"),
            _make_worst_of_greeks("put"),
            "Worst-of basket put (correlated multi-asset MC)",
        ),
        "worst_of_call": (
            _make_worst_of_pricer("call"),
            _make_worst_of_greeks("call"),
            "Worst-of basket call (correlated multi-asset MC)",
        ),
        "variance_swap": (
            _make_var_swap_pricer(),
            _make_var_swap_greeks(),
            "Variance swap (log-contract replication)",
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
    def pricer(S, K, r, sigma, T, q, n_paths=10000, n_steps=90, variance_reduction="none",
               dividend_schedule=None, **kwargs):
        # Discrete cash dividends defeat the continuous-yield approximation —
        # route to the FDM engine that takes a DividendSchedule directly.
        if QUANTLIB_AVAILABLE and dividend_schedule:
            return quantlib_engine.price_american_discrete_div_ql(
                S, K, r, sigma, T,
                dividend_schedule=dividend_schedule, option_type=opt,
                **_ql_kwargs(kwargs),
            )
        if QUANTLIB_AVAILABLE:
            return quantlib_engine.price_american_ql(
                S, K, r, sigma, T, q, n_steps=n_steps, option_type=opt,
                **_ql_kwargs(kwargs),
            )
        return monte_carlo_lsm.price_american(S, K, r, sigma, T, q, n_paths, n_steps, variance_reduction,
                                              option_type=opt)
    return pricer


def _make_american_greeks(opt: str) -> Callable:
    """Default American Greeks now come from the FDM engine — LR-tree-based
    ``greeks_ql`` produces "ghost gamma" artefacts where adjacent strikes can
    differ by 30%+ because the LR node grid is discrete. FDM uses a fixed
    space/time grid + interpolation, giving smooth Greek surfaces suitable
    for risk reporting and hedging. Tree-based Greeks remain available via
    ``route_with_engine(engine="tree")`` for backward compatibility.

    When a discrete ``dividend_schedule`` is supplied, every bump-reprice
    runs against the same FDM-with-DividendSchedule engine so Greeks see the
    spot drops on ex-div dates that drive American-call early exercise.
    """
    def greeks(S, K, r, sigma, T, q, n_steps=201, n_paths=5000,
               variance_reduction="antithetic", dividend_schedule=None, **kwargs):
        if QUANTLIB_AVAILABLE and dividend_schedule:
            return quantlib_engine.greeks_american_discrete_div_ql(
                S, K, r, sigma, T,
                dividend_schedule=dividend_schedule, option_type=opt,
                **_ql_kwargs(kwargs),
            )
        if QUANTLIB_AVAILABLE:
            return quantlib_engine.greeks_american_fdm_ql(
                S, K, r, sigma, T, q, option_type=opt,
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


# ---------------------------------------------------------------------------
# Phase 7 — Senior-structurer product wrappers
# ---------------------------------------------------------------------------
#
# Digitals: closed-form (BS) cash-or-nothing. We do NOT route digitals through
# the smile-aware path in this phase — the analytic digital is enough for an
# indicative quote, and the FDM-LV machinery is overkill for a leaf product
# that's mainly used as a building block for shark-fins / range accruals.
#
# Phoenix / worst-of: dispatch to the existing multi-asset MC engines. These
# require **multi-asset inputs** (correlation matrix, basket spots, basket
# vols) which the single-asset PricingRequest doesn't carry. Tests / agent
# paths supply the basket via kwargs.
#
# Variance swap: takes a vol strip (strikes + ivs) via kwargs; degrades to
# the flat-σ fair-strike when none is supplied. The "price" returned is the
# fair *vol strike*, not a USD value — interpret this as σ_var, the level
# at which a swap with zero PV is struck.


def _make_digital_pricer(opt: str) -> Callable:
    def pricer(S, K, r, sigma, T, q, **kwargs):
        cash_payout = float(kwargs.get("cash_payout", 1.0))
        return digitals.price_digital_cash(S, K, r, sigma, T, q, opt, cash_payout)
    return pricer


def _make_digital_greeks(opt: str) -> Callable:
    def greeks(S, K, r, sigma, T, q, **kwargs):
        cash_payout = float(kwargs.get("cash_payout", 1.0))
        return digitals.greeks_digital_cash(S, K, r, sigma, T, q, opt, cash_payout)
    return greeks


def _make_phoenix_pricer() -> Callable:
    """Phoenix autocallable. Requires multi-asset inputs via kwargs:
        - basket_spots: np.ndarray (n_assets,)
        - basket_sigma: np.ndarray (n_assets,)
        - basket_q: np.ndarray (n_assets,)
        - rho: np.ndarray (n_assets, n_assets)
        - autocall_terms: AutocallTerms
        - obs_schedule: ObservationSchedule
        - notional: float (default 1_000_000)
        - n_paths: int (default 20_000)
        - seed: int | None
    The single-asset (S, K, r, sigma, T, q) inputs are IGNORED for phoenix —
    they exist only because the router signature is shared. Strategist /
    PricingAgent must populate the kwargs path.
    """
    import numpy as np
    def pricer(S, K, r, sigma, T, q, **kwargs):
        terms = kwargs.get("autocall_terms")
        schedule = kwargs.get("obs_schedule")
        if terms is None or schedule is None:
            raise ValueError(
                "phoenix_autocall requires kwargs: autocall_terms, obs_schedule. "
                "Single-asset inputs (S, K, ...) are ignored for this product."
            )
        S0 = np.asarray(kwargs.get("basket_spots", [S]), dtype=float)
        sigma_v = np.asarray(kwargs.get("basket_sigma", [sigma] * len(S0)), dtype=float)
        q_v = np.asarray(kwargs.get("basket_q", [q] * len(S0)), dtype=float)
        rho = kwargs.get("rho")
        if rho is None:
            n = len(S0)
            rho = np.eye(n) if n > 1 else np.array([[1.0]])
        rho = np.asarray(rho, dtype=float)
        price = _autocall.price_phoenix_autocallable(
            S0=S0, r=r, q=q_v, sigma=sigma_v, rho=rho,
            terms=terms, schedule=schedule,
            notional=float(kwargs.get("notional", 1_000_000.0)),
            n_paths=int(kwargs.get("n_paths", 20_000)),
            seed=kwargs.get("seed"),
        )
        return price, 0.0, None
    return pricer


def _make_phoenix_greeks() -> Callable:
    """Phoenix Greeks via bump-and-reprice. Slow — uses 6× pricer calls.
    Returns Greeks in repo conventions."""
    pricer = _make_phoenix_pricer()
    def greeks(S, K, r, sigma, T, q, **kwargs):
        p0, _, _ = pricer(S, K, r, sigma, T, q, **kwargs)
        h_S = max(S * 0.01, 0.01)
        h_v = 0.01
        h_r = 0.0001
        # Re-use the same seed across bumps for variance reduction.
        kw = {**kwargs, "seed": kwargs.get("seed", 42)}
        p_S_up, _, _ = pricer(S + h_S, K, r, sigma, T, q, **kw)
        p_S_dn, _, _ = pricer(S - h_S, K, r, sigma, T, q, **kw)
        delta = (p_S_up - p_S_dn) / (2 * h_S)
        gamma = (p_S_up - 2 * p0 + p_S_dn) / (h_S ** 2)
        p_v_up, _, _ = pricer(S, K, r, sigma + h_v, T, q, **kw)
        p_v_dn, _, _ = pricer(S, K, r, sigma - h_v, T, q, **kw)
        vega = (p_v_up - p_v_dn) / (2 * h_v) / 100.0
        p_r_up, _, _ = pricer(S, K, r + h_r, sigma, T, q, **kw)
        p_r_dn, _, _ = pricer(S, K, r - h_r, sigma, T, q, **kw)
        rho = (p_r_up - p_r_dn) / (2 * h_r) / 100.0
        # Theta: forward 1-day bump (small relative to MC noise — flag it).
        dt = 1.0 / 365.0
        if T - dt > 0:
            p_t, _, _ = pricer(S, K, r, sigma, T - dt, q, **kw)
            theta = p_t - p0
        else:
            theta = 0.0
        return {
            "price": p0, "delta": delta, "gamma": gamma,
            "vega": vega, "theta": theta, "rho": rho,
        }
    return greeks


def _make_worst_of_pricer(opt: str) -> Callable:
    """Worst-of basket put/call. Currently only put has a closed-form MC
    in `multi_asset_mc.price_worst_of_european_put`; call is symmetric and
    inverts the worst-of to best-of-shorts."""
    import numpy as np
    def pricer(S, K, r, sigma, T, q, **kwargs):
        S0 = np.asarray(kwargs.get("basket_spots", [S]), dtype=float)
        sigma_v = np.asarray(kwargs.get("basket_sigma", [sigma] * len(S0)), dtype=float)
        q_v = np.asarray(kwargs.get("basket_q", [q] * len(S0)), dtype=float)
        rho = kwargs.get("rho")
        if rho is None:
            n = len(S0)
            rho = np.eye(n) if n > 1 else np.array([[1.0]])
        rho = np.asarray(rho, dtype=float)
        if opt == "put":
            price = multi_asset_mc.price_worst_of_european_put(
                S0=S0, K=K, r=r, q=q_v, sigma=sigma_v, rho=rho, T=T,
                n_paths=int(kwargs.get("n_paths", 20_000)),
                seed=kwargs.get("seed"),
            )
            return float(price), 0.0, None
        # Call side: simulate, take worst-of perf, payoff = max(worst − K_norm, 0).
        # K is interpreted as in same units as S0[0] for back-compat.
        from .multi_asset_mc import simulate_correlated_gbm
        paths = simulate_correlated_gbm(
            S0=S0, r=r, q=q_v, sigma=sigma_v, rho=rho, T=T,
            n_steps=1, n_paths=int(kwargs.get("n_paths", 20_000)),
            seed=kwargs.get("seed"),
        )
        S_T = paths[:, -1, :]
        worst_perf = (S_T / S0).min(axis=1)
        payoff = np.maximum(worst_perf * S0[0] - K, 0.0)
        import math
        price = math.exp(-r * T) * float(payoff.mean())
        return float(price), 0.0, None
    return pricer


def _make_worst_of_greeks(opt: str) -> Callable:
    """Bump-reprice Greeks for worst-of. Uses a fixed seed for CRN."""
    pricer = _make_worst_of_pricer(opt)
    def greeks(S, K, r, sigma, T, q, **kwargs):
        kw = {**kwargs, "seed": kwargs.get("seed", 42)}
        p0, _, _ = pricer(S, K, r, sigma, T, q, **kw)
        h_S = max(S * 0.01, 0.01)
        h_v = 0.01
        h_r = 0.0001
        p_S_up, _, _ = pricer(S + h_S, K, r, sigma, T, q, **kw)
        p_S_dn, _, _ = pricer(S - h_S, K, r, sigma, T, q, **kw)
        delta = (p_S_up - p_S_dn) / (2 * h_S)
        gamma = (p_S_up - 2 * p0 + p_S_dn) / (h_S ** 2)
        p_v_up, _, _ = pricer(S, K, r, sigma + h_v, T, q, **kw)
        p_v_dn, _, _ = pricer(S, K, r, sigma - h_v, T, q, **kw)
        vega = (p_v_up - p_v_dn) / (2 * h_v) / 100.0
        p_r_up, _, _ = pricer(S, K, r + h_r, sigma, T, q, **kw)
        p_r_dn, _, _ = pricer(S, K, r - h_r, sigma, T, q, **kw)
        rho_g = (p_r_up - p_r_dn) / (2 * h_r) / 100.0
        dt = 1.0 / 365.0
        if T - dt > 0:
            p_t, _, _ = pricer(S, K, r, sigma, T - dt, q, **kw)
            theta = p_t - p0
        else:
            theta = 0.0
        return {
            "price": p0, "delta": delta, "gamma": gamma,
            "vega": vega, "theta": theta, "rho": rho_g,
        }
    return greeks


def _make_var_swap_pricer() -> Callable:
    """Variance swap fair strike. The "price" returned is K_var (the fair
    volatility strike, in vol-units). std_error and paths are placeholders."""
    import numpy as np
    def pricer(S, K, r, sigma, T, q, **kwargs):
        strikes = kwargs.get("strikes")
        ivs = kwargs.get("ivs")
        if strikes is not None and ivs is not None:
            res = _var_swap.fair_strike_from_strip(
                S=S, r=r, q=q, T=T,
                strikes=np.asarray(strikes, dtype=float),
                ivs=np.asarray(ivs, dtype=float),
            )
        else:
            res = _var_swap.fair_strike_flat(sigma)
        # Encode the fair strike as the "price" — caller reads via method label.
        return float(res.fair_strike_var), 0.0, None
    return pricer


def _make_var_swap_greeks() -> Callable:
    """Variance swap Greeks — minimal. Vega is the dominant Greek; deltas
    are zero by construction (replicating portfolio is delta-hedged).
    """
    def greeks(S, K, r, sigma, T, q, **kwargs):
        return {
            "price": sigma,  # fair strike at flat σ is σ
            "delta": 0.0, "gamma": 0.0,
            "vega": 0.0,    # at the fair strike the swap PV is zero by definition
            "theta": 0.0, "rho": 0.0,
        }
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

    # Tree (LR binomial) — keeps the legacy LR-tree Greeks for callers that
    # explicitly want them (e.g. for parity with prior runs). Discrete cash
    # dividends still route to the FDM-with-DividendSchedule engine because
    # the LR tree cannot reproduce the spot-drop discontinuity.
    if engine == "tree" and option_type in ("american_call", "american_put"):
        opt = option_type.split("_")[1]

        def pricer(S, K, r, sigma, T, q, n_paths=10000, n_steps=90,
                   variance_reduction="none", dividend_schedule=None, **kwargs):
            if QUANTLIB_AVAILABLE and dividend_schedule:
                return quantlib_engine.price_american_discrete_div_ql(
                    S, K, r, sigma, T,
                    dividend_schedule=dividend_schedule, option_type=opt,
                    **_ql_kwargs(kwargs),
                )
            if QUANTLIB_AVAILABLE:
                return quantlib_engine.price_american_ql(
                    S, K, r, sigma, T, q, n_steps=n_steps, option_type=opt,
                    **_ql_kwargs(kwargs),
                )
            return monte_carlo_lsm.price_american(
                S, K, r, sigma, T, q, n_paths, n_steps, variance_reduction,
                option_type=opt,
            )

        def greeks(S, K, r, sigma, T, q, n_steps=201, n_paths=5000,
                   variance_reduction="antithetic", dividend_schedule=None, **kwargs):
            if QUANTLIB_AVAILABLE and dividend_schedule:
                return quantlib_engine.greeks_american_discrete_div_ql(
                    S, K, r, sigma, T,
                    dividend_schedule=dividend_schedule, option_type=opt,
                    **_ql_kwargs(kwargs),
                )
            if QUANTLIB_AVAILABLE:
                # The LR-tree path — explicit opt-in only.
                return quantlib_engine.greeks_ql(
                    S, K, r, sigma, T, q, option_type=opt, is_american=True,
                    n_steps=n_steps,
                    **_ql_kwargs(kwargs),
                )
            return monte_carlo_lsm.greeks_american(
                S, K, r, sigma, T, q, n_paths=n_paths, n_steps=n_steps,
                variance_reduction=variance_reduction, option_type=opt,
            )

        return pricer, greeks, "QuantLib (American, Binomial Tree, forced)"

    # FDM — pricing AND Greeks via FdBlackScholesVanillaEngine. Smooth Greeks
    # for hedging; this is the recommended engine for risk reporting.
    if engine == "fdm" and option_type in ("american_call", "american_put"):
        opt = option_type.split("_")[1]

        def pricer(S, K, r, sigma, T, q, n_paths=10000, n_steps=90,
                   variance_reduction="none", dividend_schedule=None, **kwargs):
            if QUANTLIB_AVAILABLE and dividend_schedule:
                return quantlib_engine.price_american_discrete_div_ql(
                    S, K, r, sigma, T,
                    dividend_schedule=dividend_schedule, option_type=opt,
                    **_ql_kwargs(kwargs),
                )
            if QUANTLIB_AVAILABLE:
                return quantlib_engine.price_american_fdm_ql(
                    S, K, r, sigma, T, q, option_type=opt,
                    **_ql_kwargs(kwargs),
                )
            return monte_carlo_lsm.price_american(
                S, K, r, sigma, T, q, n_paths, n_steps, variance_reduction,
                option_type=opt,
            )

        def greeks(S, K, r, sigma, T, q, n_steps=201, n_paths=5000,
                   variance_reduction="antithetic", dividend_schedule=None, **kwargs):
            if QUANTLIB_AVAILABLE and dividend_schedule:
                return quantlib_engine.greeks_american_discrete_div_ql(
                    S, K, r, sigma, T,
                    dividend_schedule=dividend_schedule, option_type=opt,
                    **_ql_kwargs(kwargs),
                )
            if QUANTLIB_AVAILABLE:
                return quantlib_engine.greeks_american_fdm_ql(
                    S, K, r, sigma, T, q, option_type=opt,
                    **_ql_kwargs(kwargs),
                )
            return monte_carlo_lsm.greeks_american(
                S, K, r, sigma, T, q, n_paths=n_paths, n_steps=n_steps,
                variance_reduction=variance_reduction, option_type=opt,
            )

        return pricer, greeks, "QuantLib (American, FDM)"

    if engine == "analytic":
        # No analytic American closed-form — fall back to QL default. Reserved
        # for future use (e.g. Bjerksund-Stensland).
        return route(option_type)

    if engine in ("tree", "fdm"):
        # Tree/FDM only meaningful for American options; for everything else
        # fall back to the auto-routed default.
        return route(option_type)

    raise ValueError(
        f"Unknown engine: {engine!r}. Valid values: auto|analytic|tree|mc|fdm"
    )
