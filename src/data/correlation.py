"""Implied correlation surface from index vs. component IVs.

Bakshi-Kapadia-Madan (BKM)-style implied correlation. For an index of N
components with weights w_i, individual variances σ_i² and pairwise
correlations ρ_ij, index variance is:

    σ_index² = Σ_i w_i² σ_i² + Σ_{i≠j} w_i w_j σ_i σ_j ρ_ij

If we assume an equicorrelation structure (all off-diagonal ρ_ij ≡ ρ̄),
this collapses to:

    σ_index² = Σ_i w_i² σ_i² + ρ̄ · (Σ_i w_i σ_i)² − ρ̄ · Σ_i w_i² σ_i²

Solving for ρ̄ yields the average implied pairwise correlation. This is what
desks quote as "the implied correlation" — it's an aggregate, not a surface,
but it's the load-bearing number for dispersion / correlation trades.

For a true surface, repeat for each maturity slice and stack.

Used by:
- Multi-asset MC (worst-of pricing) — sanity-check the input correlation
  matrix against what the listed market is implying.
- Strategist's dispersion-trade rule (when added).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass
class ImpliedCorrelationResult:
    rho_bar: float                    # average implied pairwise correlation
    sigma_index_implied: float        # input
    sigma_index_zero_corr: float      # what the index σ would be if ρ̄=0
    components: int
    method: str = "BKM-equicorrelation"

    def to_dict(self) -> dict:
        return {
            "rho_bar": self.rho_bar,
            "sigma_index_implied": self.sigma_index_implied,
            "sigma_index_zero_corr": self.sigma_index_zero_corr,
            "components": self.components,
            "method": self.method,
        }


def implied_correlation(
    *,
    sigma_index: float,
    weights: Sequence[float],
    sigma_components: Sequence[float],
) -> ImpliedCorrelationResult:
    """Solve for ρ̄ given index σ and component σs / weights.

    Parameters
    ----------
    sigma_index : float
        ATM IV of the index (decimal).
    weights : Sequence[float]
        Component index weights (must sum ≈ 1).
    sigma_components : Sequence[float]
        ATM IV of each component (decimal), aligned with weights.

    Returns
    -------
    ImpliedCorrelationResult — ρ̄ and diagnostics.

    Notes
    -----
    ρ̄ can exceed 1 or fall below the off-diagonal floor of −1/(N−1) when the
    index is mis-priced relative to its components, or when the weights are
    stale. We do NOT clamp — surfacing the unclamped value is the diagnostic
    signal a structurer needs.
    """
    w = np.asarray(weights, dtype=float)
    s = np.asarray(sigma_components, dtype=float)
    if len(w) != len(s):
        raise ValueError("weights and sigma_components must align in length")
    if abs(w.sum() - 1.0) > 1e-3:
        raise ValueError(f"weights must sum to ~1.0, got {w.sum():.4f}")

    # σ_index_zero_corr = √(Σ w_i² σ_i²)
    diag_var = float(np.sum((w * s) ** 2))
    sigma_zero = float(np.sqrt(diag_var))

    # weighted_sum_sigma = Σ w_i σ_i
    cross_basis = float(np.sum(w * s))

    # Solve  σ_idx² = diag + ρ̄ · (cross_basis² − diag)
    #   ρ̄ = (σ_idx² − diag) / (cross_basis² − diag)
    denom = cross_basis ** 2 - diag_var
    if abs(denom) < 1e-12:
        rho_bar = float("nan")
    else:
        rho_bar = (sigma_index ** 2 - diag_var) / denom

    return ImpliedCorrelationResult(
        rho_bar=rho_bar,
        sigma_index_implied=sigma_index,
        sigma_index_zero_corr=sigma_zero,
        components=len(w),
    )


def equicorrelation_matrix(rho_bar: float, n: int) -> np.ndarray:
    """Build an n×n correlation matrix with diag=1, off-diag=ρ̄."""
    M = np.full((n, n), rho_bar, dtype=float)
    np.fill_diagonal(M, 1.0)
    return M
