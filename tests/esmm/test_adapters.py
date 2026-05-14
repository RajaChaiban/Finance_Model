"""Tests for the DataAdapter Protocol + SyntheticAdapter + YFinanceAdapter.

External API (yfinance) is mocked so the suite runs offline / in CI.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.esmm.adapters import DataAdapter, SyntheticAdapter, YFinanceAdapter
from src.esmm.adapters.base import DataAdapter as DataAdapterProtocol
from src.esmm.schemas import OrderBookSnapshot


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_synthetic_adapter_satisfies_protocol():
    adapter = SyntheticAdapter()
    assert isinstance(adapter, DataAdapterProtocol)
    assert adapter.name == "synthetic"


def test_yfinance_adapter_satisfies_protocol():
    adapter = YFinanceAdapter()
    assert isinstance(adapter, DataAdapterProtocol)
    assert adapter.name == "yfinance"


# ---------------------------------------------------------------------------
# SyntheticAdapter
# ---------------------------------------------------------------------------


def test_synthetic_adapter_replay_sizes_path_by_window():
    adapter = SyntheticAdapter(dt_seconds=1.0, seed=42)
    start = datetime(2026, 1, 1, 9, 30)
    end = start + timedelta(seconds=100)
    snaps = list(adapter.replay("SPY", start, end))
    assert len(snaps) == 100
    assert all(s.symbol == "SPY" for s in snaps)


def test_synthetic_adapter_replay_is_deterministic():
    a = SyntheticAdapter(seed=7)
    b = SyntheticAdapter(seed=7)
    start = datetime(2026, 1, 1)
    end = start + timedelta(seconds=20)
    snaps_a = list(a.replay("SPY", start, end))
    snaps_b = list(b.replay("SPY", start, end))
    assert [(s.best_bid, s.best_ask) for s in snaps_a] == \
           [(s.best_bid, s.best_ask) for s in snaps_b]


def test_synthetic_adapter_zero_window_yields_one_snap():
    """Edge case: start == end should still produce a single snap."""
    adapter = SyntheticAdapter()
    t = datetime(2026, 1, 1)
    snaps = list(adapter.replay("SPY", t, t))
    assert len(snaps) == 1


def test_synthetic_adapter_stream_is_iterable_and_increments_ts():
    adapter = SyntheticAdapter(seed=1, dt_seconds=2.0)
    stream = adapter.stream("AAPL")
    first = next(stream)
    second = next(stream)
    assert second.ts > first.ts
    assert pytest.approx(second.ts - first.ts) == 2.0
    assert first.symbol == "AAPL"


# ---------------------------------------------------------------------------
# YFinanceAdapter — replay (mocked yfinance)
# ---------------------------------------------------------------------------


def _mock_bars_df(closes: list[float], minute_starts: list[datetime]):
    """Build a DataFrame-like object iterable as (ts, row) pairs."""
    import pandas as pd

    df = pd.DataFrame(
        {"Close": closes},
        index=pd.to_datetime(minute_starts),
    )
    return df


def test_yfinance_adapter_replay_builds_one_snapshot_per_bar():
    base = datetime(2026, 5, 14, 14, 30, tzinfo=timezone.utc)
    closes = [500.0, 500.5, 499.5, 501.0, 502.0]
    minute_starts = [base + timedelta(minutes=i) for i in range(5)]
    fake_df = _mock_bars_df(closes, minute_starts)

    fake_yf = MagicMock()
    fake_yf.Ticker.return_value.history.return_value = fake_df

    with patch.dict("sys.modules", {"yfinance": fake_yf}):
        adapter = YFinanceAdapter(half_spread_bps=5.0, synthetic_size=300.0)
        snaps = list(
            adapter.replay(
                "SPY",
                datetime(2026, 5, 14, tzinfo=timezone.utc),
                datetime(2026, 5, 15, tzinfo=timezone.utc),
            )
        )

    assert len(snaps) == 5
    for snap, close in zip(snaps, closes):
        mid = 0.5 * (snap.best_bid + snap.best_ask)
        assert mid == pytest.approx(close, rel=2e-4)  # within one tick
        assert snap.best_bid_size == 300.0
        assert snap.best_ask_size == 300.0
        assert snap.symbol == "SPY"
    # Timestamps strictly increasing
    assert all(snaps[i].ts < snaps[i + 1].ts for i in range(len(snaps) - 1))


def test_yfinance_adapter_replay_raises_on_empty_dataframe():
    import pandas as pd

    fake_yf = MagicMock()
    fake_yf.Ticker.return_value.history.return_value = pd.DataFrame()
    with patch.dict("sys.modules", {"yfinance": fake_yf}):
        adapter = YFinanceAdapter()
        with pytest.raises(ValueError, match="no.*bars"):
            list(adapter.replay("SPY", datetime(2026, 1, 1), datetime(2026, 1, 2)))


def test_yfinance_adapter_replay_skips_nan_closes():
    """NaN / zero closes (e.g. holiday minute) must be silently dropped, not
    propagated as a malformed snapshot."""
    import math
    import pandas as pd

    base = datetime(2026, 5, 14, 14, 30, tzinfo=timezone.utc)
    df = pd.DataFrame(
        {"Close": [500.0, math.nan, 0.0, 501.0]},
        index=pd.to_datetime([base + timedelta(minutes=i) for i in range(4)]),
    )

    fake_yf = MagicMock()
    fake_yf.Ticker.return_value.history.return_value = df
    with patch.dict("sys.modules", {"yfinance": fake_yf}):
        adapter = YFinanceAdapter()
        snaps = list(
            adapter.replay(
                "SPY", datetime(2026, 5, 14, tzinfo=timezone.utc),
                datetime(2026, 5, 15, tzinfo=timezone.utc)
            )
        )
    assert len(snaps) == 2  # the two valid closes only


def test_yfinance_adapter_replay_output_passes_schema_validator():
    """End-to-end check: every yfinance-built snapshot must round-trip through
    the validator (proves sort order + non-empty sides)."""
    import pandas as pd

    base = datetime(2026, 5, 14, 14, 30, tzinfo=timezone.utc)
    closes = [500.0, 500.5, 499.5]
    df = pd.DataFrame(
        {"Close": closes},
        index=pd.to_datetime([base + timedelta(minutes=i) for i in range(3)]),
    )
    fake_yf = MagicMock()
    fake_yf.Ticker.return_value.history.return_value = df
    with patch.dict("sys.modules", {"yfinance": fake_yf}):
        adapter = YFinanceAdapter(levels=3)
        snaps = list(
            adapter.replay(
                "SPY", datetime(2026, 5, 14, tzinfo=timezone.utc),
                datetime(2026, 5, 15, tzinfo=timezone.utc)
            )
        )
    for s in snaps:
        # Re-construct: if it raises, validator caught a violation.
        OrderBookSnapshot(ts=s.ts, symbol=s.symbol, bids=s.bids, asks=s.asks)


# ---------------------------------------------------------------------------
# YFinanceAdapter — stream (mocked yfinance + time.sleep)
# ---------------------------------------------------------------------------


def test_yfinance_adapter_stream_yields_real_bid_ask_when_available():
    fake_info = SimpleNamespace(bid=499.99, ask=500.01, last_price=500.0)
    fake_yf = MagicMock()
    fake_yf.Ticker.return_value.fast_info = fake_info

    with patch.dict("sys.modules", {"yfinance": fake_yf}), \
         patch("src.esmm.adapters.yfinance_adapter.time.sleep") as sleep_mock:
        adapter = YFinanceAdapter(synthetic_size=150.0)
        gen = adapter.stream("SPY", poll_seconds=0.0, max_snaps=3)
        snaps = list(gen)

    assert len(snaps) == 3
    for s in snaps:
        assert s.best_bid == pytest.approx(499.99)
        assert s.best_ask == pytest.approx(500.01)
        assert s.best_bid_size == 150.0
    # sleep happens after every yield except the one that hit max_snaps
    assert sleep_mock.call_count == 2


def test_yfinance_adapter_stream_falls_back_to_last_price_when_no_quote():
    fake_info = SimpleNamespace(bid=0.0, ask=0.0, last_price=500.0)
    fake_yf = MagicMock()
    fake_yf.Ticker.return_value.fast_info = fake_info

    with patch.dict("sys.modules", {"yfinance": fake_yf}), \
         patch("src.esmm.adapters.yfinance_adapter.time.sleep"):
        adapter = YFinanceAdapter()
        snaps = list(adapter.stream("SPY", poll_seconds=0.0, max_snaps=1))
    assert len(snaps) == 1
    mid = 0.5 * (snaps[0].best_bid + snaps[0].best_ask)
    assert mid == pytest.approx(500.0, rel=2e-4)
