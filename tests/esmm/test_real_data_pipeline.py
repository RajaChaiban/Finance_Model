"""End-to-end pipeline tests: real-data adapters → backtest → TCA → agent loop.

Exercises the full path the user wires when they swap from synthetic to live:

    adapter.replay()  →  list[OrderBookSnapshot]
                      →  run_backtest()
                      →  TCABreakdown
                      →  AgenticESMMOrchestrator.run()
                      →  AgenticRunResult

Plus the two new HTTP endpoints (/api/esmm/backtest/snapshots and
/api/esmm/backtest/live) so the wire payloads are covered too.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.agents.esmm.orchestrator import AgenticESMMOrchestrator
from src.api.main import app
from src.esmm.adapters import SyntheticAdapter, YFinanceAdapter
from src.esmm.backtest import run_backtest
from src.esmm.schemas import MarketMakingConfig


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Direct pipeline (no HTTP) — synthetic adapter
# ---------------------------------------------------------------------------


def test_pipeline_synthetic_adapter_full_engine_run():
    """Adapter → backtest → TCA → agent orchestrator, all defaults."""
    adapter = SyntheticAdapter(seed=42, sigma_per_step=0.001)
    start = datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc)
    snaps = list(adapter.replay("SPY", start, start + timedelta(seconds=400)))

    config = MarketMakingConfig(symbol="SPY", base_half_spread_bps=8.0)
    result = run_backtest(snaps, config)
    assert result.n_quotes == len(snaps)
    assert result.tca is not None
    assert result.tca["n_fills"] == result.n_fills

    orch = AgenticESMMOrchestrator(baseline=config, max_iterations=2)
    agent_result = orch.run(snaps)
    assert agent_result.best_decision is not None
    assert 0.0 <= agent_result.best_decision.score.score <= 100.0


def test_pipeline_synthetic_adapter_produces_deterministic_pnl():
    """Same seed twice → identical P&L. Catches accidental hidden state."""
    config = MarketMakingConfig(symbol="SPY")
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(seconds=300)

    a = list(SyntheticAdapter(seed=7).replay("SPY", start, end))
    b = list(SyntheticAdapter(seed=7).replay("SPY", start, end))
    pnl_a = run_backtest(a, config).total_pnl
    pnl_b = run_backtest(b, config).total_pnl
    assert pnl_a == pnl_b


# ---------------------------------------------------------------------------
# Direct pipeline — yfinance adapter (mocked yfinance)
# ---------------------------------------------------------------------------


def _mock_yfinance_with_close_path(closes: list[float]):
    """Helper: install a MagicMock yfinance returning a 1-min DataFrame.

    Returns the patcher so the caller can wrap it in `with`."""
    import pandas as pd

    base = datetime(2026, 5, 14, 14, 30, tzinfo=timezone.utc)
    df = pd.DataFrame(
        {"Close": closes},
        index=pd.to_datetime([base + timedelta(minutes=i) for i in range(len(closes))]),
    )
    fake = MagicMock()
    fake.Ticker.return_value.history.return_value = df
    return patch.dict("sys.modules", {"yfinance": fake})


def test_pipeline_yfinance_adapter_drives_full_backtest():
    """Real-shape historical price path → run_backtest → TCA, with the agent
    layer running on top. This is the e2e test that proves the wires are
    actually connected."""
    # Trending up then down — should generate enough mid motion to fill our
    # adversarial quotes a few times.
    closes = [500.0 + (i * 0.5 if i < 30 else 15.0 - 0.5 * (i - 30)) for i in range(60)]

    with _mock_yfinance_with_close_path(closes):
        adapter = YFinanceAdapter(half_spread_bps=8.0, synthetic_size=200.0)
        snaps = list(
            adapter.replay(
                "SPY",
                datetime(2026, 5, 14, tzinfo=timezone.utc),
                datetime(2026, 5, 15, tzinfo=timezone.utc),
            )
        )

    assert len(snaps) == 60
    config = MarketMakingConfig(
        symbol="SPY",
        base_half_spread_bps=4.0,  # tighter than the synthetic spread so we cross
        max_inventory=10_000.0,
    )
    result = run_backtest(snaps, config)
    assert result.tca is not None
    assert result.n_quotes == 60
    # P&L attribution must close (within float tolerance)
    parts = result.tca
    total_parts = (
        parts["spread_capture_pnl"]
        + parts["inventory_pnl"]
        + parts["hedge_pnl"]
        + parts["adverse_selection_pnl"]
        + parts["fees_pnl"]
    )
    assert total_parts == pytest.approx(parts["total_pnl"], abs=1e-6)

    # Agent layer runs on top without exploding
    orch = AgenticESMMOrchestrator(baseline=config, max_iterations=2)
    agent_result = orch.run(snaps)
    assert agent_result.best_decision is not None


# ---------------------------------------------------------------------------
# HTTP endpoint: /api/esmm/backtest/snapshots
# ---------------------------------------------------------------------------


def _snapshot_payloads_from_synthetic(n: int = 50, seed: int = 1) -> list[dict]:
    adapter = SyntheticAdapter(seed=seed)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    snaps = list(adapter.replay("SPY", start, start + timedelta(seconds=n)))
    return [s.model_dump() for s in snaps]


def test_backtest_snapshots_endpoint_happy_path(client: TestClient):
    payload = {
        "config": {
            "symbol": "SPY",
            "base_half_spread_bps": 5.0,
            "quote_size": 100,
            "max_inventory": 1000,
        },
        "snapshots": _snapshot_payloads_from_synthetic(n=80),
    }
    resp = client.post("/api/esmm/backtest/snapshots", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["n_quotes"] == 80
    assert "tca" in body
    assert "spread_capture_pnl" in body["tca"]


def test_backtest_snapshots_endpoint_rejects_symbol_mismatch(client: TestClient):
    payload = {
        "config": {"symbol": "AAPL"},
        "snapshots": _snapshot_payloads_from_synthetic(n=10),  # symbol=SPY
    }
    resp = client.post("/api/esmm/backtest/snapshots", json=payload)
    assert resp.status_code == 400
    assert "symbol" in resp.text.lower()


def test_backtest_snapshots_endpoint_rejects_too_few(client: TestClient):
    """`min_length=2` on the request model — single-snap requests must fail
    fast with 422 rather than blow up inside the engine."""
    payload = {
        "config": {"symbol": "SPY"},
        "snapshots": _snapshot_payloads_from_synthetic(n=1),
    }
    resp = client.post("/api/esmm/backtest/snapshots", json=payload)
    assert resp.status_code == 422


def test_backtest_snapshots_endpoint_rejects_malformed_book(client: TestClient):
    """Bids in ascending order — the validator must reject at the wire."""
    bad_snap = {
        "ts": 0.0,
        "symbol": "SPY",
        "bids": [
            {"price": 99.0, "size": 100},  # wrong: should be best (largest) first
            {"price": 99.5, "size": 100},
        ],
        "asks": [{"price": 100.5, "size": 100}],
    }
    good_snap = {
        "ts": 1.0,
        "symbol": "SPY",
        "bids": [{"price": 99.5, "size": 100}],
        "asks": [{"price": 100.5, "size": 100}],
    }
    payload = {
        "config": {"symbol": "SPY"},
        "snapshots": [bad_snap, good_snap],
    }
    resp = client.post("/api/esmm/backtest/snapshots", json=payload)
    assert resp.status_code == 422
    assert "descending" in resp.text.lower()


# ---------------------------------------------------------------------------
# HTTP endpoint: /api/esmm/backtest/live
# ---------------------------------------------------------------------------


def test_backtest_live_synthetic_adapter(client: TestClient):
    """The synthetic adapter is always registered, so this is the offline
    smoke test for the live endpoint."""
    start = datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc).isoformat()
    end = datetime(2026, 1, 1, 14, 35, tzinfo=timezone.utc).isoformat()
    payload = {
        "config": {"symbol": "SPY"},
        "adapter": "synthetic",
        "start": start,
        "end": end,
        "adapter_kwargs": {"seed": 99, "dt_seconds": 1.0},
    }
    resp = client.post("/api/esmm/backtest/live", json=payload)
    assert resp.status_code == 200, resp.text
    assert resp.json()["n_quotes"] == 300  # 5 minutes × 60s


def test_backtest_live_unknown_adapter_returns_400(client: TestClient):
    resp = client.post(
        "/api/esmm/backtest/live",
        json={
            "config": {"symbol": "SPY"},
            "adapter": "nonexistent",
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-01-01T00:05:00Z",
        },
    )
    assert resp.status_code == 400
    assert "unknown adapter" in resp.text.lower()


def test_backtest_live_bad_adapter_kwargs_returns_422(client: TestClient):
    resp = client.post(
        "/api/esmm/backtest/live",
        json={
            "config": {"symbol": "SPY"},
            "adapter": "synthetic",
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-01-01T00:05:00Z",
            "adapter_kwargs": {"nonsense_arg_that_doesnt_exist": 1},
        },
    )
    assert resp.status_code == 422


def test_backtest_live_yfinance_adapter_with_mocked_yf(client: TestClient):
    """Exercises the live endpoint against the YFinanceAdapter, with the
    yfinance import patched to return a deterministic OHLC frame."""
    import pandas as pd

    base = datetime(2026, 5, 14, 14, 30, tzinfo=timezone.utc)
    closes = [500.0 + 0.3 * i for i in range(40)]
    df = pd.DataFrame(
        {"Close": closes},
        index=pd.to_datetime([base + timedelta(minutes=i) for i in range(40)]),
    )
    fake_yf = MagicMock()
    fake_yf.Ticker.return_value.history.return_value = df

    payload = {
        "config": {"symbol": "SPY", "base_half_spread_bps": 4.0},
        "adapter": "yfinance",
        "start": "2026-05-14T14:30:00+00:00",
        "end": "2026-05-14T15:10:00+00:00",
        "adapter_kwargs": {"half_spread_bps": 6.0, "synthetic_size": 250.0},
    }
    with patch.dict("sys.modules", {"yfinance": fake_yf}):
        resp = client.post("/api/esmm/backtest/live", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["n_quotes"] == 40
    assert body["tca"]["n_fills"] == body["n_fills"]


def test_backtest_live_yfinance_no_data_returns_404(client: TestClient):
    import pandas as pd

    fake_yf = MagicMock()
    fake_yf.Ticker.return_value.history.return_value = pd.DataFrame()  # empty
    payload = {
        "config": {"symbol": "SPY"},
        "adapter": "yfinance",
        "start": "2026-05-14T14:30:00+00:00",
        "end": "2026-05-14T14:40:00+00:00",
    }
    with patch.dict("sys.modules", {"yfinance": fake_yf}):
        resp = client.post("/api/esmm/backtest/live", json=payload)
    assert resp.status_code == 404
    assert "no" in resp.text.lower() and "bars" in resp.text.lower()


# ---------------------------------------------------------------------------
# HTTP endpoint: /api/esmm/adapters
# ---------------------------------------------------------------------------


def test_list_adapters_includes_synthetic_and_yfinance(client: TestClient):
    resp = client.get("/api/esmm/adapters")
    assert resp.status_code == 200
    body = resp.json()
    names = {a["name"] for a in body}
    assert {"synthetic", "yfinance"}.issubset(names)
    for a in body:
        assert isinstance(a["docstring"], str)


# ---------------------------------------------------------------------------
# Sanity: existing /backtest endpoint still works (regression guard)
# ---------------------------------------------------------------------------


def test_legacy_synthetic_backtest_endpoint_still_works(client: TestClient):
    resp = client.post(
        "/api/esmm/backtest",
        json={
            "config": {"symbol": "SPY"},
            "n_snaps": 100,
            "seed": 1,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["n_quotes"] == 100
