"""Tests for the scenarios loader + the curated YAML library.

Covers:
  * the bundled library.yaml loads cleanly and contains the 6 documented
    scenarios with the right structure
  * required fields raise ScenarioParseError with a useful message
  * invalid regime / participant kind / event kind are rejected
  * negative duration / weight / ts_offset rejected
  * latency_overrides parses Nones as defaults
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from src.esmm.sim.scenarios.loader import (
    Scenario,
    ScenarioParseError,
    load_library,
)


def _write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "scenarios.yaml"
    p.write_text(dedent(body), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Curated library
# ---------------------------------------------------------------------------
class TestBundledLibrary:
    """The shipped library.yaml is the user's contract — verify it."""

    def test_loads_six_scenarios(self) -> None:
        lib = load_library()
        expected = {
            "flash_crash_2010",
            "covid_mar_2020",
            "hot_cpi",
            "fomc_surprise",
            "opex_pin",
            "liquidity_drought",
        }
        assert expected.issubset(set(lib.keys()))

    def test_every_scenario_well_formed(self) -> None:
        lib = load_library()
        for sid, sc in lib.items():
            assert isinstance(sc, Scenario), sid
            assert sc.duration_sec > 0, sid
            assert sc.starting_mid > 0, sid
            assert sc.starting_spread_bps > 0, sid
            assert sc.regime_label in {"CALM", "TRENDING", "VOLATILE", "STRESS"}
            assert sc.participants, f"{sid} has no participants"
            assert sum(p.weight for p in sc.participants) > 0, sid

    def test_flash_crash_has_vol_spike_and_gap(self) -> None:
        lib = load_library()
        sc = lib["flash_crash_2010"]
        kinds = {e.kind for e in sc.events}
        assert "vol_spike" in kinds
        assert "gap" in kinds
        # Latency should be elevated (exchange congestion).
        assert sc.latency_overrides.submit_mean_ms is not None
        assert sc.latency_overrides.submit_mean_ms > 20

    def test_opex_pin_has_mean_reverter_heavy_weight(self) -> None:
        lib = load_library()
        sc = lib["opex_pin"]
        weights = {p.kind: p.weight for p in sc.participants}
        # In a pinning regime, mean-reverters should be at least 0.3
        assert weights.get("mean_reverter", 0.0) >= 0.3

    def test_liquidity_drought_has_low_arrival_rate(self) -> None:
        lib = load_library()
        sc = lib["liquidity_drought"]
        noise = next(p for p in sc.participants if p.kind == "noise")
        # Low arrival rate is the defining feature
        assert noise.params.get("arrival_rate_hz", 99) < 1.0


# ---------------------------------------------------------------------------
# Parse errors — each scenario file edit must fail loudly, not silently.
# ---------------------------------------------------------------------------
class TestSchemaErrors:
    def test_missing_top_level(self, tmp_path: Path) -> None:
        p = _write_yaml(tmp_path, "other_key: 1")
        with pytest.raises(ScenarioParseError, match="scenarios"):
            load_library(p)

    def test_scenarios_not_a_mapping(self, tmp_path: Path) -> None:
        p = _write_yaml(tmp_path, "scenarios:\n  - foo")
        with pytest.raises(ScenarioParseError, match="mapping"):
            load_library(p)

    def test_missing_required_field(self, tmp_path: Path) -> None:
        p = _write_yaml(
            tmp_path,
            """
            scenarios:
              bad:
                description: oops
                duration_sec: 10
                regime_label: CALM
                starting_mid: 100
                # missing starting_spread_bps
                participants:
                  - kind: noise
                    weight: 1.0
            """,
        )
        with pytest.raises(ScenarioParseError, match="starting_spread_bps"):
            load_library(p)

    def test_invalid_regime(self, tmp_path: Path) -> None:
        p = _write_yaml(
            tmp_path,
            """
            scenarios:
              bad:
                description: oops
                duration_sec: 10
                regime_label: NUCLEAR
                starting_mid: 100
                starting_spread_bps: 3
                participants:
                  - kind: noise
                    weight: 1.0
            """,
        )
        with pytest.raises(ScenarioParseError, match="regime_label"):
            load_library(p)

    def test_invalid_participant_kind(self, tmp_path: Path) -> None:
        p = _write_yaml(
            tmp_path,
            """
            scenarios:
              bad:
                description: oops
                duration_sec: 10
                regime_label: CALM
                starting_mid: 100
                starting_spread_bps: 3
                participants:
                  - kind: alien
                    weight: 1.0
            """,
        )
        with pytest.raises(ScenarioParseError, match="kind"):
            load_library(p)

    def test_invalid_event_kind(self, tmp_path: Path) -> None:
        p = _write_yaml(
            tmp_path,
            """
            scenarios:
              bad:
                description: oops
                duration_sec: 10
                regime_label: CALM
                starting_mid: 100
                starting_spread_bps: 3
                participants:
                  - kind: noise
                    weight: 1.0
                events:
                  - ts_offset_sec: 5
                    kind: explosion
            """,
        )
        with pytest.raises(ScenarioParseError, match="kind"):
            load_library(p)

    def test_negative_event_ts_rejected(self, tmp_path: Path) -> None:
        p = _write_yaml(
            tmp_path,
            """
            scenarios:
              bad:
                description: oops
                duration_sec: 10
                regime_label: CALM
                starting_mid: 100
                starting_spread_bps: 3
                participants:
                  - kind: noise
                    weight: 1.0
                events:
                  - ts_offset_sec: -1
                    kind: gap
            """,
        )
        with pytest.raises(ScenarioParseError, match="ts_offset_sec"):
            load_library(p)

    def test_negative_participant_weight_rejected(self, tmp_path: Path) -> None:
        p = _write_yaml(
            tmp_path,
            """
            scenarios:
              bad:
                description: oops
                duration_sec: 10
                regime_label: CALM
                starting_mid: 100
                starting_spread_bps: 3
                participants:
                  - kind: noise
                    weight: -1
            """,
        )
        with pytest.raises(ScenarioParseError, match="weight"):
            load_library(p)

    def test_zero_total_weight_rejected(self, tmp_path: Path) -> None:
        p = _write_yaml(
            tmp_path,
            """
            scenarios:
              bad:
                description: oops
                duration_sec: 10
                regime_label: CALM
                starting_mid: 100
                starting_spread_bps: 3
                participants:
                  - kind: noise
                    weight: 0
            """,
        )
        with pytest.raises(ScenarioParseError, match="weight"):
            load_library(p)

    def test_latency_overrides_partial(self, tmp_path: Path) -> None:
        p = _write_yaml(
            tmp_path,
            """
            scenarios:
              ok:
                description: partial latency
                duration_sec: 10
                regime_label: CALM
                starting_mid: 100
                starting_spread_bps: 3
                participants:
                  - kind: noise
                    weight: 1.0
                latency_overrides:
                  submit_mean_ms: 25
            """,
        )
        lib = load_library(p)
        ovr = lib["ok"].latency_overrides
        assert ovr.submit_mean_ms == 25
        assert ovr.submit_sigma_ms is None
        assert ovr.cancel_mean_ms is None

    def test_no_participants_rejected(self, tmp_path: Path) -> None:
        p = _write_yaml(
            tmp_path,
            """
            scenarios:
              bad:
                description: oops
                duration_sec: 10
                regime_label: CALM
                starting_mid: 100
                starting_spread_bps: 3
                participants: []
            """,
        )
        with pytest.raises(ScenarioParseError, match="participant"):
            load_library(p)
