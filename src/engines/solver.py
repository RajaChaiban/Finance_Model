"""
Inverse pricing solver for derivatives structuring.

Solves: "Given a target price, find the parameter value"
Example: "What strike makes this American put cost exactly $5?"

Enables product design workflows:
- Client budgets: "Design protection for $X max cost"
- Barrier optimization: "Find barrier that keeps cost at target"
- Maturity tuning: "What expiration hits our cost target?"
"""

import numpy as np
from scipy.optimize import brentq, minimize_scalar
from typing import Tuple, Optional, Dict, Any
from . import quantlib_engine, black_scholes


def _solver_steps(T: float) -> int:
    """Step-count policy for American binomial inside the brentq objective.

    Old policy (``int(T * 100)``) gave 5 steps for a 3-week option — far below
    LR-tree convergence. New policy floors at 101 (≈ 4-month business-day
    daily) and scales linearly with T. For 1Y → 252; for 5Y → 1260.
    """
    return max(101, int(252 * T))


class SolverResult:
    """Container for solver solution."""

    def __init__(self, parameter: str, value: float, target_price: float,
                 actual_price: float, iterations: int, converged: bool,
                 original_params: Dict[str, Any]):
        self.parameter = parameter
        self.value = value
        self.target_price = target_price
        self.actual_price = actual_price
        self.iterations = iterations
        self.converged = converged
        self.original_params = original_params
        self.error = abs(actual_price - target_price)
        self.error_pct = (self.error / target_price * 100) if target_price > 0 else 0

    def __repr__(self):
        status = "CONVERGED" if self.converged else "DIVERGED"
        return (
            f"SolverResult({status})\n"
            f"  Parameter: {self.parameter} = {self.value:.4f}\n"
            f"  Target Price: ${self.target_price:.4f}\n"
            f"  Actual Price: ${self.actual_price:.4f}\n"
            f"  Error: ${self.error:.4f} ({self.error_pct:.2f}%)\n"
            f"  Iterations: {self.iterations}"
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for reporting."""
        return {
            "parameter": self.parameter,
            "value": self.value,
            "target_price": self.target_price,
            "actual_price": self.actual_price,
            "error": self.error,
            "error_pct": self.error_pct,
            "iterations": self.iterations,
            "converged": self.converged,
        }


def solve_for_strike(S: float, target_price: float, r: float, sigma: float, T: float,
                     q: float = 0, option_type: str = 'put',
                     bounds: Tuple[float, float] = None,
                     tolerance: float = 0.01) -> SolverResult:
    """
    Find strike price that achieves target option price.

    Args:
        S: Spot price
        target_price: Target option price
        r: Risk-free rate
        sigma: Volatility
        T: Time to expiration (years)
        q: Dividend yield
        option_type: 'call' or 'put'
        bounds: (K_min, K_max) tuple. If None, uses (S*0.5, S*1.5)
        tolerance: Price tolerance for convergence (default $0.01)

    Returns:
        SolverResult with strike price and convergence info

    Example:
        >>> result = solve_for_strike(S=100, target_price=5.0, r=0.05, sigma=0.20, T=0.25)
        >>> print(f"Strike: ${result.value:.2f}")
        Strike: $102.34
    """
    if bounds is None:
        bounds = (S * 0.5, S * 1.5)

    K_min, K_max = bounds

    # Objective function: price - target = 0
    def objective(K: float) -> float:
        try:
            price, _, _ = quantlib_engine.price_american_ql(S, K, r, sigma, T, q,
                                                            _solver_steps(T), option_type)
            return price - target_price
        except:
            return float('inf')

    try:
        # Use Brent's method for robust 1D root finding
        K_solution = brentq(objective, K_min, K_max, xtol=tolerance, maxiter=100)

        # Verify solution
        actual_price, _, _ = quantlib_engine.price_american_ql(S, K_solution, r, sigma, T, q,
                                                               _solver_steps(T), option_type)

        return SolverResult(
            parameter='strike_price',
            value=K_solution,
            target_price=target_price,
            actual_price=actual_price,
            iterations=100,  # Brent's method iterations not exposed, use upper bound
            converged=abs(actual_price - target_price) < tolerance * 2,
            original_params={'S': S, 'r': r, 'sigma': sigma, 'T': T, 'q': q, 'option_type': option_type}
        )

    except ValueError as e:
        raise ValueError(
            f"Solver failed to find strike in range [{K_min:.2f}, {K_max:.2f}]. "
            f"Target price ${target_price:.4f} may be unachievable. Error: {e}"
        )


def solve_for_barrier(S: float, K: float, target_price: float, r: float, sigma: float,
                      T: float, q: float = 0, option_type: str = 'put',
                      barrier_type: str = 'down_and_out',
                      bounds: Tuple[float, float] = None,
                      tolerance: float = 0.01) -> SolverResult:
    """
    Find barrier level that achieves target knockout option price.

    Args:
        S: Spot price
        K: Strike price
        target_price: Target option price
        r: Risk-free rate
        sigma: Volatility
        T: Time to expiration (years)
        q: Dividend yield
        option_type: 'call' or 'put'
        barrier_type: 'down_and_out' or 'up_and_out'
        bounds: (B_min, B_max) tuple. If None, uses intelligent defaults
        tolerance: Price tolerance for convergence

    Returns:
        SolverResult with barrier level and convergence info

    Example:
        >>> result = solve_for_barrier(S=100, K=100, target_price=1.5, r=0.05, sigma=0.20, T=0.25)
        >>> print(f"Barrier: ${result.value:.2f}")
        Barrier: $89.50
    """
    # Intelligent bounds based on option type and barrier type
    if bounds is None:
        if barrier_type == 'down_and_out':
            # Barrier below spot for down-out
            bounds = (S * 0.7, S * 0.99)
        else:  # up_and_out
            # Barrier above spot for up-out
            bounds = (S * 1.01, S * 1.3)

    B_min, B_max = bounds

    def objective(B: float) -> float:
        try:
            price, _, _ = quantlib_engine.price_knockout_ql(S, K, B, r, sigma, T, q, option_type)
            return price - target_price
        except:
            return float('inf')

    try:
        # Validate bounds make sense
        price_at_min = objective(B_min)
        price_at_max = objective(B_max)

        if price_at_min * price_at_max > 0:
            raise ValueError(
                f"Target price ${target_price:.4f} outside achievable range. "
                f"At barrier ${B_min:.2f}: ${target_price - price_at_min:.4f}, "
                f"At barrier ${B_max:.2f}: ${target_price - price_at_max:.4f}"
            )

        # Use Brent's method
        B_solution = brentq(objective, B_min, B_max, xtol=tolerance, maxiter=100)

        # Verify solution
        actual_price, _, _ = quantlib_engine.price_knockout_ql(S, K, B_solution, r, sigma, T, q, option_type)

        return SolverResult(
            parameter='barrier_level',
            value=B_solution,
            target_price=target_price,
            actual_price=actual_price,
            iterations=100,
            converged=abs(actual_price - target_price) < tolerance * 2,
            original_params={'S': S, 'K': K, 'r': r, 'sigma': sigma, 'T': T, 'q': q,
                           'option_type': option_type, 'barrier_type': barrier_type}
        )

    except ValueError as e:
        raise ValueError(
            f"Solver failed to find barrier. {str(e)}"
        )


def solve_for_expiration(S: float, K: float, target_price: float, r: float, sigma: float,
                        q: float = 0, option_type: str = 'put',
                        bounds: Tuple[float, float] = None,
                        tolerance: float = 0.01) -> SolverResult:
    """
    Find time to expiration that achieves target option price.

    Args:
        S: Spot price
        K: Strike price
        target_price: Target option price
        r: Risk-free rate
        sigma: Volatility
        q: Dividend yield
        option_type: 'call' or 'put'
        bounds: (T_min, T_max) in years. If None, uses (1/365, 2.0)
        tolerance: Price tolerance for convergence

    Returns:
        SolverResult with time to expiration (years) and convergence info
    """
    if bounds is None:
        bounds = (1/365, 2.0)  # 1 day to 2 years

    T_min, T_max = bounds

    def objective(T: float) -> float:
        try:
            price, _, _ = quantlib_engine.price_american_ql(S, K, r, sigma, T, q,
                                                            _solver_steps(T), option_type)
            return price - target_price
        except:
            return float('inf')

    try:
        T_solution = brentq(objective, T_min, T_max, xtol=tolerance/1000, maxiter=100)

        # Verify solution
        actual_price, _, _ = quantlib_engine.price_american_ql(S, K, r, sigma, T_solution, q,
                                                               _solver_steps(T_solution), option_type)

        return SolverResult(
            parameter='days_to_expiration',
            value=T_solution * 365,  # Convert to days for user-friendly output
            target_price=target_price,
            actual_price=actual_price,
            iterations=100,
            converged=abs(actual_price - target_price) < tolerance * 2,
            original_params={'S': S, 'K': K, 'r': r, 'sigma': sigma, 'q': q, 'option_type': option_type}
        )

    except ValueError as e:
        raise ValueError(
            f"Solver failed to find expiration in range [{T_min*365:.0f}, {T_max*365:.0f}] days. "
            f"Target price ${target_price:.4f} may be unachievable. Error: {e}"
        )


def solve_for_volatility_european(S: float, K: float, target_price: float, r: float, T: float,
                                  q: float = 0, option_type: str = 'put',
                                  bounds: Tuple[float, float] = None,
                                  tolerance: float = 0.0001) -> SolverResult:
    """
    Market-convention implied volatility via European Black-Scholes inversion.

    Use this for listed-option IV: exchange-quoted vols are defined against
    European BS regardless of the option's exercise style. ~1000x faster than
    the American version because each evaluation is a closed-form CDF call.

    Args:
        S: Spot price
        K: Strike price
        target_price: Market price (mid)
        r: Risk-free rate
        T: Time to expiration (years)
        q: Dividend yield
        option_type: 'call' or 'put'
        bounds: (vol_min, vol_max). If None, uses (0.001, 5.0)
        tolerance: Vol tolerance for convergence (default 1bp)

    Returns:
        SolverResult with implied volatility and convergence info

    Example:
        >>> result = solve_for_volatility_european(S=100, K=100, target_price=5.25,
        ...                                        r=0.05, T=0.25)
        >>> print(f"IV: {result.value:.2%}")
    """
    if bounds is None:
        bounds = (0.001, 5.0)

    vol_min, vol_max = bounds

    def objective(sigma: float) -> float:
        return black_scholes.price_european(S, K, r, sigma, T, q, option_type) - target_price

    # No-arbitrage check: target must lie between intrinsic (vol→0) and asymptote (vol→∞)
    f_min = objective(vol_min)
    f_max = objective(vol_max)
    if f_min * f_max > 0:
        raise ValueError(
            f"Target price ${target_price:.4f} outside achievable range "
            f"[{target_price - f_max:.4f}, {target_price - f_min:.4f}] "
            f"for σ ∈ [{vol_min:.4f}, {vol_max:.4f}]. "
            f"Likely an arbitrage violation in the input price."
        )

    sigma_solution = brentq(objective, vol_min, vol_max, xtol=tolerance, maxiter=100)
    actual_price = black_scholes.price_european(S, K, r, sigma_solution, T, q, option_type)

    # Convergence is on σ (Brent's root-finding); price residual just confirms the inversion
    price_residual = abs(actual_price - target_price)

    return SolverResult(
        parameter='implied_volatility_european',
        value=sigma_solution,
        target_price=target_price,
        actual_price=actual_price,
        iterations=100,
        converged=price_residual < max(target_price * 1e-4, 1e-6),
        original_params={'S': S, 'K': K, 'r': r, 'T': T, 'q': q, 'option_type': option_type}
    )


def solve_for_volatility(S: float, K: float, target_price: float, r: float, T: float,
                         q: float = 0, option_type: str = 'put',
                         bounds: Tuple[float, float] = None,
                         tolerance: float = 0.0001) -> SolverResult:
    """
    Implied volatility from a target price.

    **Default convention is European Black-Scholes**, matching exchange quote
    convention (listed-option IV is defined against European BS regardless of
    the contract's exercise style). Use ``solve_for_volatility_american`` when
    you specifically need American-binomial-implied vol.

    Aliased to :func:`solve_for_volatility_european`.

    Args:
        S: Spot price
        K: Strike price
        target_price: Market price or target price
        r: Risk-free rate
        T: Time to expiration (years)
        q: Dividend yield
        option_type: 'call' or 'put'
        bounds: (vol_min, vol_max). If None, uses (0.001, 5.0)
        tolerance: Price tolerance for convergence (default 1bp)

    Returns:
        SolverResult with implied volatility and convergence info
    """
    return solve_for_volatility_european(
        S, K, target_price, r, T, q=q, option_type=option_type,
        bounds=bounds, tolerance=tolerance,
    )


def solve_for_volatility_american(S: float, K: float, target_price: float, r: float, T: float,
                                  q: float = 0, option_type: str = 'put',
                                  bounds: Tuple[float, float] = None,
                                  tolerance: float = 0.01) -> SolverResult:
    """
    Implied volatility under the American binomial model.

    Niche use: only relevant when you want the σ that makes the American
    binomial (not BS) reproduce a price. Listed-option IV is European-defined
    — for that, use :func:`solve_for_volatility`.

    Args:
        S: Spot price
        K: Strike price
        target_price: Market or target price
        r: Risk-free rate
        T: Time to expiration (years)
        q: Dividend yield
        option_type: 'call' or 'put'
        bounds: (vol_min, vol_max). If None, uses (0.01, 2.0)
        tolerance: Price tolerance for convergence

    Returns:
        SolverResult with American-binomial-implied volatility
    """
    if bounds is None:
        bounds = (0.01, 2.0)

    vol_min, vol_max = bounds

    # Use a meaningful step count regardless of T (LR floor in engine is 51).
    n_steps = max(101, int(252 * T))

    def objective(sigma: float) -> float:
        try:
            price, _, _ = quantlib_engine.price_american_ql(S, K, r, sigma, T, q,
                                                            n_steps, option_type)
            return price - target_price
        except:
            return float('inf')

    try:
        sigma_solution = brentq(objective, vol_min, vol_max, xtol=0.0001, maxiter=100)

        actual_price, _, _ = quantlib_engine.price_american_ql(S, K, r, sigma_solution, T, q,
                                                               n_steps, option_type)

        return SolverResult(
            parameter='volatility_american',
            value=sigma_solution,
            target_price=target_price,
            actual_price=actual_price,
            iterations=100,
            converged=abs(actual_price - target_price) < tolerance * 2,
            original_params={'S': S, 'K': K, 'r': r, 'T': T, 'q': q, 'option_type': option_type}
        )

    except ValueError as e:
        raise ValueError(
            f"Solver failed to find American IV. Target ${target_price:.4f} may be unachievable. "
            f"Error: {e}"
        )
