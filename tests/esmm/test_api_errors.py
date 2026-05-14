"""API error-path tests — verify the router returns proper 4xx, never 500,
on malformed or out-of-bounds inputs."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_synthetic_book_rejects_n_snaps_below_minimum(client: TestClient):
    resp = client.post("/api/esmm/synthetic-book", json={"n_snaps": 5})
    assert resp.status_code == 422  # ge=10 in schema


def test_synthetic_book_rejects_n_snaps_above_max(client: TestClient):
    resp = client.post("/api/esmm/synthetic-book", json={"n_snaps": 99999})
    assert resp.status_code == 422


def test_quote_endpoint_400_on_symbol_mismatch_not_500(client: TestClient):
    resp = client.post(
        "/api/esmm/quote",
        json={
            "snapshot": {
                "ts": 0.0, "symbol": "AAA",
                "bids": [{"price": 1, "size": 1}], "asks": [{"price": 2, "size": 1}],
            },
            "config": {"symbol": "BBB"},
        },
    )
    assert resp.status_code == 400


def test_quote_endpoint_422_on_missing_required_field(client: TestClient):
    resp = client.post("/api/esmm/quote", json={"config": {"symbol": "X"}})
    assert resp.status_code == 422


def test_crb_endpoint_rejects_cap_above_one(client: TestClient):
    snap = {
        "ts": 0.0, "symbol": "X",
        "bids": [{"price": 1, "size": 1}], "asks": [{"price": 2, "size": 1}],
    }
    resp = client.post(
        "/api/esmm/crb/internalise",
        json={"snapshot": snap, "incoming_buys": 1, "incoming_sells": 1, "internalisation_cap_pct": 1.5},
    )
    assert resp.status_code == 422


def test_crb_endpoint_rejects_negative_cap(client: TestClient):
    snap = {
        "ts": 0.0, "symbol": "X",
        "bids": [{"price": 1, "size": 1}], "asks": [{"price": 2, "size": 1}],
    }
    resp = client.post(
        "/api/esmm/crb/internalise",
        json={"snapshot": snap, "incoming_buys": 1, "incoming_sells": 1, "internalisation_cap_pct": -0.1},
    )
    assert resp.status_code == 422


def test_backtest_endpoint_rejects_n_snaps_too_small(client: TestClient):
    resp = client.post(
        "/api/esmm/backtest",
        json={"config": {"symbol": "X"}, "n_snaps": 1},
    )
    assert resp.status_code == 422


def test_backtest_endpoint_rejects_missing_config(client: TestClient):
    resp = client.post("/api/esmm/backtest", json={"n_snaps": 100})
    assert resp.status_code == 422


def test_quote_endpoint_with_seed_position_short(client: TestClient):
    """Cover the short-seed-position branch of the quote endpoint."""
    resp = client.post(
        "/api/esmm/quote",
        json={
            "snapshot": {
                "ts": 0.0, "symbol": "X",
                "bids": [{"price": 99.5, "size": 100}],
                "asks": [{"price": 100.5, "size": 100}],
            },
            "config": {"symbol": "X", "max_inventory": 1000},
            "seed_position": {"symbol": "X", "quantity": -50.0, "avg_cost": 100.0},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # Short inventory → quotes should skew up (mid = 100, skew positive)
    assert body["fair_value"] == pytest.approx(100.0, abs=0.01)
    assert body["bid_price"] > 100.0 - 0.05 - body["half_spread_bps"] * 100 * 1e-4 - 0.01


def test_quote_endpoint_with_zero_seed_position_treated_as_flat(client: TestClient):
    resp = client.post(
        "/api/esmm/quote",
        json={
            "snapshot": {
                "ts": 0.0, "symbol": "X",
                "bids": [{"price": 99.5, "size": 100}],
                "asks": [{"price": 100.5, "size": 100}],
            },
            "config": {"symbol": "X"},
            "seed_position": {"symbol": "X", "quantity": 0.0, "avg_cost": 0.0},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["skew_bps"] == 0.0


def test_synthetic_book_endpoint_uses_default_symbol(client: TestClient):
    """Cover the default-symbol path."""
    resp = client.post("/api/esmm/synthetic-book", json={"n_snaps": 10})
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["symbol"] == "SPY"
