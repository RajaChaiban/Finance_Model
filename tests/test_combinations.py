"""
Combination matrix tests: router pricing across all option types and
parameter regimes, plus solver round-trip and error-handling tests.
"""

import pytest
pytest.importorskip("QuantLib")

import math
from itertools import product

from src.engines import router, solver, black_scholes


BASE = dict(S=100.0, K=100.0, r=0.05, sigma=0.20, T=0.5, q=0.02)

GRID = [
    dict(BASE),
    dict(BASE, S=120.0),
    dict(BASE, S=80.0),
    dict(BASE, sigma=0.10),
    dict(BASE, sigma=0.50),
    dict(BASE, T=0.05),
    dict(BASE, T=2.0),
    dict(BASE, q=0.0, r=0.03),
    dict(BASE, q=0.06, r=0.03),
]

OPTION_TYPES = [
    "european_put",
    "european_call",
    "american_put",
    "american_call",
    "knockout_put",
    "knockout_call",
]


def _kwargs_for(option_type, params):
    """Add knockout-specific barrier_level kwargs when needed."""
    extra = {}
    if option_type == "knockout_call":
        # Why: down-and-out barrier below spot keeps the option alive at start.
        extra["barrier_level"] = 80.0
    elif option_type == "knockout_put":
        # Why: up-and-out barrier above spot keeps the option alive at start.
        extra["barrier_level"] = 120.0
    return extra


def _european_baseline(option_type, params):
    """BS European price ignoring American/knockout semantics — for upper bounds."""
    if "call" in option_type:
        return black_scholes.price_european(option_type="call", **params)
    return black_scholes.price_european(option_type="put", **params)


# ------------------------------------------------------------------ #
# Section A — Router pricing matrix (6 types x 9 params).
# ------------------------------------------------------------------ #
class TestRouterMatrix:

    @pytest.mark.parametrize("option_type", OPTION_TYPES)
    @pytest.mark.parametrize("params", GRID)
    def test_price_finite_and_nonneg(self, option_type, params):
        pricer, _, _ = router.route(option_type)
        extra = _kwargs_for(option_type, params)
        price, _, _ = pricer(**params, **extra)
        assert math.isfinite(price)
        assert price >= -1e-9

    @pytest.mark.parametrize("option_type", OPTION_TYPES)
    @pytest.mark.parametrize("params", GRID)
    def test_price_upper_bound(self, option_type, params):
        pricer, _, _ = router.route(option_type)
        extra = _kwargs_for(option_type, params)
        price, _, _ = pricer(**params, **extra)
        S, K, r, T, q = params["S"], params["K"], params["r"], params["T"], params["q"]
        if option_type == "european_call":
            assert price <= S * math.exp(-q * T) + 1e-6
        elif option_type == "european_put":
            assert price <= K * math.exp(-r * T) + 1e-6
        elif option_type == "american_call":
            assert price <= S + 1e-6
        elif option_type == "american_put":
            assert price <= K + 1e-6
        else:
            # Why: knock-out cannot exceed the corresponding vanilla European.
            eur = _european_baseline(option_type, params)
            assert price <= eur + max(0.05, 0.02 * abs(eur))

    @pytest.mark.parametrize("option_type", OPTION_TYPES)
    @pytest.mark.parametrize("params", GRID)
    def test_greek_signs(self, option_type, params):
        _, greeks_fn, _ = router.route(option_type)
        extra = _kwargs_for(option_type, params)
        g = greeks_fn(**params, **extra)
        if "call" in option_type:
            assert -1e-6 <= g["delta"] <= 1.0 + 1e-6
        else:
            assert -1.0 - 1e-6 <= g["delta"] <= 1e-6
        assert g["gamma"] >= -1e-6
        assert g["vega"] >= -1e-6

    @pytest.mark.parametrize("opt_side", ["call", "put"])
    @pytest.mark.parametrize("params", GRID)
    def test_american_ge_european(self, opt_side, params):
        eur_pricer, _, _ = router.route(f"european_{opt_side}")
        amer_pricer, _, _ = router.route(f"american_{opt_side}")
        eur_price, _, _ = eur_pricer(**params)
        amer_price, _, _ = amer_pricer(**params)
        # Why: American holders have at least the European optionality, so
        # American >= European up to engine day-count noise.
        assert amer_price >= eur_price - max(0.05, 0.01 * abs(eur_price)), (
            f"{opt_side}: American=${amer_price:.4f} < European=${eur_price:.4f}"
        )

    @pytest.mark.parametrize("opt_side", ["call", "put"])
    @pytest.mark.parametrize("params", GRID)
    def test_american_ge_bs_baseline(self, opt_side, params):
        """American router price >= BS European baseline (callability invariant)."""
        amer_pricer, _, _ = router.route(f"american_{opt_side}")
        amer_price, _, _ = amer_pricer(**params)
        bs_eur = black_scholes.price_european(option_type=opt_side, **params)
        assert amer_price >= bs_eur - max(0.05, 0.01 * abs(bs_eur))


# ------------------------------------------------------------------ #
# Section B — Solver round-trips. Solve, then re-price and confirm.
# ------------------------------------------------------------------ #
def _within_tol(actual, target):
    return abs(actual - target) < max(0.05, 0.02 * abs(target))


class TestSolverRoundTrips:

    def test_strike_american_put(self):
        S, target, r, sigma, T, q = 100.0, 5.0, 0.05, 0.20, 0.25, 0.0
        sol = solver.solve_for_strike(S, target, r, sigma, T, q=q, option_type="put")
        assert sol.converged
        assert _within_tol(sol.actual_price, target)
        pricer, _, _ = router.route("american_put")
        price, _, _ = pricer(S=S, K=sol.value, r=r, sigma=sigma, T=T, q=q)
        assert _within_tol(price, target)

    def test_strike_american_call(self):
        S, target, r, sigma, T, q = 100.0, 5.0, 0.05, 0.20, 0.25, 0.02
        sol = solver.solve_for_strike(S, target, r, sigma, T, q=q, option_type="call")
        assert sol.converged
        assert _within_tol(sol.actual_price, target)
        pricer, _, _ = router.route("american_call")
        price, _, _ = pricer(S=S, K=sol.value, r=r, sigma=sigma, T=T, q=q)
        assert _within_tol(price, target)

    def test_volatility_american_put(self):
        # Why: round-tripping with the American pricer requires the explicit
        # American IV solver — `solve_for_volatility` now defaults to European
        # (market convention).
        S, K, target, r, T, q = 100.0, 100.0, 4.0, 0.05, 0.25, 0.0
        sol = solver.solve_for_volatility_american(S, K, target, r, T, q=q, option_type="put")
        assert sol.converged
        assert _within_tol(sol.actual_price, target)
        pricer, _, _ = router.route("american_put")
        price, _, _ = pricer(S=S, K=K, r=r, sigma=sol.value, T=T, q=q)
        assert _within_tol(price, target)

    def test_volatility_american_call(self):
        S, K, target, r, T, q = 100.0, 100.0, 4.0, 0.05, 0.25, 0.02
        sol = solver.solve_for_volatility_american(S, K, target, r, T, q=q, option_type="call")
        assert sol.converged
        assert _within_tol(sol.actual_price, target)
        pricer, _, _ = router.route("american_call")
        price, _, _ = pricer(S=S, K=K, r=r, sigma=sol.value, T=T, q=q)
        assert _within_tol(price, target)

    def test_expiration_american_put(self):
        S, K, target, r, sigma, q = 100.0, 100.0, 3.0, 0.05, 0.20, 0.0
        sol = solver.solve_for_expiration(S, K, target, r, sigma, q=q, option_type="put")
        assert sol.converged
        assert _within_tol(sol.actual_price, target)
        # Why: solver returns DAYS; convert back to years for the pricer.
        T_years = sol.value / 365.0
        pricer, _, _ = router.route("american_put")
        price, _, _ = pricer(S=S, K=K, r=r, sigma=sigma, T=T_years, q=q)
        assert _within_tol(price, target)

    def test_expiration_american_call(self):
        S, K, target, r, sigma, q = 100.0, 100.0, 3.0, 0.05, 0.20, 0.02
        sol = solver.solve_for_expiration(S, K, target, r, sigma, q=q, option_type="call")
        assert sol.converged
        assert _within_tol(sol.actual_price, target)
        T_years = sol.value / 365.0
        pricer, _, _ = router.route("american_call")
        price, _, _ = pricer(S=S, K=K, r=r, sigma=sigma, T=T_years, q=q)
        assert _within_tol(price, target)

    def test_barrier_knockout_put(self):
        S, K, target, r, sigma, T, q = 100.0, 100.0, 1.5, 0.05, 0.20, 0.25, 0.0
        sol = solver.solve_for_barrier(
            S, K, target, r, sigma, T, q=q,
            option_type="put", barrier_type="up_and_out",
        )
        assert sol.converged
        assert _within_tol(sol.actual_price, target)
        pricer, _, _ = router.route("knockout_put")
        price, _, _ = pricer(S=S, K=K, r=r, sigma=sigma, T=T, q=q, barrier_level=sol.value)
        assert _within_tol(price, target)

    def test_barrier_knockout_call(self):
        S, K, target, r, sigma, T, q = 100.0, 100.0, 1.5, 0.05, 0.20, 0.25, 0.0
        sol = solver.solve_for_barrier(
            S, K, target, r, sigma, T, q=q,
            option_type="call", barrier_type="down_and_out",
        )
        assert sol.converged
        assert _within_tol(sol.actual_price, target)
        pricer, _, _ = router.route("knockout_call")
        price, _, _ = pricer(S=S, K=K, r=r, sigma=sigma, T=T, q=q, barrier_level=sol.value)
        assert _within_tol(price, target)


# ------------------------------------------------------------------ #
# Section C — Solver error handling.
# ------------------------------------------------------------------ #
class TestSolverErrors:

    def test_unreachable_strike_raises(self):
        # Why: target $1000 on S=100 is impossible for a put or call.
        with pytest.raises(ValueError):
            solver.solve_for_strike(
                S=100.0, target_price=1000.0, r=0.05, sigma=0.20, T=0.5, q=0.0,
                option_type="put",
            )

    def test_unreachable_volatility_raises(self):
        with pytest.raises(ValueError):
            solver.solve_for_volatility(
                S=100.0, K=100.0, target_price=500.0, r=0.05, T=0.5, q=0.0,
                option_type="call",
            )

    def test_unreachable_barrier_raises(self):
        # Why: target above vanilla price is unreachable; knockouts cap at vanilla.
        with pytest.raises(ValueError):
            solver.solve_for_barrier(
                S=100.0, K=100.0, target_price=500.0,
                r=0.05, sigma=0.20, T=0.25, q=0.0,
                option_type="put", barrier_type="up_and_out",
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
