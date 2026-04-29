# tests/test_engine_selection.py
import pytest
from src.engines.router import route_with_engine


def test_american_put_default_engine_is_tree():
    pricer, _, label = route_with_engine("american_put", engine="auto")
    assert "Tree" in label or "Binomial" in label


def test_american_put_force_mc_engine():
    pricer, _, label = route_with_engine("american_put", engine="mc")
    assert "Monte Carlo" in label or "MC" in label
    price, std_err, paths = pricer(100, 100, 0.05, 0.2, 1.0, 0.0, n_paths=5000, n_steps=50)
    assert std_err is not None and std_err > 0
    assert price > 0


def test_unknown_engine_raises():
    with pytest.raises(ValueError):
        route_with_engine("american_put", engine="black_magic")
