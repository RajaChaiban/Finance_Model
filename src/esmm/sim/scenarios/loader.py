"""Scenario YAML loader.

Reads ``library.yaml`` next to this module and produces typed
:class:`Scenario` records. The kernel uses these to configure the
participant mix, scheduled events, latency overrides, and starting
mid/spread for one run.

Parsing is deliberately permissive: missing optional fields fall back
to sensible defaults so the YAML stays human-friendly. Required fields
that are missing raise :class:`ScenarioParseError` with a path to the
offending entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


_VALID_REGIMES = {"CALM", "TRENDING", "VOLATILE", "STRESS"}
_VALID_PARTICIPANT_KINDS = {
    "noise",
    "informed",
    "momentum",
    "mean_reverter",
    "news_shock",
    "replay_taker",
}
_VALID_EVENT_KINDS = {
    "gap",
    "halt",
    "vol_spike",
    "news_print",
    "spread_widen",
}


class ScenarioParseError(ValueError):
    """Raised when the YAML doesn't match the expected schema."""


@dataclass(frozen=True)
class ParticipantSpec:
    kind: str
    weight: float
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EventSpec:
    ts_offset_sec: float
    kind: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LatencyOverrides:
    submit_mean_ms: float | None = None
    submit_sigma_ms: float | None = None
    cancel_mean_ms: float | None = None
    cancel_sigma_ms: float | None = None


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    description: str
    duration_sec: float
    regime_label: str
    starting_mid: float
    starting_spread_bps: float
    participants: list[ParticipantSpec]
    events: list[EventSpec] = field(default_factory=list)
    latency_overrides: LatencyOverrides = field(default_factory=LatencyOverrides)


def _default_library_path() -> Path:
    return Path(__file__).parent / "library.yaml"


def load_library(path: Path | None = None) -> dict[str, Scenario]:
    """Load and validate ``library.yaml``. Returns ``{id: Scenario}``.

    Args:
        path: optional override (mainly for tests). Defaults to the
            ``library.yaml`` sitting next to this module.

    Raises:
        ScenarioParseError: missing/invalid fields with a path to the bad
            entry. The error message is intentionally specific so YAML
            edits fail loudly rather than silently producing garbage runs.
    """
    if path is None:
        path = _default_library_path()
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    if "scenarios" not in raw:
        raise ScenarioParseError(f"{path}: missing top-level 'scenarios' key")

    scenarios_raw = raw["scenarios"] or {}
    if not isinstance(scenarios_raw, dict):
        raise ScenarioParseError(
            f"{path}: 'scenarios' must be a mapping; got {type(scenarios_raw).__name__}"
        )

    out: dict[str, Scenario] = {}
    for sid, entry in scenarios_raw.items():
        out[sid] = _parse_scenario(sid, entry)
    return out


def _parse_scenario(sid: str, entry: Any) -> Scenario:
    if not isinstance(entry, dict):
        raise ScenarioParseError(f"scenario {sid!r}: expected mapping, got {type(entry).__name__}")

    def required(name: str) -> Any:
        if name not in entry:
            raise ScenarioParseError(f"scenario {sid!r}: missing required field {name!r}")
        return entry[name]

    description = required("description")
    duration = float(required("duration_sec"))
    regime = required("regime_label")
    if regime not in _VALID_REGIMES:
        raise ScenarioParseError(
            f"scenario {sid!r}: regime_label={regime!r} must be one of {sorted(_VALID_REGIMES)}"
        )
    starting_mid = float(required("starting_mid"))
    starting_spread = float(required("starting_spread_bps"))

    participants_raw = entry.get("participants", [])
    participants = [_parse_participant(sid, i, p) for i, p in enumerate(participants_raw)]
    if not participants:
        raise ScenarioParseError(f"scenario {sid!r}: must declare at least one participant")
    total_weight = sum(p.weight for p in participants)
    if total_weight <= 0:
        raise ScenarioParseError(f"scenario {sid!r}: total participant weight must be > 0")

    events_raw = entry.get("events", []) or []
    events = [_parse_event(sid, i, e) for i, e in enumerate(events_raw)]

    latency = _parse_latency(sid, entry.get("latency_overrides"))

    return Scenario(
        scenario_id=sid,
        description=description,
        duration_sec=duration,
        regime_label=regime,
        starting_mid=starting_mid,
        starting_spread_bps=starting_spread,
        participants=participants,
        events=events,
        latency_overrides=latency,
    )


def _parse_participant(sid: str, idx: int, raw: Any) -> ParticipantSpec:
    if not isinstance(raw, dict):
        raise ScenarioParseError(
            f"scenario {sid!r} participant[{idx}]: expected mapping, got {type(raw).__name__}"
        )
    kind = raw.get("kind")
    if kind not in _VALID_PARTICIPANT_KINDS:
        raise ScenarioParseError(
            f"scenario {sid!r} participant[{idx}]: kind={kind!r} must be one of "
            f"{sorted(_VALID_PARTICIPANT_KINDS)}"
        )
    weight = float(raw.get("weight", 1.0))
    if weight < 0:
        raise ScenarioParseError(
            f"scenario {sid!r} participant[{idx}]: weight must be >= 0, got {weight}"
        )
    params = raw.get("params") or {}
    if not isinstance(params, dict):
        raise ScenarioParseError(
            f"scenario {sid!r} participant[{idx}]: params must be a mapping"
        )
    return ParticipantSpec(kind=kind, weight=weight, params=dict(params))


def _parse_event(sid: str, idx: int, raw: Any) -> EventSpec:
    if not isinstance(raw, dict):
        raise ScenarioParseError(
            f"scenario {sid!r} event[{idx}]: expected mapping, got {type(raw).__name__}"
        )
    kind = raw.get("kind")
    if kind not in _VALID_EVENT_KINDS:
        raise ScenarioParseError(
            f"scenario {sid!r} event[{idx}]: kind={kind!r} must be one of "
            f"{sorted(_VALID_EVENT_KINDS)}"
        )
    if "ts_offset_sec" not in raw:
        raise ScenarioParseError(f"scenario {sid!r} event[{idx}]: missing ts_offset_sec")
    ts = float(raw["ts_offset_sec"])
    if ts < 0:
        raise ScenarioParseError(
            f"scenario {sid!r} event[{idx}]: ts_offset_sec must be >= 0, got {ts}"
        )
    params = raw.get("params") or {}
    if not isinstance(params, dict):
        raise ScenarioParseError(
            f"scenario {sid!r} event[{idx}]: params must be a mapping"
        )
    return EventSpec(ts_offset_sec=ts, kind=kind, params=dict(params))


def _parse_latency(sid: str, raw: Any) -> LatencyOverrides:
    if raw is None:
        return LatencyOverrides()
    if not isinstance(raw, dict):
        raise ScenarioParseError(
            f"scenario {sid!r}: latency_overrides must be a mapping, got {type(raw).__name__}"
        )
    return LatencyOverrides(
        submit_mean_ms=_opt_float(raw.get("submit_mean_ms")),
        submit_sigma_ms=_opt_float(raw.get("submit_sigma_ms")),
        cancel_mean_ms=_opt_float(raw.get("cancel_mean_ms")),
        cancel_sigma_ms=_opt_float(raw.get("cancel_sigma_ms")),
    )


def _opt_float(v: Any) -> float | None:
    if v is None:
        return None
    return float(v)


__all__ = [
    "EventSpec",
    "LatencyOverrides",
    "ParticipantSpec",
    "Scenario",
    "ScenarioParseError",
    "load_library",
]
