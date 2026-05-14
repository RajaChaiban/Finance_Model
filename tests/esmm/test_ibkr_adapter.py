"""Tests for IBKRAdapter.

`ib_insync` is mocked end-to-end. Tests never connect to TWS/IB Gateway and
do not require the SDK to be installed (we inject a fake `ib_insync` module
into sys.modules per test).

What we mock and how:
    ib_insync.IB           -> a MagicMock class; instance has .connect/.disconnect,
                              .qualifyContracts, and .reqHistoricalData
    ib_insync.Stock        -> SimpleNamespace; we don't introspect it
    BarData                -> SimpleNamespace with date/open/high/low/close/volume
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.esmm.adapters import IBKRAdapter
from src.esmm.adapters.base import DataAdapter as DataAdapterProtocol
from src.esmm.schemas import OrderBookSnapshot


# ---------------------------------------------------------------------------
# Fake ib_insync installer
# ---------------------------------------------------------------------------


def _install_fake_ib_insync(
    bid_bars: list | None = None,
    ask_bars: list | None = None,
    connect_side_effect=None,
    qualify_side_effect=None,
):
    """Install a fake ib_insync module returning the supplied historical bars.

    Returns (fake_module, ib_instance) so callers can assert on call args.
    """
    fake_module = MagicMock(name="ib_insync")
    ib_instance = MagicMock(name="IB.instance")
    fake_module.IB = MagicMock(return_value=ib_instance)
    # Stock(symbol, exchange, currency) is called positionally; MagicMock
    # accepts any positional/kwarg combination.
    fake_module.Stock = MagicMock(name="Stock")

    if connect_side_effect is not None:
        ib_instance.connect.side_effect = connect_side_effect
    if qualify_side_effect is not None:
        ib_instance.qualifyContracts.side_effect = qualify_side_effect

    # reqHistoricalData(..., whatToShow="BID"|"ASK", ...) — route by kwarg
    def _hist(*args, **kwargs):
        if kwargs.get("whatToShow") == "BID":
            return bid_bars or []
        if kwargs.get("whatToShow") == "ASK":
            return ask_bars or []
        return []

    ib_instance.reqHistoricalData.side_effect = _hist
    sys.modules["ib_insync"] = fake_module
    return fake_module, ib_instance


@pytest.fixture(autouse=True)
def _scrub_ib_insync_module():
    yield
    sys.modules.pop("ib_insync", None)


def _bar(ts: datetime, close: float, volume: float = 50.0):
    return SimpleNamespace(
        date=ts, open=close, high=close, low=close, close=close, volume=volume
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_ibkr_adapter_satisfies_protocol():
    adapter = IBKRAdapter()
    assert isinstance(adapter, DataAdapterProtocol)
    assert adapter.name == "ibkr"


# ---------------------------------------------------------------------------
# Replay — happy path
# ---------------------------------------------------------------------------


def test_ibkr_adapter_replay_pairs_bid_and_ask_bars():
    base = datetime(2026, 5, 14, 14, 30)
    bid_bars = [_bar(base + timedelta(minutes=i), 499.95 + 0.1 * i) for i in range(3)]
    ask_bars = [_bar(base + timedelta(minutes=i), 500.05 + 0.1 * i) for i in range(3)]
    _install_fake_ib_insync(bid_bars=bid_bars, ask_bars=ask_bars)

    adapter = IBKRAdapter()
    snaps = list(
        adapter.replay(
            "SPY", base, base + timedelta(minutes=5)
        )
    )
    assert len(snaps) == 3
    for i, snap in enumerate(snaps):
        assert snap.best_bid == pytest.approx(499.95 + 0.1 * i)
        assert snap.best_ask == pytest.approx(500.05 + 0.1 * i)
        assert snap.best_bid_size == 50.0  # bar.volume
        assert snap.best_ask_size == 50.0


def test_ibkr_adapter_replay_drops_unmatched_timestamps():
    """If only one side has a bar at a given ts, that pair is skipped."""
    base = datetime(2026, 5, 14, 14, 30)
    bid_bars = [
        _bar(base + timedelta(minutes=0), 499.9),
        _bar(base + timedelta(minutes=1), 500.0),
        _bar(base + timedelta(minutes=2), 500.1),  # only on bid side
    ]
    ask_bars = [
        _bar(base + timedelta(minutes=0), 500.0),
        _bar(base + timedelta(minutes=1), 500.1),
        # nothing at minute 2
    ]
    _install_fake_ib_insync(bid_bars=bid_bars, ask_bars=ask_bars)
    adapter = IBKRAdapter()
    snaps = list(adapter.replay("SPY", base, base + timedelta(minutes=3)))
    assert len(snaps) == 2


def test_ibkr_adapter_replay_drops_invalid_pairs():
    """Locked, crossed, or non-positive prices must be dropped."""
    base = datetime(2026, 5, 14, 14, 30)
    bid_bars = [
        _bar(base + timedelta(minutes=0), 500.0),  # OK
        _bar(base + timedelta(minutes=1), 500.0),  # locked
        _bar(base + timedelta(minutes=2), 500.5),  # crossed
        _bar(base + timedelta(minutes=3), 0.0),    # zero
        _bar(base + timedelta(minutes=4), 500.1),  # OK
    ]
    ask_bars = [
        _bar(base + timedelta(minutes=0), 500.5),
        _bar(base + timedelta(minutes=1), 500.0),  # locked
        _bar(base + timedelta(minutes=2), 500.0),  # crossed
        _bar(base + timedelta(minutes=3), 500.0),
        _bar(base + timedelta(minutes=4), 500.6),
    ]
    _install_fake_ib_insync(bid_bars=bid_bars, ask_bars=ask_bars)
    adapter = IBKRAdapter()
    snaps = list(adapter.replay("SPY", base, base + timedelta(minutes=5)))
    assert len(snaps) == 2


def test_ibkr_adapter_replay_uses_synthetic_size_when_volume_zero():
    base = datetime(2026, 5, 14, 14, 30)
    bid_bars = [_bar(base, 500.0, volume=0)]
    ask_bars = [_bar(base, 500.5, volume=0)]
    _install_fake_ib_insync(bid_bars=bid_bars, ask_bars=ask_bars)

    adapter = IBKRAdapter(synthetic_size=250.0)
    snaps = list(adapter.replay("SPY", base, base + timedelta(minutes=1)))
    assert snaps[0].best_bid_size == 250.0
    assert snaps[0].best_ask_size == 250.0


# ---------------------------------------------------------------------------
# Replay — failure paths
# ---------------------------------------------------------------------------


def test_ibkr_adapter_replay_raises_when_no_bars_returned():
    _install_fake_ib_insync(bid_bars=[], ask_bars=[])
    adapter = IBKRAdapter()
    with pytest.raises(ValueError, match="no historical bars"):
        list(
            adapter.replay(
                "SPY",
                datetime(2026, 5, 14, 14, 30),
                datetime(2026, 5, 14, 14, 31),
            )
        )


def test_ibkr_adapter_replay_raises_when_no_pairs_align():
    """Both sides have bars but none share a timestamp."""
    base = datetime(2026, 5, 14, 14, 30)
    bid_bars = [_bar(base + timedelta(minutes=0), 500.0)]
    ask_bars = [_bar(base + timedelta(minutes=1), 500.5)]
    _install_fake_ib_insync(bid_bars=bid_bars, ask_bars=ask_bars)
    adapter = IBKRAdapter()
    with pytest.raises(ValueError, match="no bid/ask pair aligned"):
        list(adapter.replay("SPY", base, base + timedelta(minutes=2)))


def test_ibkr_adapter_replay_wraps_connect_failure_as_value_error():
    _install_fake_ib_insync(
        bid_bars=[], ask_bars=[],
        connect_side_effect=ConnectionRefusedError("TWS not running"),
    )
    adapter = IBKRAdapter()
    with pytest.raises(ValueError, match="failed to connect"):
        list(
            adapter.replay(
                "SPY",
                datetime(2026, 5, 14, 14, 30),
                datetime(2026, 5, 14, 14, 31),
            )
        )


def test_ibkr_adapter_replay_wraps_qualify_failure_as_value_error():
    _install_fake_ib_insync(
        bid_bars=[], ask_bars=[],
        qualify_side_effect=RuntimeError("ambiguous contract"),
    )
    adapter = IBKRAdapter()
    with pytest.raises(ValueError, match="could not qualify"):
        list(
            adapter.replay(
                "SPY",
                datetime(2026, 5, 14, 14, 30),
                datetime(2026, 5, 14, 14, 31),
            )
        )


def test_ibkr_adapter_replay_disconnects_even_when_no_bars():
    """Resource cleanup must run on the error path."""
    _, ib_instance = _install_fake_ib_insync(bid_bars=[], ask_bars=[])
    adapter = IBKRAdapter()
    with pytest.raises(ValueError):
        list(
            adapter.replay(
                "SPY",
                datetime(2026, 5, 14, 14, 30),
                datetime(2026, 5, 14, 14, 31),
            )
        )
    ib_instance.disconnect.assert_called_once()


# ---------------------------------------------------------------------------
# Duration grammar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "delta_seconds,expected",
    [
        (60, "60 S"),
        (3600, "3600 S"),
        (86_399, "86399 S"),
        (86_400, "1 D"),
        (86_401, "2 D"),  # ceil
        (604_800, "7 D"),
    ],
)
def test_duration_str_translation(delta_seconds: int, expected: str):
    base = datetime(2026, 1, 1)
    assert (
        IBKRAdapter._duration_str(base, base + timedelta(seconds=delta_seconds))
        == expected
    )


# ---------------------------------------------------------------------------
# Bar timestamp parsing
# ---------------------------------------------------------------------------


def test_bar_ts_handles_datetime_value():
    dt = datetime(2026, 5, 14, 14, 30, 0)
    assert IBKRAdapter._bar_ts(SimpleNamespace(date=dt)) == dt.timestamp()


def test_bar_ts_handles_string_date_in_known_formats():
    # ib_insync sometimes returns a string for date-only bars
    expected = datetime(2026, 5, 14).timestamp()
    assert IBKRAdapter._bar_ts(SimpleNamespace(date="20260514")) == expected


def test_bar_ts_returns_none_for_garbage():
    assert IBKRAdapter._bar_ts(SimpleNamespace(date="not-a-date")) is None
    assert IBKRAdapter._bar_ts(SimpleNamespace(date=None)) is None


# ---------------------------------------------------------------------------
# Stream is a stub
# ---------------------------------------------------------------------------


def test_ibkr_adapter_stream_is_not_implemented():
    adapter = IBKRAdapter()
    with pytest.raises(NotImplementedError, match="stream"):
        next(adapter.stream("SPY"))


# ---------------------------------------------------------------------------
# End-to-end + HTTP
# ---------------------------------------------------------------------------


def test_ibkr_adapter_drives_full_backtest():
    from src.esmm.backtest import run_backtest
    from src.esmm.schemas import MarketMakingConfig

    base = datetime(2026, 5, 14, 14, 30)
    n = 40
    bid_bars = [_bar(base + timedelta(seconds=i), 500.0 + 0.02 * i) for i in range(n)]
    ask_bars = [_bar(base + timedelta(seconds=i), 500.10 + 0.02 * i) for i in range(n)]
    _install_fake_ib_insync(bid_bars=bid_bars, ask_bars=ask_bars)

    adapter = IBKRAdapter(bar_size="1 secs")
    snaps = list(adapter.replay("SPY", base, base + timedelta(seconds=n)))
    assert len(snaps) == n

    config = MarketMakingConfig(symbol="SPY", base_half_spread_bps=2.0)
    result = run_backtest(snaps, config)
    assert result.n_quotes == n
    assert result.tca is not None


def test_ibkr_appears_in_adapters_endpoint():
    from fastapi.testclient import TestClient

    from src.api.main import app

    client = TestClient(app)
    resp = client.get("/api/esmm/adapters")
    assert resp.status_code == 200
    names = {a["name"] for a in resp.json()}
    assert "ibkr" in names


def test_ibkr_live_endpoint_via_http():
    from fastapi.testclient import TestClient

    from src.api.main import app

    base = datetime(2026, 5, 14, 14, 30)
    bid_bars = [_bar(base + timedelta(seconds=i), 500.0 + 0.02 * i) for i in range(20)]
    ask_bars = [_bar(base + timedelta(seconds=i), 500.10 + 0.02 * i) for i in range(20)]
    _install_fake_ib_insync(bid_bars=bid_bars, ask_bars=ask_bars)

    client = TestClient(app)
    resp = client.post(
        "/api/esmm/backtest/live",
        json={
            "config": {"symbol": "SPY", "base_half_spread_bps": 2.0},
            "adapter": "ibkr",
            "start": base.isoformat(),
            "end": (base + timedelta(seconds=20)).isoformat(),
            "adapter_kwargs": {"port": 7497, "client_id": 999, "bar_size": "1 secs"},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["n_quotes"] == 20
