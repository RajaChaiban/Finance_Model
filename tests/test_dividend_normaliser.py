"""Yahoo's dividendYield field has historically returned percent on some
endpoints, decimal on others — silently. A naive consumer can mis-price by
100x. The normaliser detects scale and returns decimal.

Heuristic:
  - Decimal yields for normal equities are < 0.20 (rarely > 15% even for REITs)
  - Percent yields are typically 0.5 to 15.0
  - Anything > 0.20 looks like percent → divide by 100
  - Anything ≥ 1.0 (e.g. 1.5 meaning 1.5%) is unambiguously percent
"""

import pytest

from src.api.market_data import normalise_dividend_yield


def test_decimal_passthrough():
    """Already-decimal values < 0.20 pass through unchanged."""
    assert normalise_dividend_yield(0.015) == 0.015
    assert normalise_dividend_yield(0.04) == 0.04
    assert normalise_dividend_yield(0.0) == 0.0


def test_percent_above_0_20_treated_as_percent():
    """Values clearly in percent space: divide by 100."""
    assert normalise_dividend_yield(1.5) == pytest.approx(0.015)
    assert normalise_dividend_yield(4.0) == pytest.approx(0.04)
    assert normalise_dividend_yield(8.5) == pytest.approx(0.085)


def test_high_decimal_borderline_treated_as_percent():
    """0.5 — almost certainly 0.5% (decimal would be 50% yield, absurd)."""
    assert normalise_dividend_yield(0.5) == pytest.approx(0.005)


def test_negative_rejected():
    with pytest.raises(ValueError):
        normalise_dividend_yield(-0.01)


def test_absurd_high_rejected():
    """Values that are ambiguous AND too large flag a data error."""
    with pytest.raises(ValueError):
        normalise_dividend_yield(50.0)


def test_none_returns_zero():
    """No-dividend stocks return 0 — match production fallback."""
    assert normalise_dividend_yield(None) == 0.0
