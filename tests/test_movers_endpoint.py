"""Endpoint-level regression test for /api/market/movers.

Mocks the underlying batch fetch so the test runs hermetically and asserts
the FastAPI route plumbing (CORS middleware, JSON serialisation, error path).
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

from src.api.main import app
from src.data import movers


def _series_for_universe(closes):
    return {t: list(closes) for t in movers.INDEX_TICKERS + movers.DEFAULT_UNIVERSE}


def setup_function(_):
    movers._movers_cache.clear()


def teardown_function(_):
    movers._movers_cache.clear()


def test_movers_endpoint_200_and_shape():
    closes = list(np.linspace(100, 110, 32))
    series = _series_for_universe(closes)

    client = TestClient(app)
    with patch("src.data.movers.fetch_movers_batch", return_value=series):
        resp = client.get("/api/market/movers")

    assert resp.status_code == 200
    body = resp.json()
    assert {"as_of", "indices", "gainers", "losers", "volatile"} <= set(body.keys())
    assert isinstance(body["indices"], list)
    assert len(body["indices"]) == len(movers.INDEX_TICKERS)


def test_movers_endpoint_universe_param():
    """The universe query parameter is accepted (defaults to 'default')."""
    closes = list(np.linspace(100, 102, 32))
    series = _series_for_universe(closes)

    client = TestClient(app)
    with patch("src.data.movers.fetch_movers_batch", return_value=series):
        resp = client.get("/api/market/movers?universe=default")

    assert resp.status_code == 200


def test_movers_endpoint_handles_fetch_failure_gracefully():
    """If the upstream fetch returns no usable series, return 200 with empty lists."""
    empty = {t: [] for t in movers.INDEX_TICKERS + movers.DEFAULT_UNIVERSE}

    client = TestClient(app)
    with patch("src.data.movers.fetch_movers_batch", return_value=empty):
        resp = client.get("/api/market/movers")

    assert resp.status_code == 200
    body = resp.json()
    assert body["indices"] == []
    assert body["gainers"] == []
    assert body["losers"] == []
    assert body["volatile"] == []


def test_movers_endpoint_returns_500_on_exception():
    """Internal failures bubble to a 500 with a JSON error body."""
    client = TestClient(app, raise_server_exceptions=False)
    # main.py does `from ..data.movers import get_movers_payload`, so the name
    # to patch lives in the api.main namespace.
    with patch("src.api.main.get_movers_payload", side_effect=RuntimeError("boom")):
        resp = client.get("/api/market/movers")

    assert resp.status_code == 500
    assert "detail" in resp.json()


def test_existing_health_endpoint_unchanged():
    """Regression check: the /health route still works (no route conflict)."""
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}
