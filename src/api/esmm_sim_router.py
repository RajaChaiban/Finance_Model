"""FastAPI router for the ESMM simulation platform (Phase 3).

Endpoints under ``/api/esmm/sim/*``:

* GET  ``/scenarios``                — list curated scenarios from library.yaml
* GET  ``/participants``             — list available participant archetypes
* POST ``/sandbox``                  — run one synthetic scenario
* POST ``/arena``                    — N-strategy bake-off

The router is intentionally lightweight: it constructs typed
request/response models, builds a kernel/arena, runs it, and serialises
the result. Heavy lifting lives in :mod:`src.esmm.sim`.

Note: participant **construction** lives behind a small registry so the
endpoint stays decoupled from individual participant class names. Until
real participants land (Phase 2 agent finishing in parallel), the
registry uses placeholder factories that emit no orders — the endpoint
shape is still meaningful for the frontend to consume.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.esmm.sim.arena import Arena, ArenaConfig
from src.esmm.sim.kernel import Kernel, KernelConfig
from src.esmm.sim.latency import LatencyConfig
from src.esmm.sim.participants.base import Participant
from src.esmm.sim.risk import RiskLimits
from src.esmm.sim.scenarios.loader import load_library

router = APIRouter(prefix="/api/esmm/sim", tags=["esmm-sim"])


# ---------------------------------------------------------------------------
# Participant registry
# ---------------------------------------------------------------------------
ParticipantFactory = Callable[[dict[str, Any]], Participant]
_PARTICIPANT_REGISTRY: dict[str, ParticipantFactory] = {}


def register_participant(kind: str, factory: ParticipantFactory) -> None:
    """Register a participant factory under ``kind``.

    Called once per archetype at import time (typically from the
    participant module itself). Factories accept a ``params`` dict
    (matches the YAML schema) and return a Participant.
    """
    _PARTICIPANT_REGISTRY[kind] = factory


def _try_register_known_participants() -> None:
    """Best-effort registration of the v1 participants.

    Each import is guarded — when the agent-built participants land
    they'll appear; otherwise the endpoint reports an empty registry.
    """
    try:
        from src.esmm.sim.participants.noise import NoiseTrader  # type: ignore

        def _noise_factory(params: dict[str, Any]) -> Participant:
            return NoiseTrader(participant_id=params.pop("participant_id", "noise"), **params)

        register_participant("noise", _noise_factory)
    except Exception:
        pass

    try:
        from src.esmm.sim.participants.informed import InformedTrader  # type: ignore

        def _informed_factory(params: dict[str, Any]) -> Participant:
            return InformedTrader(
                participant_id=params.pop("participant_id", "informed"), **params
            )

        register_participant("informed", _informed_factory)
    except Exception:
        pass

    try:
        from src.esmm.sim.participants.replay_taker import ReplayTaker  # type: ignore

        def _replay_factory(params: dict[str, Any]) -> Participant:
            return ReplayTaker(
                participant_id=params.pop("participant_id", "replay"), **params
            )

        register_participant("replay_taker", _replay_factory)
    except Exception:
        pass

    try:
        from src.esmm.schemas import MarketMakingConfig as _MMConfig  # type: ignore
        from src.esmm.sim.participants.market_maker import (  # type: ignore
            MarketMakerParticipant,
        )

        def _mm_factory(params: dict[str, Any]) -> Participant:
            pid = params.pop("participant_id", "mm")
            cfg = params.pop("config", None)
            if cfg is None:
                # Build config from any remaining params (raw dict).
                cfg = _MMConfig(**params)
                params = {}
            elif isinstance(cfg, dict):
                cfg = _MMConfig(**cfg)
            return MarketMakerParticipant(
                participant_id=pid, config=cfg, **params
            )

        register_participant("market_maker", _mm_factory)
    except Exception:
        pass


_try_register_known_participants()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class ParticipantSpecBody(BaseModel):
    kind: str
    weight: float = 1.0
    params: dict[str, Any] = Field(default_factory=dict)


class KernelConfigBody(BaseModel):
    duration_sec: float = Field(60.0, gt=0)
    tick_interval_sec: float = Field(0.01, gt=0)
    snapshot_interval_sec: float = Field(0.05, gt=0)
    seed: Optional[int] = None
    enable_latency: bool = True
    fee_bps: float = -0.2
    symbol: str = "SPY"
    starting_mid: float = Field(100.0, gt=0)
    starting_spread_bps: float = Field(4.0, gt=0)


class LatencyConfigBody(BaseModel):
    submit_mean_ms: float = 15.0
    submit_sigma_ms: float = 8.0
    cancel_mean_ms: float = 12.0
    cancel_sigma_ms: float = 6.0


class SandboxRequest(BaseModel):
    """Run a single synthetic scenario."""

    kernel: KernelConfigBody
    latency: Optional[LatencyConfigBody] = None
    participants: list[ParticipantSpecBody] = Field(default_factory=list)
    risk: Optional[dict[str, float]] = None


class SandboxResponse(BaseModel):
    duration_sec: float
    n_ticks: int
    n_snapshots: int
    n_orders_submitted: int
    n_fills: int
    initial_mid: float
    final_mid: float
    pnl_per_participant: dict[str, float]
    inventory_per_participant: dict[str, float]
    halted_at: Optional[float] = None
    n_risk_breaches: int = 0


class ScenarioInfo(BaseModel):
    scenario_id: str
    description: str
    duration_sec: float
    regime_label: str
    starting_mid: float
    starting_spread_bps: float
    n_participants: int
    n_events: int


class ParticipantInfo(BaseModel):
    kind: str
    registered: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _kernel_config_from_body(body: KernelConfigBody) -> KernelConfig:
    return KernelConfig(
        duration_sec=body.duration_sec,
        tick_interval_sec=body.tick_interval_sec,
        snapshot_interval_sec=body.snapshot_interval_sec,
        seed=body.seed,
        enable_latency=body.enable_latency,
        fee_bps=body.fee_bps,
        symbol=body.symbol,
        starting_mid=body.starting_mid,
        starting_spread_bps=body.starting_spread_bps,
    )


def _latency_from_body(body: Optional[LatencyConfigBody], seed: Optional[int]) -> LatencyConfig:
    if body is None:
        return LatencyConfig(seed=seed)
    return LatencyConfig(
        submit_mean_ms=body.submit_mean_ms,
        submit_sigma_ms=body.submit_sigma_ms,
        cancel_mean_ms=body.cancel_mean_ms,
        cancel_sigma_ms=body.cancel_sigma_ms,
        seed=seed,
    )


def _risk_from_dict(d: Optional[dict[str, float]]) -> Optional[RiskLimits]:
    if d is None:
        return None
    return RiskLimits(**d)


def _build_participant(spec: ParticipantSpecBody) -> Participant:
    if spec.kind not in _PARTICIPANT_REGISTRY:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown participant kind {spec.kind!r}. "
                f"Registered: {sorted(_PARTICIPANT_REGISTRY.keys())}. "
                "If this is a v1 archetype (noise/informed/replay_taker), "
                "the participant module may not be installed yet."
            ),
        )
    factory = _PARTICIPANT_REGISTRY[spec.kind]
    params = dict(spec.params)
    return factory(params)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/scenarios", response_model=list[ScenarioInfo])
def list_scenarios() -> list[ScenarioInfo]:
    lib = load_library()
    return [
        ScenarioInfo(
            scenario_id=sid,
            description=sc.description,
            duration_sec=sc.duration_sec,
            regime_label=sc.regime_label,
            starting_mid=sc.starting_mid,
            starting_spread_bps=sc.starting_spread_bps,
            n_participants=len(sc.participants),
            n_events=len(sc.events),
        )
        for sid, sc in lib.items()
    ]


@router.get("/participants", response_model=list[ParticipantInfo])
def list_participants() -> list[ParticipantInfo]:
    return [
        ParticipantInfo(kind=k, registered=True)
        for k in sorted(_PARTICIPANT_REGISTRY.keys())
    ]


@router.post("/sandbox", response_model=SandboxResponse)
def run_sandbox(req: SandboxRequest) -> SandboxResponse:
    kc = _kernel_config_from_body(req.kernel)
    latency_cfg = _latency_from_body(req.latency, kc.seed)
    risk_limits = _risk_from_dict(req.risk)

    kernel = Kernel(kc, latency_config=latency_cfg, risk_limits=risk_limits)
    for spec in req.participants:
        kernel.register(_build_participant(spec))

    result = kernel.run()
    return SandboxResponse(
        duration_sec=result.duration_sec,
        n_ticks=result.n_ticks,
        n_snapshots=result.n_snapshots,
        n_orders_submitted=result.n_orders_submitted,
        n_fills=result.n_fills,
        initial_mid=result.initial_mid,
        final_mid=result.final_mid,
        pnl_per_participant=result.pnl_per_participant,
        inventory_per_participant=result.inventory_per_participant,
        halted_at=result.halted_at,
        n_risk_breaches=len(result.risk_breaches),
    )


# ---------------------------------------------------------------------------
# Arena bake-off endpoint
# ---------------------------------------------------------------------------
class ArenaStrategyBody(BaseModel):
    """One strategy entry in an arena bake-off.

    The strategy is constructed as a single participant from the registry,
    just like Sandbox participants. Different strategies usually share
    the same kind (e.g. all "mm") with different ``params``.
    """

    strategy_id: str
    participant: ParticipantSpecBody


class ArenaRequest(BaseModel):
    kernel: KernelConfigBody
    latency: Optional[LatencyConfigBody] = None
    flow: list[ParticipantSpecBody] = Field(default_factory=list)
    strategies: list[ArenaStrategyBody]
    risk: Optional[dict[str, float]] = None


class ArenaStrategyResponse(BaseModel):
    strategy_id: str
    pnl: float
    final_inventory: float
    n_fills: int
    n_orders_submitted: int
    sharpe_approx: float
    max_drawdown: float
    edge_over_passive: float
    halted_at: Optional[float] = None


class ArenaResponse(BaseModel):
    run_id: str
    strategies: list[str]
    per_strategy: list[ArenaStrategyResponse]
    comparison: dict[str, Any]


@router.post("/arena", response_model=ArenaResponse)
def run_arena(req: ArenaRequest) -> ArenaResponse:
    if not req.strategies:
        raise HTTPException(status_code=400, detail="At least one strategy is required")

    kc = _kernel_config_from_body(req.kernel)
    latency_cfg = _latency_from_body(req.latency, kc.seed)
    risk_limits = _risk_from_dict(req.risk)

    def flow_factory(_kc) -> list[Participant]:
        return [_build_participant(spec) for spec in req.flow]

    strategy_factories = {}
    for entry in req.strategies:
        # Captured per-iter to avoid late-binding issues.
        def _make(_kc, _spec=entry.participant):
            # Stamp the strategy_id as the participant_id so downstream
            # bookkeeping is consistent.
            params = dict(_spec.params)
            params["participant_id"] = entry.strategy_id
            return _build_participant(
                ParticipantSpecBody(kind=_spec.kind, weight=_spec.weight, params=params)
            )

        strategy_factories[entry.strategy_id] = _make

    arena = Arena(
        config=ArenaConfig(
            kernel_config=kc,
            latency_config=latency_cfg,
            risk_limits=risk_limits,
            seed=kc.seed,
            flow_factory=flow_factory,
        ),
        strategies=strategy_factories,
    )
    result = arena.run()
    return ArenaResponse(
        run_id=result.run_id,
        strategies=result.strategies,
        per_strategy=[
            ArenaStrategyResponse(**s.to_dict()) for s in result.per_strategy
        ],
        comparison=result.comparison,
    )


# ---------------------------------------------------------------------------
# Agentic loop endpoint
# ---------------------------------------------------------------------------
class AgenticRequest(BaseModel):
    scenario_id: str
    baseline_config: dict[str, Any]
    flow: list[ParticipantSpecBody] = Field(default_factory=list)
    acceptance_score: float = 70.0
    max_iterations: int = 5
    base_seed: int = 42
    duration_override_sec: Optional[float] = None


class AgenticIterationResponse(BaseModel):
    iteration: int
    regime: str
    score: float
    accepted: bool
    proposed_config: dict[str, Any]
    total_pnl: float
    spread_capture_pnl: float
    adverse_selection_pnl: float
    hedge_pnl: float
    inventory_pnl: float
    fees_pnl: float


class AgenticResponse(BaseModel):
    converged: bool
    stopped_reason: str
    iterations: list[AgenticIterationResponse]
    best_iteration: Optional[int] = None
    best_score: Optional[float] = None


@router.post("/agentic", response_model=AgenticResponse)
def run_agentic_loop(req: AgenticRequest) -> AgenticResponse:
    """Run the agentic observe→propose→score loop against a scenario."""
    from src.agents.esmm.sim_orchestrator import AgenticSimOrchestrator
    from src.esmm.schemas import MarketMakingConfig

    # We need an MM participant kind in the registry. Surface a clear
    # error if the v1 MarketMakerParticipant hasn't landed yet.
    if "market_maker" not in _PARTICIPANT_REGISTRY:
        raise HTTPException(
            status_code=503,
            detail=(
                "MarketMakerParticipant not registered. Phase-4 v1 not yet "
                "deployed. Expected kind='market_maker' in the participant registry."
            ),
        )

    baseline = MarketMakingConfig(**req.baseline_config)

    def mm_factory(cfg: MarketMakingConfig) -> Participant:
        return _PARTICIPANT_REGISTRY["market_maker"](
            {"participant_id": "mm", "config": cfg}
        )

    def flow_factory(_kc, _sc) -> list[Participant]:
        return [_build_participant(spec) for spec in req.flow]

    orch = AgenticSimOrchestrator(
        baseline=baseline,
        mm_factory=mm_factory,
        flow_factory=flow_factory,
        acceptance_score=req.acceptance_score,
        max_iterations=req.max_iterations,
        base_seed=req.base_seed,
        duration_override_sec=req.duration_override_sec,
    )
    run_result = orch.run(req.scenario_id)

    iter_rows = []
    for d in run_result.history:
        iter_rows.append(
            AgenticIterationResponse(
                iteration=d.iteration,
                regime=d.observation.regime.value,
                score=d.score.score,
                accepted=d.accepted,
                proposed_config=d.proposal.config.model_dump(),
                total_pnl=d.tca.total_pnl,
                spread_capture_pnl=d.tca.spread_capture_pnl,
                adverse_selection_pnl=d.tca.adverse_selection_pnl,
                hedge_pnl=d.tca.hedge_pnl,
                inventory_pnl=d.tca.inventory_pnl,
                fees_pnl=d.tca.fees_pnl,
            )
        )

    best_idx = (
        run_result.best_decision.iteration if run_result.best_decision else None
    )
    best_score = (
        run_result.best_decision.score.score if run_result.best_decision else None
    )

    return AgenticResponse(
        converged=run_result.converged,
        stopped_reason=run_result.stopped_reason,
        iterations=iter_rows,
        best_iteration=best_idx,
        best_score=best_score,
    )


__all__ = ["router", "register_participant"]
