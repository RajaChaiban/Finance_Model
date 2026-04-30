"""Tests for src.engines.gs_quant_engine and the gs router selector.

No live Marquee calls — credentials are deliberately absent so the
GsQuantNotConfigured guard fires. Coverage:

  * is_gs_available() reflects the env state.
  * Calling pricing without creds raises GsQuantNotConfigured (clear error).
  * Instrument construction works without creds (purely local object build).
  * route_with_engine(..., engine='gs') returns a valid triple for European
    call/put and raises for unsupported product types.
"""

from __future__ import annotations

import pytest

from src.engines import gs_quant_engine
from src.engines.gs_quant_engine import (
    GsQuantNotConfigured,
    _build_european_option,
    is_gs_available,
)
from src.engines.router import route_with_engine


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


def test_is_gs_available_false_without_creds(monkeypatch):
    monkeypatch.delenv("GS_MARQUEE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GS_MARQUEE_CLIENT_SECRET", raising=False)
    assert is_gs_available() is False


def test_is_gs_available_true_with_both_creds(monkeypatch):
    monkeypatch.setenv("GS_MARQUEE_CLIENT_ID", "id")
    monkeypatch.setenv("GS_MARQUEE_CLIENT_SECRET", "secret")
    assert is_gs_available() is True


def test_is_gs_available_false_with_only_one_cred(monkeypatch):
    monkeypatch.setenv("GS_MARQUEE_CLIENT_ID", "id")
    monkeypatch.delenv("GS_MARQUEE_CLIENT_SECRET", raising=False)
    assert is_gs_available() is False


def test_price_raises_clear_error_without_creds(monkeypatch):
    monkeypatch.delenv("GS_MARQUEE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GS_MARQUEE_CLIENT_SECRET", raising=False)
    # Reset session flag in case other tests touched it.
    gs_quant_engine._SESSION_INITIALISED = False

    with pytest.raises(GsQuantNotConfigured) as exc:
        gs_quant_engine.price_european_gs(
            S=4500, K=4500, r=0.045, sigma=0.20, T=0.25, q=0.0,
            option_type="call",
        )
    msg = str(exc.value).lower()
    assert "marquee" in msg
    assert "gs_marquee_client_id" in msg


def test_greeks_raises_clear_error_without_creds(monkeypatch):
    monkeypatch.delenv("GS_MARQUEE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GS_MARQUEE_CLIENT_SECRET", raising=False)
    gs_quant_engine._SESSION_INITIALISED = False

    with pytest.raises(GsQuantNotConfigured):
        gs_quant_engine.greeks_european_gs(
            S=4500, K=4500, r=0.045, sigma=0.20, T=0.25, q=0.0,
            option_type="put",
        )


# ---------------------------------------------------------------------------
# Pure-local instrument build (no Marquee needed)
# ---------------------------------------------------------------------------


def test_build_european_call_no_creds_required():
    """Constructing a gs_quant EqOption is offline; only pricing hits Marquee."""
    opt = _build_european_option(
        strike=4500, expiry_days=90, option_type="call", underlier="SPX",
    )
    d = opt.as_dict()
    assert d["strike_price"] == 4500
    assert str(d["option_type"]) == "Call"
    assert str(d["option_style"]) == "European"
    assert d["underlier"] == "SPX"


def test_build_european_put_uses_put_type():
    opt = _build_european_option(
        strike=100, expiry_days=30, option_type="put", underlier="AAPL UW",
    )
    assert str(opt.as_dict()["option_type"]) == "Put"
    assert opt.as_dict()["underlier"] == "AAPL UW"


def test_build_rejects_invalid_option_type():
    with pytest.raises(ValueError, match="option_type must be"):
        _build_european_option(
            strike=100, expiry_days=30, option_type="straddle", underlier="SPX",
        )


# ---------------------------------------------------------------------------
# Router wiring
# ---------------------------------------------------------------------------


def test_route_with_engine_gs_returns_triple_for_european_call():
    pricer, greeks, desc = route_with_engine("european_call", engine="gs")
    assert callable(pricer)
    assert callable(greeks)
    assert "gs_quant" in desc.lower()


def test_route_with_engine_gs_returns_triple_for_european_put():
    pricer, greeks, desc = route_with_engine("european_put", engine="gs")
    assert callable(pricer)
    assert callable(greeks)
    assert "european" in desc.lower()


def test_route_with_engine_gs_rejects_american():
    with pytest.raises(ValueError, match="european_call / european_put only"):
        route_with_engine("american_call", engine="gs")


def test_route_with_engine_gs_rejects_barrier():
    with pytest.raises(ValueError, match="european_call / european_put only"):
        route_with_engine("knockout_put", engine="gs")


def test_route_with_engine_gs_pricer_propagates_auth_error(monkeypatch):
    """Calling the gs pricer without creds bubbles the GsQuantNotConfigured up."""
    monkeypatch.delenv("GS_MARQUEE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GS_MARQUEE_CLIENT_SECRET", raising=False)
    gs_quant_engine._SESSION_INITIALISED = False

    pricer, _, _ = route_with_engine("european_call", engine="gs")
    with pytest.raises(GsQuantNotConfigured):
        pricer(S=4500, K=4500, r=0.045, sigma=0.20, T=0.25, q=0.0)


def test_route_with_engine_unknown_value_in_error_includes_gs():
    """The error listing valid engines now mentions 'gs'."""
    with pytest.raises(ValueError, match=r"\bgs\b"):
        route_with_engine("european_call", engine="bogus")
