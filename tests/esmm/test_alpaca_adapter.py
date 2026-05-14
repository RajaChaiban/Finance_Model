"""Tests for AlpacaAdapter.

The `alpaca-py` SDK is mocked end-to-end: tests never hit the network and
do not require ALPACA_API_KEY env vars. The mocked structure mirrors what
`StockHistoricalDataClient.get_stock_quotes()` actually returns:

    response.data[symbol] -> list[Quote]
    Quote.bid_price / .ask_price / .bid_size / .ask_size / .timestamp
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.esmm.adapters import AlpacaAdapter
from src.esmm.adapters.base import DataAdapter as DataAdapterProtocol
from src.esmm.schemas import OrderBookSnapshot


# ---------------------------------------------------------------------------
# SDK-installed mock — the AlpacaAdapter does `from alpaca.* import ...` at
# call time. We inject a fake `alpaca` package tree into sys.modules so the
# import resolves without the real library installed.
# ---------------------------------------------------------------------------


def _install_fake_alpaca_sdk(quotes_for_symbol: dict | None = None):
    """Install a fake alpaca SDK in sys.modules.

    Returns the MagicMock `StockHistoricalDataClient` class so tests can
    assert how the adapter called into it.
    """
    fake_root = MagicMock(name="alpaca")
    fake_data = MagicMock(name="alpaca.data")
    fake_historical = MagicMock(name="alpaca.data.historical")
    fake_requests = MagicMock(name="alpaca.data.requests")
    fake_enums = MagicMock(name="alpaca.data.enums")

    # The constructor returns an instance whose get_stock_quotes() returns a
    # response with .data dict.
    client_instance = MagicMock(name="StockHistoricalDataClient.instance")
    response = SimpleNamespace(data=quotes_for_symbol or {})
    client_instance.get_stock_quotes.return_value = response

    fake_historical.StockHistoricalDataClient = MagicMock(return_value=client_instance)
    fake_requests.StockQuotesRequest = MagicMock(name="StockQuotesRequest")
    fake_enums.DataFeed = SimpleNamespace(IEX="iex", SIP="sip")

    sys.modules["alpaca"] = fake_root
    sys.modules["alpaca.data"] = fake_data
    sys.modules["alpaca.data.historical"] = fake_historical
    sys.modules["alpaca.data.requests"] = fake_requests
    sys.modules["alpaca.data.enums"] = fake_enums

    return fake_historical.StockHistoricalDataClient, client_instance


@pytest.fixture(autouse=True)
def _scrub_alpaca_modules():
    """Each test gets a fresh fake-SDK install. Teardown removes them so
    other tests aren't poisoned by our mocks."""
    yield
    for key in [
        "alpaca",
        "alpaca.data",
        "alpaca.data.historical",
        "alpaca.data.requests",
        "alpaca.data.enums",
    ]:
        sys.modules.pop(key, None)


def _quote(bid: float, ask: float, bid_sz: float, ask_sz: float, ts: datetime):
    return SimpleNamespace(
        bid_price=bid,
        ask_price=ask,
        bid_size=bid_sz,
        ask_size=ask_sz,
        timestamp=ts,
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_alpaca_adapter_satisfies_protocol():
    adapter = AlpacaAdapter(api_key="x", secret_key="y")
    assert isinstance(adapter, DataAdapterProtocol)
    assert adapter.name == "alpaca"


# ---------------------------------------------------------------------------
# Replay — happy path
# ---------------------------------------------------------------------------


def test_alpaca_adapter_replay_converts_quotes_to_snapshots():
    base = datetime(2026, 5, 14, 14, 30, tzinfo=timezone.utc)
    quotes = [
        _quote(499.99, 500.01, 5.0, 5.0, base + timedelta(seconds=0)),
        _quote(500.00, 500.02, 3.0, 4.0, base + timedelta(seconds=1)),
        _quote(500.01, 500.03, 8.0, 2.0, base + timedelta(seconds=2)),
    ]
    _install_fake_alpaca_sdk({"SPY": quotes})

    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    snaps = list(
        adapter.replay(
            "SPY", base, base + timedelta(seconds=10)
        )
    )

    assert len(snaps) == 3
    assert snaps[0].best_bid == pytest.approx(499.99)
    assert snaps[0].best_ask == pytest.approx(500.01)
    # Default size_multiplier = 100 (Alpaca quote sizes are round-lots)
    assert snaps[0].best_bid_size == pytest.approx(500.0)
    assert snaps[0].best_ask_size == pytest.approx(500.0)
    # Timestamps strictly increasing
    assert snaps[0].ts < snaps[1].ts < snaps[2].ts


def test_alpaca_adapter_replay_passes_validator_for_every_snapshot():
    base = datetime(2026, 5, 14, 14, 30, tzinfo=timezone.utc)
    quotes = [
        _quote(500.0, 500.5, 2.0, 3.0, base + timedelta(seconds=i))
        for i in range(5)
    ]
    _install_fake_alpaca_sdk({"SPY": quotes})

    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    snaps = list(adapter.replay("SPY", base, base + timedelta(minutes=1)))
    # Re-construct each via the model — if the validator catches anything
    # the original was bad.
    for s in snaps:
        OrderBookSnapshot(ts=s.ts, symbol=s.symbol, bids=s.bids, asks=s.asks)


def test_alpaca_adapter_replay_size_multiplier_override():
    """size_multiplier=1.0 keeps raw Alpaca sizes (round-lots)."""
    base = datetime(2026, 5, 14, 14, 30, tzinfo=timezone.utc)
    quotes = [_quote(500.0, 500.5, 7.0, 9.0, base)]
    _install_fake_alpaca_sdk({"SPY": quotes})

    adapter = AlpacaAdapter(api_key="k", secret_key="s", size_multiplier=1.0)
    snaps = list(adapter.replay("SPY", base, base + timedelta(seconds=1)))
    assert snaps[0].best_bid_size == 7.0
    assert snaps[0].best_ask_size == 9.0


# ---------------------------------------------------------------------------
# Replay — degenerate input handling
# ---------------------------------------------------------------------------


def test_alpaca_adapter_replay_drops_locked_and_crossed_quotes():
    """Alpaca's SIP tape can carry locked/crossed NBBOs during halts. Those
    must be silently dropped, not propagated as malformed snapshots."""
    base = datetime(2026, 5, 14, 14, 30, tzinfo=timezone.utc)
    quotes = [
        _quote(500.0, 500.5, 5.0, 5.0, base + timedelta(seconds=0)),  # OK
        _quote(500.0, 500.0, 5.0, 5.0, base + timedelta(seconds=1)),  # locked
        _quote(500.5, 500.0, 5.0, 5.0, base + timedelta(seconds=2)),  # crossed
        _quote(0.0, 500.5, 5.0, 5.0, base + timedelta(seconds=3)),    # zero bid
        _quote(500.1, 500.6, 5.0, 5.0, base + timedelta(seconds=4)),  # OK
    ]
    _install_fake_alpaca_sdk({"SPY": quotes})

    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    snaps = list(adapter.replay("SPY", base, base + timedelta(seconds=10)))
    assert len(snaps) == 2


def test_alpaca_adapter_replay_raises_on_empty_response():
    _install_fake_alpaca_sdk({"SPY": []})
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    with pytest.raises(ValueError, match="no quotes"):
        list(
            adapter.replay(
                "SPY",
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 2, tzinfo=timezone.utc),
            )
        )


def test_alpaca_adapter_replay_raises_when_all_quotes_malformed():
    base = datetime(2026, 5, 14, 14, 30, tzinfo=timezone.utc)
    quotes = [_quote(500.0, 500.0, 5.0, 5.0, base)]  # all locked
    _install_fake_alpaca_sdk({"SPY": quotes})

    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    with pytest.raises(ValueError, match="none were well-formed"):
        list(adapter.replay("SPY", base, base + timedelta(seconds=10)))


def test_alpaca_adapter_replay_wraps_sdk_exceptions_as_value_error():
    _install_fake_alpaca_sdk()
    sys.modules["alpaca.data.historical"].StockHistoricalDataClient.return_value\
        .get_stock_quotes.side_effect = RuntimeError("503 upstream")
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    with pytest.raises(ValueError, match="alpaca-py replay failed.*503"):
        list(
            adapter.replay(
                "SPY",
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 2, tzinfo=timezone.utc),
            )
        )


# ---------------------------------------------------------------------------
# Auth / credential plumbing
# ---------------------------------------------------------------------------


def test_alpaca_adapter_reads_credentials_from_env_when_constructor_omits_them(
    monkeypatch,
):
    base = datetime(2026, 5, 14, 14, 30, tzinfo=timezone.utc)
    _, client_instance = _install_fake_alpaca_sdk({"SPY": [_quote(500.0, 500.5, 5, 5, base)]})
    monkeypatch.setenv("ALPACA_API_KEY", "env-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "env-secret")

    adapter = AlpacaAdapter()  # no constructor args
    list(adapter.replay("SPY", base, base + timedelta(seconds=1)))

    # Assert the SDK constructor saw the env-provided credentials
    sdk_ctor = sys.modules["alpaca.data.historical"].StockHistoricalDataClient
    sdk_ctor.assert_called_with(api_key="env-key", secret_key="env-secret")


def test_alpaca_adapter_raises_when_credentials_missing(monkeypatch):
    _install_fake_alpaca_sdk({"SPY": []})
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)

    adapter = AlpacaAdapter()
    with pytest.raises(ValueError, match="api_key.*secret_key"):
        list(
            adapter.replay(
                "SPY",
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 2, tzinfo=timezone.utc),
            )
        )


def test_alpaca_adapter_feed_param_passed_to_request(monkeypatch):
    base = datetime(2026, 5, 14, 14, 30, tzinfo=timezone.utc)
    _install_fake_alpaca_sdk({"SPY": [_quote(500.0, 500.5, 5, 5, base)]})

    adapter = AlpacaAdapter(api_key="k", secret_key="s", feed="sip")
    list(adapter.replay("SPY", base, base + timedelta(seconds=1)))

    req_class = sys.modules["alpaca.data.requests"].StockQuotesRequest
    # The adapter built a StockQuotesRequest at least once
    assert req_class.called
    # And passed feed=DataFeed.SIP ('sip')
    _, kwargs = req_class.call_args
    assert kwargs["feed"] == "sip"


# ---------------------------------------------------------------------------
# Stream is a stub
# ---------------------------------------------------------------------------


def test_alpaca_adapter_stream_is_not_implemented():
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    with pytest.raises(NotImplementedError, match="stream"):
        next(adapter.stream("SPY"))


# ---------------------------------------------------------------------------
# End-to-end: adapter → backtest → TCA
# ---------------------------------------------------------------------------


def test_alpaca_adapter_drives_full_backtest():
    """Real shape: a tape of NBBO ticks through the engine."""
    from src.esmm.backtest import run_backtest
    from src.esmm.schemas import MarketMakingConfig

    base = datetime(2026, 5, 14, 14, 30, tzinfo=timezone.utc)
    # 50 ticks with a slight upward drift
    quotes = [
        _quote(
            500.0 + 0.01 * i,
            500.02 + 0.01 * i,
            5.0,
            5.0,
            base + timedelta(milliseconds=200 * i),
        )
        for i in range(50)
    ]
    _install_fake_alpaca_sdk({"SPY": quotes})

    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    snaps = list(adapter.replay("SPY", base, base + timedelta(seconds=20)))
    assert len(snaps) == 50

    config = MarketMakingConfig(symbol="SPY", base_half_spread_bps=2.0)
    result = run_backtest(snaps, config)
    assert result.tca is not None
    assert result.n_quotes == 50


# ---------------------------------------------------------------------------
# HTTP endpoint: alpaca is registered and reachable via /backtest/live
# ---------------------------------------------------------------------------


def test_alpaca_appears_in_adapters_endpoint():
    from fastapi.testclient import TestClient

    from src.api.main import app

    client = TestClient(app)
    resp = client.get("/api/esmm/adapters")
    assert resp.status_code == 200
    names = {a["name"] for a in resp.json()}
    assert "alpaca" in names


def test_alpaca_live_endpoint_with_mocked_sdk():
    from fastapi.testclient import TestClient

    from src.api.main import app

    base = datetime(2026, 5, 14, 14, 30, tzinfo=timezone.utc)
    quotes = [
        _quote(500.0 + 0.01 * i, 500.02 + 0.01 * i, 5.0, 5.0,
               base + timedelta(milliseconds=200 * i))
        for i in range(20)
    ]
    _install_fake_alpaca_sdk({"SPY": quotes})

    client = TestClient(app)
    resp = client.post(
        "/api/esmm/backtest/live",
        json={
            "config": {"symbol": "SPY", "base_half_spread_bps": 2.0},
            "adapter": "alpaca",
            "start": base.isoformat(),
            "end": (base + timedelta(seconds=10)).isoformat(),
            "adapter_kwargs": {"api_key": "k", "secret_key": "s"},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["n_quotes"] == 20
