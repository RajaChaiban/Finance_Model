"""Tests for fetch_option_chain — yfinance is mocked."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from src.api.market_data import fetch_option_chain


def _frame(strikes, bids, asks):
    return pd.DataFrame({"strike": strikes, "bid": bids, "ask": asks})


def _stub_ticker(spot: float, expiries_to_chains):
    """Build a stub yf.Ticker.

    expiries_to_chains: dict[expiry_str → (calls_df, puts_df)]
    """

    def _option_chain(expiry_str):
        calls, puts = expiries_to_chains[expiry_str]
        return SimpleNamespace(calls=calls, puts=puts)

    return SimpleNamespace(
        info={"currentPrice": spot},
        options=tuple(expiries_to_chains.keys()),
        option_chain=_option_chain,
        history=lambda **kwargs: pd.DataFrame({"Close": [spot]}),
    )


@pytest.fixture
def today_ref():
    return date(2026, 4, 26)


def test_fetch_option_chain_basic_shape(today_ref):
    """A two-expiry chain with three usable strikes per side is returned cleanly."""
    spot = 500.0
    chains = {
        "2026-06-19": (
            _frame([480.0, 500.0, 520.0], [10.0, 5.0, 1.5], [10.5, 5.4, 1.8]),
            _frame([480.0, 500.0, 520.0], [1.4, 5.0, 21.0], [1.6, 5.4, 21.5]),
        ),
        "2026-09-18": (
            _frame([480.0, 500.0, 520.0], [15.0, 9.0, 4.0], [15.4, 9.5, 4.5]),
            _frame([480.0, 500.0, 520.0], [3.5, 9.0, 25.0], [3.9, 9.5, 25.6]),
        ),
    }
    with patch("src.api.market_data.yf.Ticker", return_value=_stub_ticker(spot, chains)):
        result = fetch_option_chain("SPY", today=today_ref)

    assert len(result) == 2
    keys = list(result.keys())
    assert keys == sorted(keys), "Expiries must be sorted ascending"

    first = result[keys[0]]
    assert {"strike", "bid", "ask", "mid", "option_type", "dte_days", "moneyness"} <= set(first.columns)
    # Each strike has a call and a put → 6 rows.
    assert len(first) == 6
    assert (first["mid"] == 0.5 * (first["bid"] + first["ask"])).all()
    assert (first["dte_days"] > 0).all()


def test_fetch_option_chain_filters_zero_bids(today_ref):
    """Quotes with bid=0 (no live market) must be dropped."""
    spot = 500.0
    chains = {
        "2026-06-19": (
            _frame([480.0, 500.0, 520.0], [10.0, 0.0, 1.5], [10.5, 0.05, 1.8]),
            _frame([480.0, 500.0, 520.0], [1.4, 5.0, 21.0], [1.6, 5.4, 21.5]),
        ),
        "2026-09-18": (
            _frame([480.0, 500.0], [15.0, 9.0], [15.4, 9.5]),
            _frame([480.0, 500.0], [3.5, 9.0], [3.9, 9.5]),
        ),
    }
    with patch("src.api.market_data.yf.Ticker", return_value=_stub_ticker(spot, chains)):
        result = fetch_option_chain("SPY", today=today_ref)

    first = result[date(2026, 6, 19)]
    # The K=500 call had bid=0 → only the K=500 put survives at that strike.
    rows_at_500 = first[first["strike"] == 500.0]
    assert set(rows_at_500["option_type"]) == {"put"}


def test_fetch_option_chain_moneyness_filter(today_ref):
    """Strikes outside ±25 % of spot should be dropped."""
    spot = 500.0
    chains = {
        "2026-06-19": (
            _frame(
                [300.0, 480.0, 500.0, 520.0, 700.0],
                [200.0, 25.0, 5.0, 1.5, 0.5],
                [200.5, 25.5, 5.4, 1.8, 0.7],
            ),
            _frame(
                [300.0, 480.0, 500.0, 520.0, 700.0],
                [0.5, 1.4, 5.0, 21.0, 200.0],
                [0.7, 1.6, 5.4, 21.5, 200.5],
            ),
        ),
        "2026-09-18": (
            _frame([480.0, 500.0], [15.0, 9.0], [15.4, 9.5]),
            _frame([480.0, 500.0], [3.5, 9.0], [3.9, 9.5]),
        ),
    }
    with patch("src.api.market_data.yf.Ticker", return_value=_stub_ticker(spot, chains)):
        result = fetch_option_chain("SPY", today=today_ref)

    first = result[date(2026, 6, 19)]
    assert first["moneyness"].abs().max() <= 0.25 + 1e-12
    assert 300.0 not in set(first["strike"])
    assert 700.0 not in set(first["strike"])


def test_fetch_option_chain_dte_filter(today_ref):
    """Expiries with DTE < min_dte must be dropped, even if they exist."""
    spot = 500.0
    chains = {
        "2026-04-28": (  # only 2 days out — under default min_dte=5
            _frame([500.0], [5.0], [5.4]),
            _frame([500.0], [5.0], [5.4]),
        ),
        "2026-06-19": (
            _frame([480.0, 500.0], [10.0, 5.0], [10.5, 5.4]),
            _frame([480.0, 500.0], [1.4, 5.0], [1.6, 5.4]),
        ),
        "2026-09-18": (
            _frame([480.0, 500.0], [15.0, 9.0], [15.4, 9.5]),
            _frame([480.0, 500.0], [3.5, 9.0], [3.9, 9.5]),
        ),
    }
    with patch("src.api.market_data.yf.Ticker", return_value=_stub_ticker(spot, chains)):
        result = fetch_option_chain("SPY", today=today_ref)

    assert date(2026, 4, 28) not in result
    assert date(2026, 6, 19) in result
    assert date(2026, 9, 18) in result


def test_fetch_option_chain_max_expiries(today_ref):
    """Cap on number of expiries returned must be respected."""
    spot = 500.0
    chains = {
        "2026-06-19": (
            _frame([500.0], [5.0], [5.4]),
            _frame([500.0], [5.0], [5.4]),
        ),
        "2026-07-17": (
            _frame([500.0], [6.0], [6.4]),
            _frame([500.0], [6.0], [6.4]),
        ),
        "2026-08-21": (
            _frame([500.0], [7.0], [7.4]),
            _frame([500.0], [7.0], [7.4]),
        ),
        "2026-09-18": (
            _frame([500.0], [8.0], [8.4]),
            _frame([500.0], [8.0], [8.4]),
        ),
    }
    with patch("src.api.market_data.yf.Ticker", return_value=_stub_ticker(spot, chains)):
        result = fetch_option_chain("SPY", today=today_ref, max_expiries=2)

    assert len(result) == 2
    assert list(result.keys()) == [date(2026, 6, 19), date(2026, 7, 17)]


def test_fetch_option_chain_empty_returns_empty(today_ref):
    """No expiries at all → empty dict, no exception."""
    spot = 500.0
    stub = SimpleNamespace(
        info={"currentPrice": spot},
        options=tuple(),
        option_chain=lambda _e: SimpleNamespace(calls=pd.DataFrame(), puts=pd.DataFrame()),
        history=lambda **kwargs: pd.DataFrame({"Close": [spot]}),
    )
    with patch("src.api.market_data.yf.Ticker", return_value=stub):
        result = fetch_option_chain("SPY", today=today_ref)

    assert result == {}
