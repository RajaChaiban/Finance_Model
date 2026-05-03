"""Regression tests: silent-drop LEAK on dividend_schedule.

The router's ``_ql_kwargs`` filter forwards only surface-related kwargs
(``vol_handle``, ``use_local_vol_pde``). When a user supplies
``dividend_schedule`` for a non-American product (european / asian /
lookback / barrier), the kwarg is silently dropped — the price is the
same as if no dividend schedule had been supplied. That is a model-risk
footgun. We do not (yet) refuse the request; the API entry points emit
a WARNING instead, and the price is unchanged from the no-schedule
baseline.

These tests pin both behaviours: that the warning fires for non-American
products, and that the *price* path remains unaffected (i.e. silent drop
is preserved — only the silence is fixed).
"""

import logging

import pytest

from src.api.handlers import price_option
from src.api.models import PricingRequest


def _base_request(option_type: str, dividend_schedule=None) -> PricingRequest:
    """Minimal valid request for the chosen product."""
    kwargs = dict(
        option_type=option_type,
        underlying="SPY",
        spot_price=450.0,
        strike_price=450.0,
        days_to_expiration=180,
        risk_free_rate=0.045,
        volatility=0.20,
        dividend_yield=0.015,
        # Keep MC tiny if any path uses it — these tests don't care about
        # MC accuracy, just the warning + price-equality property.
        n_paths=2000,
        n_steps=60,
    )
    if dividend_schedule is not None:
        kwargs["dividend_schedule"] = dividend_schedule
    return PricingRequest(**kwargs)


def _warning_about_div_schedule(records, option_type: str) -> bool:
    """Return True iff a WARNING was logged that mentions both the
    ``dividend_schedule`` token and the offending product type.
    """
    for rec in records:
        if rec.levelno != logging.WARNING:
            continue
        msg = rec.getMessage()
        if "dividend_schedule" in msg and option_type in msg:
            return True
    return False


def test_warning_logged_for_european_with_dividend_schedule(caplog):
    """A european_call with dividend_schedule must emit the silent-drop
    warning AND must price identically to a request without the schedule
    (the engine never received it; the warning is the entire fix)."""
    caplog.set_level(logging.WARNING, logger="src.api.handlers")

    req_no = _base_request("european_call", dividend_schedule=None)
    res_no = price_option(req_no)

    caplog.clear()
    req_yes = _base_request("european_call",
                            dividend_schedule=[["2026-08-15", 1.5]])
    res_yes = price_option(req_yes)

    assert _warning_about_div_schedule(caplog.records, "european_call"), (
        "expected a WARNING mentioning dividend_schedule and european_call; "
        f"got: {[r.getMessage() for r in caplog.records]}"
    )
    # Silent-drop preserved: same price to within numerical noise.
    assert abs(res_yes.price - res_no.price) < 1e-6, (
        f"european_call price changed when dividend_schedule was supplied "
        f"(no-schedule={res_no.price!r}, with-schedule={res_yes.price!r}); "
        f"the engine should not have consumed it."
    )


def test_no_warning_for_american_with_dividend_schedule(caplog):
    """An american_call with dividend_schedule is the legitimate use case —
    no warning should fire."""
    caplog.set_level(logging.WARNING, logger="src.api.handlers")

    req = _base_request("american_call",
                        dividend_schedule=[["2026-08-15", 1.5]])
    _ = price_option(req)

    # Specifically: no warning naming dividend_schedule + american_call.
    offending = [
        r.getMessage() for r in caplog.records
        if r.levelno == logging.WARNING
        and "dividend_schedule" in r.getMessage()
        and "american_call" in r.getMessage()
    ]
    assert not offending, (
        "american_call with dividend_schedule should NOT trigger the "
        f"silent-drop warning, but got: {offending}"
    )


def test_warning_logged_for_asian_with_dividend_schedule(caplog):
    """asian_call is also non-American; the same warning must fire."""
    caplog.set_level(logging.WARNING, logger="src.api.handlers")

    req = _base_request(
        "asian_call",
        dividend_schedule=[["2026-08-15", 1.5]],
    )
    # Asian engine path uses MC — accept whatever price it returns; we
    # only assert the warning was emitted.
    _ = price_option(req)

    assert _warning_about_div_schedule(caplog.records, "asian_call"), (
        "expected a WARNING mentioning dividend_schedule and asian_call; "
        f"got: {[r.getMessage() for r in caplog.records]}"
    )
