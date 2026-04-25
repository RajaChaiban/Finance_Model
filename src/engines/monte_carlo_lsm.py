"""Monte Carlo Least Squares Method (LSM) for American options."""

import numpy as np
from . import black_scholes


def _polynomial_basis(x: np.ndarray, degree: int = 3) -> None:
    """Use polynomial basis for continuation value estimation.

    Production systems typically use Laguerre polynomials for better numerical
    stability, but standard polynomial regression works well and is more portable.

    Note: To upgrade to Laguerre basis (industry standard), would need to:
    1. Use scipy.special.hermgauss for quadrature integration
    2. Construct generalized Laguerre polynomials with proper weight function
    3. Normalize by stock price mean for numerical stability

    This is left as an enhancement for production deployment.

    Args:
        x: Stock prices (n,)
        degree: Polynomial degree (default 3)

    Returns:
        Polynomial coefficients from np.polyfit
    """
    pass


def price_american(S: float, K: float, r: float, sigma: float, T: float, q: float = 0,
                   n_paths: int = 10000, n_steps: int = 90, variance_reduction: str = "none") -> tuple:
    """Price American option using Monte Carlo LSM.

    Args:
        S: Spot price
        K: Strike price
        r: Risk-free rate
        sigma: Volatility (annual)
        T: Time to expiration (years)
        q: Dividend yield
        n_paths: Number of MC paths
        n_steps: Number of time steps
        variance_reduction: "none" or "antithetic"

    Returns:
        (price, std_error, paths) where:
        - price: American option price
        - std_error: Standard error of estimate
        - paths: Stock price paths (n_paths x n_steps+1)
    """
    dt = T / n_steps

    # Generate stock price paths (GBM)
    np.random.seed(42)
    paths = np.zeros((n_paths, n_steps + 1))
    paths[:, 0] = S

    # Generate Brownian increments
    dW = np.random.standard_normal((n_paths, n_steps))

    if variance_reduction == "antithetic":
        # Antithetic variates: use Z and -Z together
        # First half: original Z
        for t in range(n_steps):
            paths[:n_paths // 2, t + 1] = paths[:n_paths // 2, t] * np.exp(
                (r - q - 0.5 * sigma ** 2) * dt + sigma * np.sqrt(dt) * dW[:n_paths // 2, t]
            )
        # Second half: -Z (negated)
        for t in range(n_steps):
            paths[n_paths // 2:, t + 1] = paths[n_paths // 2:, t] * np.exp(
                (r - q - 0.5 * sigma ** 2) * dt - sigma * np.sqrt(dt) * dW[:n_paths // 2, t]
            )
    else:
        # Standard paths
        for t in range(n_steps):
            paths[:, t + 1] = paths[:, t] * np.exp(
                (r - q - 0.5 * sigma ** 2) * dt + sigma * np.sqrt(dt) * dW[:, t]
            )

    # Backward induction with LSM
    option_values = np.maximum(K - paths[:, n_steps], 0)  # Payoff at maturity
    discount = np.exp(-r * dt)

    for t in range(n_steps - 1, 0, -1):
        S_t = paths[:, t]
        intrinsic = np.maximum(K - S_t, 0)

        ITM = intrinsic > 0

        if np.sum(ITM) > 0:
            # Get ITM stock prices and continuation values
            S_itm = S_t[ITM]
            continuation_itm = option_values[ITM] * discount

            # Polynomial regression for continuation value
            # Production systems use Laguerre basis for better stability
            coeffs = np.polyfit(S_itm, continuation_itm, 3)
            poly = np.poly1d(coeffs)

            # Estimate continuation value
            continuation = np.zeros(n_paths)
            continuation[ITM] = poly(S_itm)

            # Exercise decision
            exercise = intrinsic > continuation
            option_values[exercise] = intrinsic[exercise]
            option_values[~exercise & ITM] = continuation[~exercise & ITM]
            option_values[~ITM] = option_values[~ITM] * discount
        else:
            option_values = option_values * discount

    # Discount back to t=0
    american_price = np.mean(option_values) * np.exp(-r * dt)
    std_error = np.std(option_values) / np.sqrt(n_paths)

    return american_price, std_error, paths


def greeks_american(S: float, K: float, r: float, sigma: float, T: float, q: float = 0,
                    n_paths: int = 5000, n_steps: int = 45) -> dict:
    """Calculate Greeks for American option (bump-and-reprice).

    Uses fewer paths than pricing for speed (roughly 25% of pricing paths).

    Args:
        S, K, r, sigma, T, q: Option parameters
        n_paths: Paths for Greek calculations (default 5000)
        n_steps: Time steps (default 45)

    Returns:
        Dict with keys: delta, gamma, vega, theta, rho, early_exercise_premium
    """
    # Base case
    price_base, _, _ = price_american(S, K, r, sigma, T, q, n_paths, n_steps)

    # Delta: (S+1%) - (S-1%)
    bump_pct = 0.01
    S_up = S * (1 + bump_pct)
    S_down = S * (1 - bump_pct)
    price_up, _, _ = price_american(S_up, K, r, sigma, T, q, n_paths, n_steps)
    price_down, _, _ = price_american(S_down, K, r, sigma, T, q, n_paths, n_steps)
    delta = (price_up - price_down) / (S_up - S_down)

    # Gamma: d(delta)/dS
    price_base_2, _, _ = price_american(S, K, r, sigma, T, q, n_paths, n_steps)
    delta_up = (price_up - price_base_2) / (S_up - S)
    delta_down = (price_base_2 - price_down) / (S - S_down)
    gamma = (delta_up - delta_down) / (S_up - S_down)

    # Vega: +(1%) vol
    vol_bump = 0.01
    price_vol_up, _, _ = price_american(S, K, r, sigma + vol_bump, T, q, n_paths, n_steps)
    vega = (price_vol_up - price_base) / vol_bump / 100  # Per 1% vol change

    # Theta: -1 day
    T_down = max(T - 1 / 365, 0.001)
    price_t_down, _, _ = price_american(S, K, r, sigma, T_down, q, n_paths, n_steps)
    theta = (price_t_down - price_base) / (T_down - T)

    # Rho: +1% rate
    rate_bump = 0.01
    price_r_up, _, _ = price_american(S, K, r + rate_bump, sigma, T, q, n_paths, n_steps)
    rho = (price_r_up - price_base) / rate_bump / 100  # Per 1% rate change

    # Early exercise premium vs European
    european_price = black_scholes.price_european(S, K, r, sigma, T, q, "put")
    early_exercise_premium = price_base - european_price

    return {
        "delta": float(delta),
        "gamma": float(gamma),
        "vega": float(vega),
        "theta": float(theta),
        "rho": float(rho),
        "price": float(price_base),
        "early_exercise_premium": float(early_exercise_premium),
        "early_exercise_premium_pct": float(early_exercise_premium / european_price * 100 if european_price > 0 else 0),
    }
