"""Tests for bucketed sensitivities: scenario grid, gamma ladder, SensitivityBlock."""
import numpy as np
from src.analysis.sensitivities import (
    compute_scenario_grid,
    compute_gamma_ladder,
    SensitivityBlock,
)


def test_scenario_grid_shape():
    block = compute_scenario_grid(
        option_type="european_call",
        S=100, K=100, r=0.05, sigma=0.2, T=1.0, q=0.0,
        spot_shifts=(-0.10, -0.05, 0, 0.05, 0.10),
        vol_shifts=(-0.05, 0, 0.05),
    )
    assert block.shape == (5, 3)
    assert np.isfinite(block.values).all()


def test_gamma_ladder_centered_on_atm():
    ladder = compute_gamma_ladder(
        option_type="european_call",
        S=100, K=100, r=0.05, sigma=0.2, T=1.0, q=0.0,
    )
    # ATM gamma is the max for vanilla options.
    atm_idx = len(ladder) // 2
    gammas = [pt.gamma for pt in ladder]
    assert max(gammas) - gammas[atm_idx] < max(gammas) * 0.05


def test_sensitivity_block_serialises():
    s = SensitivityBlock(values=[[1.0, 2.0], [3.0, 4.0]],
                         spot_axis=[95, 100], vol_axis=[0.18, 0.22])
    j = s.model_dump_json()
    assert "values" in j
