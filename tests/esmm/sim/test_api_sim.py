"""Tests for the /api/esmm/sim/* endpoints.

Cover:
  * GET /scenarios returns the 6 bundled curated scenarios
  * GET /participants returns the registered archetypes
  * POST /sandbox runs end-to-end with no participants (seeded book only)
  * POST /sandbox rejects unknown participant kinds with 400
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import app


client = TestClient(app)


class TestScenariosEndpoint:
    def test_returns_curated_six(self) -> None:
        r = client.get("/api/esmm/sim/scenarios")
        assert r.status_code == 200
        data = r.json()
        ids = {row["scenario_id"] for row in data}
        assert {
            "flash_crash_2010",
            "covid_mar_2020",
            "hot_cpi",
            "fomc_surprise",
            "opex_pin",
            "liquidity_drought",
        }.issubset(ids)

    def test_scenario_info_shape(self) -> None:
        r = client.get("/api/esmm/sim/scenarios")
        data = r.json()
        row = data[0]
        for key in (
            "scenario_id",
            "description",
            "duration_sec",
            "regime_label",
            "starting_mid",
            "starting_spread_bps",
            "n_participants",
            "n_events",
        ):
            assert key in row


class TestParticipantsEndpoint:
    def test_returns_list(self) -> None:
        r = client.get("/api/esmm/sim/participants")
        assert r.status_code == 200
        # We don't assert specific kinds — depends on whether
        # noise/informed/replay modules are present yet.
        assert isinstance(r.json(), list)


class TestSandboxEndpoint:
    def test_empty_participants_returns_zero_fills(self) -> None:
        r = client.post(
            "/api/esmm/sim/sandbox",
            json={
                "kernel": {
                    "duration_sec": 0.05,
                    "tick_interval_sec": 0.001,
                    "snapshot_interval_sec": 0.01,
                    "enable_latency": False,
                    "symbol": "SPY",
                    "starting_mid": 100.0,
                    "starting_spread_bps": 4.0,
                },
                "participants": [],
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["n_fills"] == 0
        assert body["n_snapshots"] >= 1
        assert body["initial_mid"] > 0
        assert body["final_mid"] > 0

    def test_unknown_participant_kind_rejected(self) -> None:
        r = client.post(
            "/api/esmm/sim/sandbox",
            json={
                "kernel": {
                    "duration_sec": 0.05,
                    "tick_interval_sec": 0.001,
                    "snapshot_interval_sec": 0.01,
                    "enable_latency": False,
                },
                "participants": [{"kind": "alien", "weight": 1.0, "params": {}}],
            },
        )
        assert r.status_code == 400

    def test_invalid_kernel_config_rejected(self) -> None:
        r = client.post(
            "/api/esmm/sim/sandbox",
            json={
                "kernel": {"duration_sec": 0, "tick_interval_sec": 0.001},
                "participants": [],
            },
        )
        # FastAPI validation should reject duration_sec <= 0.
        assert r.status_code in (400, 422)

    def test_returns_pnl_and_inventory(self) -> None:
        r = client.post(
            "/api/esmm/sim/sandbox",
            json={
                "kernel": {
                    "duration_sec": 0.05,
                    "tick_interval_sec": 0.001,
                    "snapshot_interval_sec": 0.01,
                    "enable_latency": False,
                },
                "participants": [],
            },
        )
        body = r.json()
        # House seed orders should appear in inventory tracking when
        # they fill — but with no aggressors they stay flat.
        assert "pnl_per_participant" in body
        assert "inventory_per_participant" in body


class TestArenaEndpoint:
    def test_empty_strategies_rejected(self) -> None:
        r = client.post(
            "/api/esmm/sim/arena",
            json={
                "kernel": {
                    "duration_sec": 0.05,
                    "tick_interval_sec": 0.001,
                    "enable_latency": False,
                },
                "flow": [],
                "strategies": [],
            },
        )
        assert r.status_code == 400

    def test_unknown_strategy_kind_rejected(self) -> None:
        r = client.post(
            "/api/esmm/sim/arena",
            json={
                "kernel": {
                    "duration_sec": 0.05,
                    "tick_interval_sec": 0.001,
                    "enable_latency": False,
                },
                "flow": [],
                "strategies": [
                    {
                        "strategy_id": "alpha",
                        "participant": {"kind": "alien", "weight": 1.0, "params": {}},
                    }
                ],
            },
        )
        assert r.status_code == 400


class TestAgenticEndpoint:
    def test_503_when_market_maker_not_registered(self) -> None:
        # If MarketMakerParticipant hasn't been registered yet, the
        # endpoint should return 503 with a clear message rather than
        # 500.
        from src.api.esmm_sim_router import _PARTICIPANT_REGISTRY

        if "market_maker" in _PARTICIPANT_REGISTRY:
            # MM is registered — skip this test
            return

        r = client.post(
            "/api/esmm/sim/agentic",
            json={
                "scenario_id": "hot_cpi",
                "baseline_config": {"symbol": "SPY"},
                "flow": [],
                "max_iterations": 2,
                "duration_override_sec": 0.05,
            },
        )
        assert r.status_code == 503
        assert "MarketMakerParticipant" in r.json()["detail"]
