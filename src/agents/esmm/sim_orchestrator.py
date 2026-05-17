"""Agentic eSMM orchestrator — sim variant.

Drives the same observe → propose → score loop as
:class:`~src.agents.esmm.orchestrator.AgenticESMMOrchestrator`, but each
iteration runs a full sim against a curated scenario instead of replaying
historical snapshots. This is the marquee Layer-C wiring: the agentic
system can now stress-test itself against flash crashes, COVID
liquidity routs, hot CPI prints, etc.

Differences vs the legacy orchestrator:

* Input is a ``scenario_id`` (from ``library.yaml``) and an
  ``mm_factory`` callable, not a snapshot list.
* Regime ``observation`` is synthesised from the scenario's declared
  ``regime_label`` rather than computed from snapshots. The agentic
  strategist works the same way either way — it only consumes a
  :class:`RegimeObservation`.
* Each iteration uses an independent kernel seed (base_seed + iter * 7919)
  so we don't re-run the same path; this is what gives the loop a chance
  to see different fills under different configs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.agents.esmm.config_strategist import ConfigStrategist
from src.agents.esmm.schemas import (
    AgenticDecision,
    AgenticRunResult,
    Regime,
    RegimeObservation,
    TCAScore,
)
from src.agents.esmm.sim_runner import (
    FlowFactory,
    MarketMakerFactory,
    run_sim_iteration,
)
from src.agents.esmm.tca_critic import TCACritic
from src.esmm.schemas import MarketMakingConfig, TCABreakdown
from src.esmm.sim.scenarios.loader import Scenario, load_library


def _regime_from_label(label: str) -> Regime:
    """Map the YAML ``regime_label`` (uppercase) to the enum."""
    return Regime(label.lower())


def _scenario_to_observation(scenario: Scenario) -> RegimeObservation:
    """Synthesise a :class:`RegimeObservation` from a scenario.

    The real-time observer (``RegimeObserver``) needs snapshots to
    compute realized vol, momentum, etc. The agentic loop only consumes
    the resulting observation, so we fabricate one here using the
    scenario's authored ``regime_label`` and zero feature values. This
    is fine for the strategist's lookup table (it keys off ``regime``);
    a richer phase-5 version will run a warm-up sim and use real
    snapshots.
    """
    return RegimeObservation(
        regime=_regime_from_label(scenario.regime_label),
        rv_fast=0.0,
        rv_slow=0.0,
        momentum=0.0,
        signed_flow=0.0,
        rv_ratio=1.0,
        n_snapshots=0,
    )


class AgenticSimOrchestrator:
    """observe → propose → sim → score loop with a real LOB underneath.

    Usage::

        orch = AgenticSimOrchestrator(
            baseline=MarketMakingConfig(symbol="SPY"),
            mm_factory=lambda cfg: MarketMakerParticipant(
                participant_id="mm", config=cfg
            ),
            flow_factory=lambda kc, sc: [NoiseTrader(...), InformedTrader(...)],
        )
        result = orch.run(scenario_id="flash_crash_2010")
    """

    def __init__(
        self,
        baseline: MarketMakingConfig,
        mm_factory: MarketMakerFactory,
        flow_factory: Optional[FlowFactory] = None,
        strategist: Optional[ConfigStrategist] = None,
        critic: Optional[TCACritic] = None,
        acceptance_score: float = 70.0,
        max_iterations: int = 5,
        base_seed: int = 42,
        duration_override_sec: Optional[float] = None,
    ) -> None:
        self.baseline = baseline
        self.mm_factory = mm_factory
        self.flow_factory = flow_factory
        self.strategist = strategist or ConfigStrategist(baseline=baseline)
        self.critic = critic or TCACritic()
        self.acceptance_score = acceptance_score
        self.max_iterations = max_iterations
        self.base_seed = base_seed
        self.duration_override_sec = duration_override_sec

    def run(self, scenario_id: str) -> AgenticRunResult:
        lib = load_library()
        if scenario_id not in lib:
            return AgenticRunResult(
                history=[],
                best_decision=None,
                converged=False,
                stopped_reason=f"unknown_scenario_{scenario_id}",
            )
        scenario = lib[scenario_id]
        observation = _scenario_to_observation(scenario)

        history: list[AgenticDecision] = []
        prior_score: Optional[TCAScore] = None

        for iteration in range(self.max_iterations):
            proposal = self.strategist.propose(
                observation, prior_score=prior_score, iteration=iteration
            )
            seed = (self.base_seed + iteration * 7919) & 0x7FFFFFFF
            sim_out = run_sim_iteration(
                scenario_id=scenario_id,
                config=proposal.config,
                mm_factory=self.mm_factory,
                flow_factory=self.flow_factory,
                seed=seed,
                duration_override_sec=self.duration_override_sec,
            )
            tca_dict = sim_out.backtest_result.tca or {}
            tca = TCABreakdown(**tca_dict)
            score = self.critic.score(tca)

            decision = AgenticDecision(
                iteration=iteration,
                observation=observation,
                proposal=proposal,
                tca=tca,
                score=score,
                accepted=score.score >= self.acceptance_score,
            )
            history.append(decision)
            prior_score = score

            if decision.accepted:
                return AgenticRunResult(
                    history=history,
                    best_decision=decision,
                    converged=True,
                    stopped_reason=f"accepted_at_iter_{iteration}",
                )

        if not history:
            return AgenticRunResult(
                history=[],
                best_decision=None,
                converged=False,
                stopped_reason="no_iterations",
            )
        best = max(history, key=lambda d: d.score.score)
        return AgenticRunResult(
            history=history,
            best_decision=best,
            converged=False,
            stopped_reason=f"max_iterations_{self.max_iterations}",
        )


__all__ = ["AgenticSimOrchestrator"]
