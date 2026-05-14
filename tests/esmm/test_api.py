"""End-to-end tests against the FastAPI eSMM router."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_synthetic_book_endpoint_returns_requested_count(client: TestClient):
    resp = client.post(
        "/api/esmm/synthetic-book",
        json={"n_snaps": 50, "symbol": "SPY", "seed": 1},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 50
    assert all(s["symbol"] == "SPY" for s in body)


def test_quote_endpoint_returns_centred_quote_when_flat(client: TestClient):
    snap_payload = {
        "ts": 0.0,
        "symbol": "SPY",
        "bids": [{"price": 99.95, "size": 100}],
        "asks": [{"price": 100.05, "size": 100}],
    }
    config_payload = {
        "symbol": "SPY",
        "base_half_spread_bps": 10.0,
        "inventory_skew_bps_per_unit": 0.5,
        "max_inventory": 1000.0,
        "quote_size": 50.0,
    }
    resp = client.post(
        "/api/esmm/quote",
        json={"snapshot": snap_payload, "config": config_payload},
    )
    assert resp.status_code == 200
    q = resp.json()
    assert q["bid_price"] < q["fair_value"] < q["ask_price"]
    assert q["bid_size"] == 50.0


def test_crb_endpoint_internalises_overlap(client: TestClient):
    snap_payload = {
        "ts": 0.0,
        "symbol": "SPY",
        "bids": [{"price": 99.95, "size": 500}],
        "asks": [{"price": 100.05, "size": 500}],
    }
    resp = client.post(
        "/api/esmm/crb/internalise",
        json={
            "snapshot": snap_payload,
            "incoming_buys": 1000,
            "incoming_sells": 800,
            "internalisation_cap_pct": 1.0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["internalised"] == 800
    assert body["residual_to_street"] == 200


def test_backtest_endpoint_returns_tca(client: TestClient):
    config_payload = {
        "symbol": "SPY",
        "base_half_spread_bps": 8.0,
        "inventory_skew_bps_per_unit": 0.05,
        "max_inventory": 500.0,
        "quote_size": 50.0,
    }
    resp = client.post(
        "/api/esmm/backtest",
        json={"config": config_payload, "n_snaps": 100, "seed": 7},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_quotes"] == 100
    assert "tca" in body
    assert "spread_capture_pnl" in body["tca"]
    assert len(body["mid_path_sample"]) <= 100


def test_quote_endpoint_rejects_symbol_mismatch(client: TestClient):
    snap_payload = {
        "ts": 0.0,
        "symbol": "QQQ",
        "bids": [{"price": 99.95, "size": 100}],
        "asks": [{"price": 100.05, "size": 100}],
    }
    config_payload = {"symbol": "SPY"}
    resp = client.post(
        "/api/esmm/quote",
        json={"snapshot": snap_payload, "config": config_payload},
    )
    assert resp.status_code == 400
