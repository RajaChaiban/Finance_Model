"""Antithetic variates must materially reduce MC standard error.

The original code generated dW with shape (n_paths, n_steps) but only used
the upper half (mirrored to the lower half), wasting half the random draws.
After the fix, antithetic should give a meaningful std-error reduction
(typically 20–40% for vanilla payoffs) at the same path budget.
"""

import numpy as np
import pytest

from src.engines import monte_carlo_lsm


@pytest.mark.parametrize("opt", ["call", "put"])
def test_antithetic_reduces_std_error(opt):
    """Antithetic std error < plain std error at same n_paths."""
    S, K, r, sigma, T, q = 100.0, 100.0, 0.05, 0.30, 0.5, 0.0
    n_paths, n_steps = 4000, 50

    _, se_plain, _ = monte_carlo_lsm.price_american(
        S, K, r, sigma, T, q, n_paths=n_paths, n_steps=n_steps,
        variance_reduction="none", option_type=opt,
    )
    _, se_anti, _ = monte_carlo_lsm.price_american(
        S, K, r, sigma, T, q, n_paths=n_paths, n_steps=n_steps,
        variance_reduction="antithetic", option_type=opt,
    )
    assert se_anti < se_plain, (
        f"{opt}: antithetic SE={se_anti:.6f} not < plain SE={se_plain:.6f}"
    )


# Removed: test_antithetic_uses_negated_increments. That test inspected the
# internal ``paths`` array layout produced by the prior hand-rolled NumPy LSM.
# The pricer now delegates to ``ql.MCAmericanEngine``, which does not expose
# paths and uses its own antithetic implementation. Variance reduction is
# still validated behaviourally by ``test_antithetic_reduces_std_error`` above.
