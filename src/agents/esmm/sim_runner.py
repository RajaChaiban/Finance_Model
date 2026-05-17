"""Adapter: run one sim arena and produce a legacy ``BacktestResult``.

The existing :class:`~src.agents.esmm.orchestrator.AgenticESMMOrchestrator`
was built against ``run_backtest`` from ``src/esmm/backtest.py``. To
plug the new sim kernel into the agentic loop without rewriting the
orchestrator, this module exposes a function with the same return
shape — fed by ``Kernel.run()`` instead of the legacy snapshot replay.

What this gives the Layer-C agentic loop:

  * a *real* limit-order book under it (queue model, latency, hostile
    flow, scripted shocks)
  * its existing ``observe → propose → score`` loop still works because
    the input shape (``MarketMakingConfig`` → ``BacktestResult``) is
    preserved

What it does NOT give:

  * the strategist still observes a *historical* regime per call.
    Real-time re-observation inside the kernel is phase 5+.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from src.esmm.backtest import BacktestResult
from src.esmm.schemas import Fill, MarketMakingConfig, OrderBookSnapshot, Side
from src.esmm.sim.kernel import Kernel, KernelConfig, KernelResult
from src.esmm.sim.latency import LatencyConfig
from src.esmm.sim.participants.base import Participant
from src.esmm.sim.scenarios.loader import Scenario, load_library
from src.esmm.tca import attribute_pnl


MarketMakerFactory = Callable[[MarketMakingConfig], Participant]
"""Builds the MarketMakerParticipant under test from a config. Defers the
import so this module doesn't hard-depend on the participant class."""

FlowFactory = Callable[[KernelConfig, Scenario], list[Participant]]
"""Optional: builds ambient flow participants for a given scenario."""


@dataclass
class SimRunnerOutput:
    """Wraps ``BacktestResult`` with the raw kernel result for callers
    that want access to per-strategy fills, breaches, etc."""

    backtest_result: BacktestResult
    kernel_result: KernelResult
    strategy_fills: list[Fill]


def run_sim_iteration(
    *,
    scenario_id: str,
    config: MarketMakingConfig,
    mm_factory: MarketMakerFactory,
    flow_factory: Optional[FlowFactory] = None,
    seed: int = 42,
    duration_override_sec: Optional[float] = None,
    latency_config: Optional[LatencyConfig] = None,
) -> SimRunnerOutput:
    """Run one sim with ``config`` against ``scenario_id``.

    Args:
        scenario_id: key in ``src/esmm/sim/scenarios/library.yaml``.
        config: the ``MarketMakingConfig`` under test. Passed to
            ``mm_factory`` to construct the strategy participant.
        mm_factory: returns the strategy Participant given the config.
        flow_factory: returns the ambient flow participants. If None,
            the run is "MM versus seed-book only" — useful as a sanity
            baseline but not realistic.
        seed: kernel seed. Same seed across iterations → same exogenous
            flow seen by every config (the property the agentic loop
            depends on for fair scoring).
        duration_override_sec: overrides the scenario's duration. Useful
            when iterating an agent fast.
        latency_config: optional latency override; otherwise derived
            from the scenario's ``latency_overrides``.

    Returns:
        :class:`SimRunnerOutput` containing both the legacy
        ``BacktestResult`` (consumed by the orchestrator) and the raw
        ``KernelResult`` (for richer downstream analysis).
    """
    lib = load_library()
    if scenario_id not in lib:
        raise KeyError(
            f"scenario {scenario_id!r} not in library; available: {sorted(lib.keys())}"
        )
    scenario = lib[scenario_id]

    duration = duration_override_sec if duration_override_sec is not None else scenario.duration_sec
    kernel_config = KernelConfig(
        duration_sec=duration,
        seed=seed,
        symbol=config.symbol,
        starting_mid=scenario.starting_mid,
        starting_spread_bps=scenario.starting_spread_bps,
    )

    # Apply scenario latency overrides on top of any supplied config.
    if latency_config is None:
        lo = scenario.latency_overrides
        latency_config = LatencyConfig(
            submit_mean_ms=lo.submit_mean_ms if lo.submit_mean_ms is not None else 15.0,
            submit_sigma_ms=lo.submit_sigma_ms if lo.submit_sigma_ms is not None else 8.0,
            cancel_mean_ms=lo.cancel_mean_ms if lo.cancel_mean_ms is not None else 12.0,
            cancel_sigma_ms=lo.cancel_sigma_ms if lo.cancel_sigma_ms is not None else 6.0,
            seed=seed,
        )

    kernel = Kernel(kernel_config, latency_config=latency_config)

    # Flow first (so the MM sees a market with non-trivial flow).
    if flow_factory is not None:
        for p in flow_factory(kernel_config, scenario):
            kernel.register(p)

    # MM strategy under test.
    mm_participant = mm_factory(config)
    kernel.register(mm_participant)
    strat_id = mm_participant.participant_id

    kernel_result = kernel.run()

    # ------------------------------------------------------------------
    # Build a BacktestResult-compatible payload.
    # ------------------------------------------------------------------
    # We need *strategy* fills, not all fills. The kernel emits every
    # fill to result.fills (both sides). We filter by counterparty +
    # consistency with the strategy's inventory delta. Simpler: get the
    # strategy's accepted fills by looking at the participant's internal
    # state if it exposes one (MarketMakerParticipant tracks them);
    # otherwise we reconstruct via the inventory delta path.
    strat_fills = _extract_strategy_fills(kernel_result, mm_participant, strat_id)

    tca = attribute_pnl(strat_fills, kernel_result.snapshots)

    final_mid = kernel_result.final_mid
    final_inv = kernel_result.inventory_per_participant.get(strat_id, 0.0)
    realised = kernel_result.pnl_per_participant.get(strat_id, 0.0) - (final_inv * final_mid)
    unrealised = final_inv * final_mid

    bt_result = BacktestResult(
        quotes=[],  # not surfaced through the kernel yet (phase-5)
        fills=strat_fills,
        mid_path=[(s.ts, _mid_of(s)) for s in kernel_result.snapshots],
        inventory_path=[],  # not tracked yet
        final_inventory=final_inv,
        final_mid=final_mid,
        realised_pnl=realised,
        unrealised_pnl=unrealised,
        total_pnl=realised + unrealised,
        n_quotes=0,
        n_fills=len(strat_fills),
        tca=tca.model_dump() if hasattr(tca, "model_dump") else tca.dict(),
    )

    return SimRunnerOutput(
        backtest_result=bt_result,
        kernel_result=kernel_result,
        strategy_fills=strat_fills,
    )


def _extract_strategy_fills(
    kernel_result: KernelResult,
    participant: Participant,
    strat_id: str,
) -> list[Fill]:
    """Pick out the fills owned by the strategy participant.

    Preferred path: if the participant exposes a ``fills_received`` or
    ``recent_fills`` attribute (the v1 archetypes do), use that. It's
    the authoritative record of fills the kernel routed to this
    participant via ``on_fill``.

    Fallback: filter ``kernel_result.fills`` by ``counterparty != strat_id``.
    A fill from the strategy's POV has the strategy as the *receiver*
    and someone else as the counterparty. The kernel emits two Fills
    per trade (one per side). We pick the ones where the counterparty
    is not the strategy itself, but this is only approximate — the
    same Fill could be the strategy's own or someone else's same-trade
    counterpart. Use this only when the participant doesn't track its
    own fills.
    """
    for attr in ("fills_received", "recent_fills", "fills"):
        if hasattr(participant, attr):
            value = getattr(participant, attr)
            if isinstance(value, list) and (not value or isinstance(value[0], Fill)):
                return list(value)
    return [
        f for f in kernel_result.fills if getattr(f, "counterparty", "") != strat_id
    ]


def _mid_of(snap: OrderBookSnapshot) -> float:
    if snap.bids and snap.asks:
        return 0.5 * (snap.bids[0].price + snap.asks[0].price)
    return float("nan")


__all__ = [
    "FlowFactory",
    "MarketMakerFactory",
    "SimRunnerOutput",
    "run_sim_iteration",
]
