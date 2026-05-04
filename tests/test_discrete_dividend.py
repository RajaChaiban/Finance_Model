"""Discrete dividends for American options.

Single-name American calls have a well-known structural feature: the optimal
exercise is *just before ex-dividend*. A continuous-dividend-yield
approximation smears the dividend across the life of the option and cannot
reproduce this — material mispricing for ITM American calls on dividend-
paying stocks (think AAPL, MSFT, JNJ).

Tests:
  1. New API price_american_discrete_div_ql exists and accepts a schedule.
  2. With ZERO dividends, the discrete pricer matches the standard pricer.
  3. With ONE ex-div before expiry, ITM call price differs from the same
     option priced under a continuous-yield "spread" approximation.
"""

import pytest

pytest.importorskip("QuantLib")
import QuantLib as ql

from src.engines import quantlib_engine


# Standard test contract: 0.5Y ITM call with one ex-div mid-life.
S = 100.0
K = 95.0
r = 0.05
sigma = 0.25
T = 0.5
EVAL_DATE = ql.Date(15, 4, 2024)
EX_DIV_DATE = ql.Date(15, 7, 2024)   # ~3 months from eval
DIV_AMOUNT = 2.00


def test_zero_dividend_schedule_matches_continuous_q_zero():
    """Empty schedule ≡ standard pricer with q=0, within numerical-method noise.

    Discrete-dividend engine uses FDM; standard pricer uses LR binomial tree.
    Different numerical methods → expect agreement to ~bp (small number of
    cents on a $10 option), not float noise.
    """
    p_discrete, _, _ = quantlib_engine.price_american_discrete_div_ql(
        S=S, K=K, r=r, sigma=sigma, T=T,
        dividend_schedule=[],
        option_type="call",
        evaluation_date=EVAL_DATE,
    )
    p_standard, _, _ = quantlib_engine.price_american_ql(
        S=S, K=K, r=r, sigma=sigma, T=T, q=0.0,
        option_type="call", evaluation_date=EVAL_DATE,
    )
    # FDM (200×200) vs LR-201: both ~1bp accuracy. Agreement target ~$0.01.
    assert abs(p_discrete - p_standard) < 0.01, (
        f"FDM=${p_discrete:.4f} vs LR=${p_standard:.4f}"
    )


def test_discrete_dividend_differs_from_continuous_yield_approx():
    """ITM call with one ex-div ≠ ITM call with equivalent continuous yield.

    Continuous-yield "smearing" loses the specific ex-div drop, so the
    early-exercise boundary differs. For an ITM call this is a material gap.
    """
    p_discrete, _, _ = quantlib_engine.price_american_discrete_div_ql(
        S=S, K=K, r=r, sigma=sigma, T=T,
        dividend_schedule=[(EX_DIV_DATE, DIV_AMOUNT)],
        option_type="call",
        evaluation_date=EVAL_DATE,
    )
    # Continuous-yield equivalent: 2.0 / 100 over 0.5 years ≈ 4% continuous.
    q_equivalent = DIV_AMOUNT / S / T
    p_continuous, _, _ = quantlib_engine.price_american_ql(
        S=S, K=K, r=r, sigma=sigma, T=T, q=q_equivalent,
        option_type="call", evaluation_date=EVAL_DATE,
    )
    # Material gap: ≥ 0.5% of premium for an ITM call.
    rel_gap = abs(p_discrete - p_continuous) / p_continuous
    assert rel_gap >= 0.005, (
        f"Discrete vs continuous-yield gap {rel_gap*100:.3f}% suspiciously small. "
        f"Discrete=${p_discrete:.4f}  Continuous=${p_continuous:.4f}"
    )


def test_discrete_dividend_price_is_positive_and_bounded():
    """Sanity: positive, ≤ underlying spot."""
    p, _, _ = quantlib_engine.price_american_discrete_div_ql(
        S=S, K=K, r=r, sigma=sigma, T=T,
        dividend_schedule=[(EX_DIV_DATE, DIV_AMOUNT)],
        option_type="call",
        evaluation_date=EVAL_DATE,
    )
    assert 0 < p < S


def test_two_dividends_lower_than_one():
    """Adding a second ex-div BEFORE expiry lowers the call price further."""
    p_one, _, _ = quantlib_engine.price_american_discrete_div_ql(
        S=S, K=K, r=r, sigma=sigma, T=T,
        dividend_schedule=[(EX_DIV_DATE, DIV_AMOUNT)],
        option_type="call",
        evaluation_date=EVAL_DATE,
    )
    p_two, _, _ = quantlib_engine.price_american_discrete_div_ql(
        S=S, K=K, r=r, sigma=sigma, T=T,
        dividend_schedule=[
            (EX_DIV_DATE, DIV_AMOUNT),
            (ql.Date(15, 9, 2024), DIV_AMOUNT),
        ],
        option_type="call",
        evaluation_date=EVAL_DATE,
    )
    assert p_two < p_one


def test_dividend_after_expiry_is_irrelevant():
    """Dividends paid AFTER expiry should not affect the price."""
    p_no_div, _, _ = quantlib_engine.price_american_discrete_div_ql(
        S=S, K=K, r=r, sigma=sigma, T=T,
        dividend_schedule=[],
        option_type="call",
        evaluation_date=EVAL_DATE,
    )
    p_post_expiry_div, _, _ = quantlib_engine.price_american_discrete_div_ql(
        S=S, K=K, r=r, sigma=sigma, T=T,
        dividend_schedule=[(ql.Date(15, 1, 2025), DIV_AMOUNT)],   # > eval+0.5y
        option_type="call",
        evaluation_date=EVAL_DATE,
    )
    assert abs(p_no_div - p_post_expiry_div) < 1e-6
