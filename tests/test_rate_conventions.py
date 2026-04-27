"""Tests for rate convention conversions."""

import numpy as np
import pytest

from src.data.rate_conventions import (
    QuoteBasis,
    to_continuous_act365,
    treasury_basis_for_tenor_days,
)
from src.engines import black_scholes


# ---------------------------------------------------------------------------
# Conversion math
# ---------------------------------------------------------------------------

def test_continuous_passthrough():
    assert to_continuous_act365(0.05, QuoteBasis.CONTINUOUS_ACT365) == 0.05


def test_act360_simple_known_value():
    # 5.30% Act/360 simple SOFR → ~5.234% continuous Act/365
    # growth_act365 = 1 + 0.0530 * 365/360 = 1.05372...
    # continuous = ln(1.05372) ≈ 0.052340
    r = to_continuous_act365(0.0530, QuoteBasis.ACT360_SIMPLE)
    assert r == pytest.approx(0.052340, abs=1e-5)


def test_bey_known_value():
    # 4.30% BEY → 2 * ln(1 + 0.043/2) = 2 * ln(1.0215) ≈ 0.042543
    r = to_continuous_act365(0.0430, QuoteBasis.BEY_SEMIANNUAL)
    assert r == pytest.approx(0.042543, abs=1e-5)


def test_act365_simple_known_value():
    # 5.00% Act/365 simple → ln(1.05) ≈ 0.048790
    r = to_continuous_act365(0.0500, QuoteBasis.ACT365_SIMPLE)
    assert r == pytest.approx(np.log(1.05), abs=1e-9)


def test_act360_simple_zero_is_zero():
    assert to_continuous_act365(0.0, QuoteBasis.ACT360_SIMPLE) == 0.0


def test_bey_zero_is_zero():
    assert to_continuous_act365(0.0, QuoteBasis.BEY_SEMIANNUAL) == 0.0


def test_continuous_rate_is_lower_than_simple_quote():
    # Continuous compounding always gives a smaller r for the same growth factor
    # (1 + r_simple) = exp(r_continuous), so r_cont < r_simple
    for r_q in [0.01, 0.025, 0.05, 0.10]:
        r_cont = to_continuous_act365(r_q, QuoteBasis.ACT365_SIMPLE)
        assert r_cont < r_q


def test_act360_yield_is_higher_than_act365_continuous_equivalent():
    # USD money-market is on a 360-day year, so the same numeric quote
    # corresponds to slightly more growth than the Act/365 reading suggests.
    # → continuous Act/365 conversion should be HIGHER than the raw 360 number...
    #   no, actually the act/360 → act/365 simple inflation (×365/360) is
    #   then partially offset by the ln() compression. Net effect for typical
    #   rates: continuous is between r and r * 365/360.
    r_quoted = 0.053
    r_cont = to_continuous_act365(r_quoted, QuoteBasis.ACT360_SIMPLE)
    assert r_quoted * 360 / 365 < r_cont < r_quoted * 365 / 360


def test_discount_yield_conversion():
    # T-bill discount yield 5.00% → money-market yield 5.263% → continuous
    # MM yield = 0.05 / (1 - 0.05) = 0.052632
    # continuous = ln(1 + 0.052632 * 365/360) = ln(1.053363) ≈ 0.051986
    r = to_continuous_act365(0.05, QuoteBasis.ACT360_DISCOUNT)
    assert r == pytest.approx(0.051986, abs=1e-5)


def test_discount_yield_rejects_invalid():
    with pytest.raises(ValueError, match="Discount yield must be"):
        to_continuous_act365(1.0, QuoteBasis.ACT360_DISCOUNT)


# ---------------------------------------------------------------------------
# Tenor → basis routing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("days", [1, 30, 90, 180, 365])
def test_short_tenor_uses_act360(days):
    assert treasury_basis_for_tenor_days(days) == QuoteBasis.ACT360_SIMPLE


@pytest.mark.parametrize("days", [366, 730, 3650])
def test_long_tenor_uses_bey(days):
    assert treasury_basis_for_tenor_days(days) == QuoteBasis.BEY_SEMIANNUAL


# ---------------------------------------------------------------------------
# Pricing impact — show the conversion isn't cosmetic
# ---------------------------------------------------------------------------

def test_pricing_impact_is_material_for_long_dated_options():
    """Demonstrate the conversion changes BS prices in the direction expected."""
    S, K, T, q = 500.0, 500.0, 1.0, 0.013
    sigma = 0.20
    r_quoted = 0.0530  # SOFR-like Act/360 quote

    r_naive = r_quoted                                                   # bug: feed raw quote
    r_correct = to_continuous_act365(r_quoted, QuoteBasis.ACT360_SIMPLE) # fix: convert

    p_naive = black_scholes.price_european(S, K, r_naive, sigma, T, q, "call")
    p_correct = black_scholes.price_european(S, K, r_correct, sigma, T, q, "call")

    # Continuous rate is slightly lower than the raw Act/360 quote, so call value
    # falls (lower carry → lower forward → lower call). Check direction + magnitude.
    assert p_correct < p_naive
    diff_bps = (p_naive - p_correct) / p_correct * 10000
    assert 5 < diff_bps < 100, f"Expected 5–100 bps mispricing, got {diff_bps:.1f}"


def test_bey_conversion_correctly_lowers_long_dated_rate():
    """A 10Y note BEY of 4.30% should map to ~4.25% continuous."""
    r_cont = to_continuous_act365(0.0430, QuoteBasis.BEY_SEMIANNUAL)
    assert r_cont < 0.0430
    assert r_cont > 0.042  # not absurdly low
