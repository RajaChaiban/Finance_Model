"""Engine functions accept an evaluation_date parameter for aged trades.

Hardcoding ql.Date(1,1,2025) prevents:
  - Re-marking aged trades at past valuation dates
  - Theta over weekends (Friday → Monday should be 3 days, not 1)
  - Holiday-adjusted maturity rolls

The fix: every engine function takes ``evaluation_date: Optional[ql.Date] = None``
and defaults to today.
"""

import pytest

pytest.importorskip("QuantLib")
import QuantLib as ql

from src.engines import quantlib_engine


def test_european_pricing_accepts_evaluation_date():
    """Pricing under an explicit eval date must work and produce sensible output."""
    eval_date = ql.Date(15, 6, 2024)
    res = quantlib_engine.greeks_ql(
        S=100.0, K=100.0, r=0.05, sigma=0.20, T=0.5, q=0.0,
        option_type="call", is_american=False,
        evaluation_date=eval_date,
    )
    assert res["price"] > 0
    assert 0 < res["delta"] < 1


def test_american_pricing_accepts_evaluation_date():
    eval_date = ql.Date(15, 6, 2024)
    price, _, _ = quantlib_engine.price_american_ql(
        S=100.0, K=100.0, r=0.05, sigma=0.20, T=0.5, q=0.02,
        n_steps=101, option_type="put",
        evaluation_date=eval_date,
    )
    assert price > 0


def test_knockout_pricing_accepts_evaluation_date():
    eval_date = ql.Date(15, 6, 2024)
    price, _, _ = quantlib_engine.price_knockout_ql(
        S=100.0, K=100.0, B=80.0, r=0.05, sigma=0.20, T=0.5, q=0.0,
        option_type="call",
        evaluation_date=eval_date,
    )
    assert price > 0


def test_evaluation_date_independence():
    """Same (S, K, r, σ, T, q) must give same price regardless of eval-date.

    Black-Scholes is time-translation-invariant: only T matters, not the
    absolute calendar date. This is a sanity check that we're not leaking
    eval-date into the computation.
    """
    res_a = quantlib_engine.greeks_ql(
        S=100.0, K=100.0, r=0.05, sigma=0.20, T=0.5, q=0.0,
        option_type="call", is_american=False,
        evaluation_date=ql.Date(15, 6, 2024),
    )
    res_b = quantlib_engine.greeks_ql(
        S=100.0, K=100.0, r=0.05, sigma=0.20, T=0.5, q=0.0,
        option_type="call", is_american=False,
        evaluation_date=ql.Date(2, 11, 2026),
    )
    assert abs(res_a["price"] - res_b["price"]) < 1e-9


def test_default_evaluation_date_uses_today():
    """No eval_date arg → uses today (settable via global)."""
    res = quantlib_engine.greeks_ql(
        S=100.0, K=100.0, r=0.05, sigma=0.20, T=0.5, q=0.0,
        option_type="call", is_american=False,
    )
    today = ql.Date.todaysDate()
    assert ql.Settings.instance().evaluationDate == today
    assert res["price"] > 0
