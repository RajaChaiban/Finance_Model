"""Latency model for the simulator.

Every submit / cancel / modify request goes through a configurable
distribution before it hits the LOB. Default is log-normal with
mean 15 ms and σ 8 ms — within typical equity-exchange round-trip
bounds. Distributions are seeded so runs are reproducible.

The log-normal parameterisation here uses **desired mean/σ in
milliseconds** as the user-facing knobs. Internally we convert to the
underlying normal's μ, σ via:

    σ²_normal = ln(1 + (S/M)²)
    μ_normal  = ln(M) - σ²_normal / 2

so that ``E[X] = M`` and ``Std[X] = S`` exactly. This is what a finance
practitioner expects when they say "latency averages 15 ms with 8 ms
dispersion."
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import random


@dataclass
class LatencyConfig:
    """Configurable latency parameters in milliseconds.

    All four fields must be ``> 0``. If either σ field is ``0``, the
    corresponding sample is deterministic at the mean (useful for
    stripping latency noise from a test).
    """

    submit_mean_ms: float = 15.0
    submit_sigma_ms: float = 8.0
    cancel_mean_ms: float = 12.0
    cancel_sigma_ms: float = 6.0
    seed: int | None = None

    def __post_init__(self) -> None:
        for name in ("submit_mean_ms", "cancel_mean_ms"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0; got {getattr(self, name)}")
        for name in ("submit_sigma_ms", "cancel_sigma_ms"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0; got {getattr(self, name)}")


def _lognormal_params(mean: float, sigma: float) -> tuple[float, float]:
    """Convert desired (mean, σ) to underlying normal's (μ, σ).

    Returns ``(mu_normal, sigma_normal)`` such that a log-normal sample
    drawn from ``exp(N(mu_normal, sigma_normal²))`` has expectation
    ``mean`` and standard deviation ``sigma``.
    """
    if sigma == 0:
        return (math.log(mean), 0.0)
    var_normal = math.log(1.0 + (sigma / mean) ** 2)
    sigma_normal = math.sqrt(var_normal)
    mu_normal = math.log(mean) - 0.5 * var_normal
    return (mu_normal, sigma_normal)


class LatencyModel:
    """Samples per-event latency from log-normal distributions.

    Reproducible given a seed. Use :meth:`sample_submit_ms` for new
    orders and :meth:`sample_cancel_ms` for cancellations. The realistic
    finding from microstructure research is that cancels are slightly
    faster than submits because exchanges optimise the cancel path.
    """

    def __init__(self, config: LatencyConfig) -> None:
        self.config = config
        self._rng = random.Random(config.seed)
        self._submit_params = _lognormal_params(
            config.submit_mean_ms, config.submit_sigma_ms
        )
        self._cancel_params = _lognormal_params(
            config.cancel_mean_ms, config.cancel_sigma_ms
        )

    def sample_submit_ms(self) -> float:
        mu, sigma = self._submit_params
        if sigma == 0.0:
            return math.exp(mu)
        return self._rng.lognormvariate(mu, sigma)

    def sample_cancel_ms(self) -> float:
        mu, sigma = self._cancel_params
        if sigma == 0.0:
            return math.exp(mu)
        return self._rng.lognormvariate(mu, sigma)

    def sample_submit_sec(self) -> float:
        return self.sample_submit_ms() / 1000.0

    def sample_cancel_sec(self) -> float:
        return self.sample_cancel_ms() / 1000.0


__all__ = ["LatencyConfig", "LatencyModel"]
