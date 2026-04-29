"""Tests for the Vol Desk movers payload — ranking, structure, caching.

Patches ``fetch_movers_batch`` directly so tests are robust against the
``sys.modules['yfinance']`` pollution introduced by test_engines.py.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from src.data import movers


@pytest.fixture(autouse=True)
def _clear_cache():
    movers._movers_cache.clear()
    yield
    movers._movers_cache.clear()


def _series_for_all(closes_default):
    return {t: list(closes_default) for t in movers.INDEX_TICKERS + movers.DEFAULT_UNIVERSE}


def test_payload_structure():
    closes = list(np.linspace(100, 110, 32))
    series = _series_for_all(closes)

    with patch("src.data.movers.fetch_movers_batch", return_value=series):
        out = movers.get_movers_payload()

    assert set(out.keys()) >= {"as_of", "indices", "gainers", "losers", "volatile"}
    assert isinstance(out["indices"], list)
    assert isinstance(out["gainers"], list)
    assert isinstance(out["losers"], list)
    assert isinstance(out["volatile"], list)


def test_ranking_correctness():
    """Gainers sort by change_pct desc; losers asc; volatile by hv30 desc."""
    flat = [100.0] * 32
    rising = [100.0] * 30 + [101.0, 110.0]
    falling = [100.0] * 30 + [101.0, 90.0]
    spiky_then_flat = [100.0, 80.0] * 15 + [100.0, 100.0]

    series = {t: flat for t in movers.INDEX_TICKERS}
    series["NVDA"] = rising
    series["TSLA"] = falling
    series["AAPL"] = spiky_then_flat
    for t in movers.DEFAULT_UNIVERSE:
        series.setdefault(t, flat)

    with patch("src.data.movers.fetch_movers_batch", return_value=series):
        out = movers.get_movers_payload()

    assert out["gainers"][0]["ticker"] == "NVDA"
    assert out["gainers"][0]["change_pct"] > 0
    assert out["losers"][0]["ticker"] == "TSLA"
    assert out["losers"][0]["change_pct"] < 0
    assert out["volatile"][0]["ticker"] == "AAPL"
    assert out["volatile"][0]["hv30"] > 0


def test_indices_returned_in_canonical_order():
    closes = list(np.linspace(100, 102, 32))
    series = _series_for_all(closes)

    with patch("src.data.movers.fetch_movers_batch", return_value=series):
        out = movers.get_movers_payload()

    returned = [r["ticker"] for r in out["indices"]]
    assert returned == movers.INDEX_TICKERS


def test_cache_hit_skips_fetch():
    """A second call within the TTL must not invoke fetch_movers_batch again."""
    closes = list(np.linspace(100, 105, 32))
    series = _series_for_all(closes)

    with patch("src.data.movers.fetch_movers_batch", return_value=series) as mock_fetch:
        first = movers.get_movers_payload()
        second = movers.get_movers_payload()

    assert mock_fetch.call_count == 1
    assert first["source"] == "api"
    assert second["source"] == "cache"
    assert first["gainers"] == second["gainers"]


def test_fetch_failure_returns_empty_lists():
    """If the batch fetch returns nothing, the payload is structurally valid."""
    empty = {t: [] for t in movers.INDEX_TICKERS + movers.DEFAULT_UNIVERSE}

    with patch("src.data.movers.fetch_movers_batch", return_value=empty):
        out = movers.get_movers_payload()

    assert out["indices"] == []
    assert out["gainers"] == []
    assert out["losers"] == []
    assert out["volatile"] == []


def test_summarize_handles_short_history():
    """Tickers with <2 closes are dropped silently."""
    closes_long = list(np.linspace(100, 110, 32))
    series = {t: closes_long for t in movers.INDEX_TICKERS + movers.DEFAULT_UNIVERSE}
    series["AAPL"] = [100.0]  # only one close

    with patch("src.data.movers.fetch_movers_batch", return_value=series):
        out = movers.get_movers_payload()

    tickers = {s["ticker"] for s in out["gainers"] + out["losers"] + out["volatile"]}
    assert "AAPL" not in tickers


def test_hv30_excluded_when_unavailable():
    """Tickers with <31 closes have hv30=None and don't appear in volatile list."""
    long_ = list(np.linspace(100, 110, 32))
    short = list(np.linspace(100, 105, 5))

    series = {t: long_ for t in movers.INDEX_TICKERS}
    series["AAPL"] = short
    for t in movers.DEFAULT_UNIVERSE:
        series.setdefault(t, long_)

    with patch("src.data.movers.fetch_movers_batch", return_value=series):
        out = movers.get_movers_payload()

    aapl = next((s for s in out["gainers"] + out["losers"] if s["ticker"] == "AAPL"), None)
    if aapl is not None:
        assert aapl["hv30"] is None
    assert "AAPL" not in {s["ticker"] for s in out["volatile"]}
