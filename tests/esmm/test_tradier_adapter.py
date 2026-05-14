"""Tests for TradierAdapter.

httpx is mocked via the `respx` plugin (already in dev deps; used elsewhere
in the repo). Tests never touch the network and don't need TRADIER_TOKEN.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest
import respx

from src.esmm.adapters import TradierAdapter
from src.esmm.adapters.base import DataAdapter as DataAdapterProtocol
from src.esmm.schemas import OrderBookSnapshot


SANDBOX = "https://sandbox.tradier.com/v1"


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_tradier_adapter_satisfies_protocol():
    adapter = TradierAdapter(token="x")
    assert isinstance(adapter, DataAdapterProtocol)
    assert adapter.name == "tradier"


# ---------------------------------------------------------------------------
# Replay (timesales)
# ---------------------------------------------------------------------------


@respx.mock
def test_tradier_adapter_replay_builds_one_snapshot_per_bar():
    payload = {
        "series": {
            "data": [
                {"time": "2026-05-14T14:30:00", "close": 500.00, "open": 499.9,
                 "high": 500.2, "low": 499.8, "volume": 1000},
                {"time": "2026-05-14T14:31:00", "close": 500.50, "open": 500.0,
                 "high": 500.7, "low": 500.0, "volume": 1500},
                {"time": "2026-05-14T14:32:00", "close": 499.75, "open": 500.5,
                 "high": 500.6, "low": 499.7, "volume": 1200},
            ]
        }
    }
    respx.get(f"{SANDBOX}/markets/timesales").mock(
        return_value=httpx.Response(200, json=payload)
    )
    adapter = TradierAdapter(token="sandbox-token", synthetic_size=300.0)
    snaps = list(
        adapter.replay(
            "SPY",
            datetime(2026, 5, 14, 14, 30),
            datetime(2026, 5, 14, 14, 33),
        )
    )
    assert len(snaps) == 3
    for snap, expected_close in zip(snaps, (500.00, 500.50, 499.75)):
        mid = 0.5 * (snap.best_bid + snap.best_ask)
        assert mid == pytest.approx(expected_close, abs=0.02)
        assert snap.best_bid_size == 300.0
        assert snap.best_ask_size == 300.0
    assert snaps[0].ts < snaps[1].ts < snaps[2].ts


@respx.mock
def test_tradier_adapter_replay_handles_single_bar_as_dict():
    """Tradier returns `data` as a dict (not a list) when only one bar matches."""
    payload = {
        "series": {
            "data": {"time": "2026-05-14T14:30:00", "close": 500.00,
                     "open": 499.9, "high": 500.2, "low": 499.8, "volume": 100}
        }
    }
    respx.get(f"{SANDBOX}/markets/timesales").mock(
        return_value=httpx.Response(200, json=payload)
    )
    adapter = TradierAdapter(token="t")
    snaps = list(
        adapter.replay(
            "SPY",
            datetime(2026, 5, 14, 14, 30),
            datetime(2026, 5, 14, 14, 31),
        )
    )
    assert len(snaps) == 1


@respx.mock
def test_tradier_adapter_replay_raises_on_empty_series():
    respx.get(f"{SANDBOX}/markets/timesales").mock(
        return_value=httpx.Response(200, json={"series": None})
    )
    adapter = TradierAdapter(token="t")
    with pytest.raises(ValueError, match="no timesales"):
        list(
            adapter.replay(
                "SPY",
                datetime(2026, 5, 14, 14, 30),
                datetime(2026, 5, 14, 14, 31),
            )
        )


@respx.mock
def test_tradier_adapter_replay_skips_malformed_bars():
    """Bars with missing/zero/non-numeric close must be dropped silently."""
    payload = {
        "series": {
            "data": [
                {"time": "2026-05-14T14:30:00", "close": 500.0},
                {"time": "2026-05-14T14:31:00", "close": None},
                {"time": "2026-05-14T14:32:00", "close": 0.0},
                {"time": "2026-05-14T14:33:00", "close": "not-a-number"},
                {"time": "2026-05-14T14:34:00", "close": 501.0},
            ]
        }
    }
    respx.get(f"{SANDBOX}/markets/timesales").mock(
        return_value=httpx.Response(200, json=payload)
    )
    adapter = TradierAdapter(token="t")
    snaps = list(
        adapter.replay(
            "SPY",
            datetime(2026, 5, 14, 14, 30),
            datetime(2026, 5, 14, 14, 35),
        )
    )
    assert len(snaps) == 2  # only 500.0 and 501.0


@respx.mock
def test_tradier_adapter_replay_4xx_raises_value_error():
    respx.get(f"{SANDBOX}/markets/timesales").mock(
        return_value=httpx.Response(401, text="Unauthorized")
    )
    adapter = TradierAdapter(token="bad")
    with pytest.raises(ValueError, match="401"):
        list(
            adapter.replay(
                "SPY", datetime(2026, 5, 14, 14, 30), datetime(2026, 5, 14, 14, 31)
            )
        )


@respx.mock
def test_tradier_adapter_replay_network_error_raises_value_error():
    respx.get(f"{SANDBOX}/markets/timesales").mock(
        side_effect=httpx.ConnectError("upstream down")
    )
    adapter = TradierAdapter(token="t")
    with pytest.raises(ValueError, match="tradier request failed"):
        list(
            adapter.replay(
                "SPY", datetime(2026, 5, 14, 14, 30), datetime(2026, 5, 14, 14, 31)
            )
        )


@respx.mock
def test_tradier_adapter_replay_non_json_raises_value_error():
    respx.get(f"{SANDBOX}/markets/timesales").mock(
        return_value=httpx.Response(200, text="<html>maintenance</html>")
    )
    adapter = TradierAdapter(token="t")
    with pytest.raises(ValueError, match="not JSON"):
        list(
            adapter.replay(
                "SPY", datetime(2026, 5, 14, 14, 30), datetime(2026, 5, 14, 14, 31)
            )
        )


# ---------------------------------------------------------------------------
# Stream / quote (markets/quotes)
# ---------------------------------------------------------------------------


@respx.mock
def test_tradier_adapter_quote_uses_real_bid_ask_sizes():
    payload = {
        "quotes": {
            "quote": {
                "bid": 499.99,
                "ask": 500.01,
                "bidsize": 5,
                "asksize": 8,
                "last": 500.00,
            }
        }
    }
    respx.get(f"{SANDBOX}/markets/quotes").mock(
        return_value=httpx.Response(200, json=payload)
    )
    adapter = TradierAdapter(token="t", size_multiplier=100.0)
    snap = adapter.quote("SPY")
    assert snap.best_bid == pytest.approx(499.99)
    assert snap.best_ask == pytest.approx(500.01)
    assert snap.best_bid_size == 500.0  # 5 × 100
    assert snap.best_ask_size == 800.0


@respx.mock
def test_tradier_adapter_quote_handles_quote_as_list():
    """When `symbols` is plural, Tradier returns `quote` as a list."""
    payload = {"quotes": {"quote": [{"bid": 100.0, "ask": 100.1, "bidsize": 1, "asksize": 1}]}}
    respx.get(f"{SANDBOX}/markets/quotes").mock(
        return_value=httpx.Response(200, json=payload)
    )
    adapter = TradierAdapter(token="t")
    snap = adapter.quote("SPY")
    assert snap.best_bid == pytest.approx(100.0)


@respx.mock
def test_tradier_adapter_quote_falls_back_to_synthetic_size_when_zero():
    """Sandbox occasionally reports bidsize=0; engine still needs a usable book."""
    payload = {
        "quotes": {"quote": {"bid": 100.0, "ask": 100.1, "bidsize": 0, "asksize": 0}}
    }
    respx.get(f"{SANDBOX}/markets/quotes").mock(
        return_value=httpx.Response(200, json=payload)
    )
    adapter = TradierAdapter(token="t", synthetic_size=123.0)
    snap = adapter.quote("SPY")
    assert snap.best_bid_size == 123.0
    assert snap.best_ask_size == 123.0


@respx.mock
def test_tradier_adapter_quote_returns_value_error_when_locked():
    payload = {"quotes": {"quote": {"bid": 100.0, "ask": 100.0, "bidsize": 1, "asksize": 1}}}
    respx.get(f"{SANDBOX}/markets/quotes").mock(
        return_value=httpx.Response(200, json=payload)
    )
    adapter = TradierAdapter(token="t")
    with pytest.raises(ValueError, match="no usable quote"):
        adapter.quote("SPY")


@respx.mock
def test_tradier_adapter_stream_yields_max_snaps_then_stops():
    payload = {"quotes": {"quote": {"bid": 100.0, "ask": 100.1, "bidsize": 1, "asksize": 1}}}
    respx.get(f"{SANDBOX}/markets/quotes").mock(
        return_value=httpx.Response(200, json=payload)
    )
    with patch("src.esmm.adapters.tradier_adapter.time.sleep"):
        adapter = TradierAdapter(token="t")
        snaps = list(adapter.stream("SPY", poll_seconds=0.0, max_snaps=3))
    assert len(snaps) == 3
    for s in snaps:
        assert s.best_bid == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Auth + URL plumbing
# ---------------------------------------------------------------------------


@respx.mock
def test_tradier_adapter_reads_token_from_env(monkeypatch):
    monkeypatch.setenv("TRADIER_TOKEN", "env-token")
    captured = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"quotes": {"quote": {
            "bid": 100, "ask": 101, "bidsize": 1, "asksize": 1
        }}})

    respx.get(f"{SANDBOX}/markets/quotes").mock(side_effect=_capture)
    TradierAdapter().quote("SPY")
    assert captured["auth"] == "Bearer env-token"


def test_tradier_adapter_raises_when_token_missing(monkeypatch):
    monkeypatch.delenv("TRADIER_TOKEN", raising=False)
    adapter = TradierAdapter()
    with pytest.raises(ValueError, match="token"):
        adapter.quote("SPY")


@respx.mock
def test_tradier_adapter_uses_production_url_when_overridden():
    captured = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"quotes": {"quote": {
            "bid": 100, "ask": 101, "bidsize": 1, "asksize": 1
        }}})

    respx.get("https://api.tradier.com/v1/markets/quotes").mock(side_effect=_capture)
    adapter = TradierAdapter(token="t", base_url="https://api.tradier.com/v1")
    adapter.quote("SPY")
    assert captured["url"].startswith("https://api.tradier.com/v1")


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------


def test_tradier_adapter_parse_ts_handles_known_formats():
    parse = TradierAdapter._parse_ts
    assert parse("2026-05-14T14:30:00") == datetime(2026, 5, 14, 14, 30, 0).timestamp()
    assert parse("2026-05-14 14:30:00") == datetime(2026, 5, 14, 14, 30, 0).timestamp()
    assert parse("2026-05-14T14:30") == datetime(2026, 5, 14, 14, 30).timestamp()
    # Epoch milliseconds path
    epoch_ms = 1_715_692_200_000
    assert parse(str(epoch_ms)) == pytest.approx(epoch_ms / 1000.0)


def test_tradier_adapter_parse_ts_rejects_garbage():
    with pytest.raises(ValueError, match="unparseable"):
        TradierAdapter._parse_ts("not-a-timestamp")


# ---------------------------------------------------------------------------
# End-to-end + HTTP
# ---------------------------------------------------------------------------


@respx.mock
def test_tradier_adapter_drives_full_backtest():
    from src.esmm.backtest import run_backtest
    from src.esmm.schemas import MarketMakingConfig

    bars = [
        {"time": f"2026-05-14T14:{30 + i:02d}:00", "close": 500.0 + 0.1 * i,
         "open": 500.0, "high": 500.5, "low": 499.5, "volume": 1000}
        for i in range(30)
    ]
    respx.get(f"{SANDBOX}/markets/timesales").mock(
        return_value=httpx.Response(200, json={"series": {"data": bars}})
    )
    adapter = TradierAdapter(token="t")
    snaps = list(
        adapter.replay(
            "SPY",
            datetime(2026, 5, 14, 14, 30),
            datetime(2026, 5, 14, 15, 0),
        )
    )
    config = MarketMakingConfig(symbol="SPY", base_half_spread_bps=2.0)
    result = run_backtest(snaps, config)
    assert result.n_quotes == 30
    assert result.tca is not None


@respx.mock
def test_tradier_live_endpoint_via_http():
    from fastapi.testclient import TestClient

    from src.api.main import app

    bars = [
        {"time": f"2026-05-14T14:{30 + i:02d}:00", "close": 500.0 + 0.1 * i,
         "open": 500.0, "high": 500.5, "low": 499.5, "volume": 1000}
        for i in range(15)
    ]
    respx.get(f"{SANDBOX}/markets/timesales").mock(
        return_value=httpx.Response(200, json={"series": {"data": bars}})
    )
    client = TestClient(app)
    resp = client.post(
        "/api/esmm/backtest/live",
        json={
            "config": {"symbol": "SPY", "base_half_spread_bps": 2.0},
            "adapter": "tradier",
            "start": "2026-05-14T14:30:00",
            "end":   "2026-05-14T15:00:00",
            "adapter_kwargs": {"token": "sandbox-token"},
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["n_quotes"] == 15


def test_tradier_appears_in_adapters_endpoint():
    from fastapi.testclient import TestClient

    from src.api.main import app

    client = TestClient(app)
    resp = client.get("/api/esmm/adapters")
    assert resp.status_code == 200
    names = {a["name"] for a in resp.json()}
    assert "tradier" in names
