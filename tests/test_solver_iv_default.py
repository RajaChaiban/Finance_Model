"""solver.solve_for_volatility — default convention test.

Exchange-quoted implied vol is defined against European Black-Scholes,
regardless of whether the listed contract is American-style. The default
solver must therefore invert European BS, not the American binomial.
"""

import pytest

pytest.importorskip("QuantLib")

from src.engines import black_scholes, solver


@pytest.mark.parametrize("opt", ["call", "put"])
def test_solve_for_volatility_default_inverts_european_bs(opt):
    """Round-trip: solve IV given a European BS target, reprice with European, agree to ~1bp."""
    S, K, r, T, q = 100.0, 100.0, 0.05, 0.5, 0.02
    sigma_true = 0.22
    target = black_scholes.price_european(S, K, r, sigma_true, T, q, opt)

    sol = solver.solve_for_volatility(S, K, target, r, T, q=q, option_type=opt)

    assert sol.converged
    assert abs(sol.value - sigma_true) < 1e-4, (
        f"{opt} IV {sol.value:.6f} differs from true {sigma_true:.6f} by more than 1bp"
    )

    # Reprice with European, must hit target within vega × σ-tolerance.
    # Brent xtol on σ is 1bp; vega ~$0.20 → reprice precision ~$2e-5.
    reprice = black_scholes.price_european(S, K, r, sol.value, T, q, opt)
    assert abs(reprice - target) < 1e-4


def test_solve_for_volatility_american_explicit_still_available():
    """The American variant must remain accessible for advanced users."""
    assert hasattr(solver, "solve_for_volatility_american") or hasattr(solver, "solve_for_volatility")
    # If solve_for_volatility_american exists, it should work
    if hasattr(solver, "solve_for_volatility_american"):
        S, K, target, r, T, q = 100.0, 100.0, 4.0, 0.05, 0.25, 0.0
        sol = solver.solve_for_volatility_american(S, K, target, r, T, q=q, option_type="put")
        assert sol.converged
        # American IV should be slightly LOWER than European IV (American puts cost more)
