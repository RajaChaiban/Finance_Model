"""Tests for the latency model.

We test:
  * config validation
  * determinism under a fixed seed
  * empirical mean/σ converges to the configured target
  * the σ=0 degenerate case produces a constant
  * sec helpers are ms/1000
"""

from __future__ import annotations

import math
import statistics

import pytest

from src.esmm.sim.latency import LatencyConfig, LatencyModel, _lognormal_params


class TestLatencyConfig:
    def test_defaults(self) -> None:
        cfg = LatencyConfig()
        assert cfg.submit_mean_ms == 15.0
        assert cfg.submit_sigma_ms == 8.0
        assert cfg.cancel_mean_ms == 12.0
        assert cfg.cancel_sigma_ms == 6.0
        assert cfg.seed is None

    @pytest.mark.parametrize("field", ["submit_mean_ms", "cancel_mean_ms"])
    def test_non_positive_mean_rejected(self, field: str) -> None:
        with pytest.raises(ValueError):
            LatencyConfig(**{field: 0.0})
        with pytest.raises(ValueError):
            LatencyConfig(**{field: -1.0})

    @pytest.mark.parametrize("field", ["submit_sigma_ms", "cancel_sigma_ms"])
    def test_negative_sigma_rejected(self, field: str) -> None:
        with pytest.raises(ValueError):
            LatencyConfig(**{field: -0.1})

    def test_sigma_zero_allowed(self) -> None:
        # σ=0 means deterministic latency. Useful for stripping noise in
        # tests that need a clean event timeline.
        LatencyConfig(submit_sigma_ms=0.0, cancel_sigma_ms=0.0)


class TestLognormalParams:
    def test_recovers_mean_and_std(self) -> None:
        # Sample many draws from N(mu_normal, sigma_normal²) and exponentiate.
        # Empirical mean and σ should match the targets we asked for.
        mu, sigma = _lognormal_params(mean=15.0, sigma=8.0)
        import random

        rng = random.Random(123)
        samples = [math.exp(rng.gauss(mu, sigma)) for _ in range(100_000)]
        emp_mean = statistics.mean(samples)
        emp_std = statistics.stdev(samples)
        # ±3% tolerance is generous given the heavy tail
        assert abs(emp_mean - 15.0) / 15.0 < 0.03
        assert abs(emp_std - 8.0) / 8.0 < 0.05

    def test_sigma_zero_gives_log_mean(self) -> None:
        mu, sigma = _lognormal_params(mean=10.0, sigma=0.0)
        assert sigma == 0.0
        assert math.isclose(mu, math.log(10.0))


class TestLatencyModel:
    def test_determinism(self) -> None:
        m1 = LatencyModel(LatencyConfig(seed=42))
        m2 = LatencyModel(LatencyConfig(seed=42))
        for _ in range(50):
            assert m1.sample_submit_ms() == m2.sample_submit_ms()
            assert m1.sample_cancel_ms() == m2.sample_cancel_ms()

    def test_different_seeds_diverge(self) -> None:
        m1 = LatencyModel(LatencyConfig(seed=1))
        m2 = LatencyModel(LatencyConfig(seed=2))
        diffs = [
            m1.sample_submit_ms() - m2.sample_submit_ms() for _ in range(20)
        ]
        assert any(abs(d) > 1e-6 for d in diffs)

    def test_samples_positive(self) -> None:
        model = LatencyModel(LatencyConfig(seed=7))
        for _ in range(1000):
            assert model.sample_submit_ms() > 0.0
            assert model.sample_cancel_ms() > 0.0

    def test_empirical_mean_matches_target(self) -> None:
        model = LatencyModel(
            LatencyConfig(
                submit_mean_ms=15.0,
                submit_sigma_ms=8.0,
                cancel_mean_ms=12.0,
                cancel_sigma_ms=6.0,
                seed=2026,
            )
        )
        submit = [model.sample_submit_ms() for _ in range(50_000)]
        cancel = [model.sample_cancel_ms() for _ in range(50_000)]
        assert abs(statistics.mean(submit) - 15.0) / 15.0 < 0.03
        assert abs(statistics.mean(cancel) - 12.0) / 12.0 < 0.03

    def test_empirical_std_matches_target(self) -> None:
        model = LatencyModel(
            LatencyConfig(
                submit_mean_ms=20.0,
                submit_sigma_ms=10.0,
                seed=99,
            )
        )
        samples = [model.sample_submit_ms() for _ in range(50_000)]
        assert abs(statistics.stdev(samples) - 10.0) / 10.0 < 0.05

    def test_sigma_zero_is_constant(self) -> None:
        model = LatencyModel(
            LatencyConfig(
                submit_mean_ms=15.0,
                submit_sigma_ms=0.0,
                cancel_mean_ms=10.0,
                cancel_sigma_ms=0.0,
                seed=1,
            )
        )
        for _ in range(20):
            assert math.isclose(model.sample_submit_ms(), 15.0, rel_tol=1e-9)
            assert math.isclose(model.sample_cancel_ms(), 10.0, rel_tol=1e-9)

    def test_sec_helpers(self) -> None:
        model = LatencyModel(LatencyConfig(seed=11))
        # Use seed twice to get the same draw
        ms = LatencyModel(LatencyConfig(seed=11)).sample_submit_ms()
        sec = model.sample_submit_sec()
        assert math.isclose(sec * 1000.0, ms, rel_tol=1e-9)

    def test_cancels_can_be_faster_than_submits(self) -> None:
        # With defaults, cancel mean (12) < submit mean (15)
        model = LatencyModel(LatencyConfig(seed=0))
        n = 10_000
        submit_mean = statistics.mean(model.sample_submit_ms() for _ in range(n))
        cancel_mean = statistics.mean(model.sample_cancel_ms() for _ in range(n))
        assert cancel_mean < submit_mean
