"""Agentic eSMM orchestrator.

Runs the observe → propose → backtest → score loop until either:
  - the score crosses an acceptance threshold (converged)
  - max_iterations hit (give up, return best-so-far)

A single iteration:

    observation = RegimeObserver.observe(snapshots)
    proposal    = ConfigStrategist.propose(observation, prior_score=last_score)
    result      = run_backtest(snapshots, proposal.config)
    score       = TCACritic.score(result.tca)

The orchestrator records every iteration as an AgenticDecision and
returns the full history + the best-scoring decision. A future HITL
gate would block before applying `best_decision.proposal.config` to
a live book.
"""

from __future__ import annotations

from typing import Optional

from src.agents.esmm.config_strategist import ConfigStrategist
from src.agents.esmm.regime_observer import RegimeObserver
from src.agents.esmm.schemas import (
    AgenticDecision,
    AgenticRunResult,
    ConfigProposal,
    TCAScore,
)
from src.agents.esmm.tca_critic import TCACritic
from src.esmm.backtest import run_backtest
from src.esmm.schemas import MarketMakingConfig, OrderBookSnapshot, TCABreakdown


class AgenticESMMOrchestrator:
    """Stateful for the duration of one `run()` call. Inject components for tests."""

    def __init__(
        self,
        baseline: MarketMakingConfig,
        observer: Optional[RegimeObserver] = None,
        strategist: Optional[ConfigStrategist] = None,
        critic: Optional[TCACritic] = None,
        acceptance_score: float = 70.0,
        max_iterations: int = 5,
    ):
        self.baseline = baseline
        self.observer = observer or RegimeObserver()
        self.strategist = strategist or ConfigStrategist(baseline=baseline)
        self.critic = critic or TCACritic()
        self.acceptance_score = acceptance_score
        self.max_iterations = max_iterations

    def run(self, snapshots: list[OrderBookSnapshot]) -> AgenticRunResult:
        if not snapshots:
            return AgenticRunResult(
                history=[],
                best_decision=None,
                converged=False,
                stopped_reason="no_snapshots",
            )

        # Observation is computed once on the historical path; the strategist
        # iterates within that regime context. (A real-time variant would
        # re-observe each iteration — left for v2.)
        observation = self.observer.observe(snapshots)

        history: list[AgenticDecision] = []
        prior_score: Optional[TCAScore] = None

        for iteration in range(self.max_iterations):
            proposal = self.strategist.propose(
                observation, prior_score=prior_score, iteration=iteration
            )
            result = run_backtest(snapshots, proposal.config)
            tca_dict = result.tca or {}
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

        # Fell through: max iterations hit. Pick the best-scoring decision.
        best = max(history, key=lambda d: d.score.score)
        return AgenticRunResult(
            history=history,
            best_decision=best,
            converged=False,
            stopped_reason=f"max_iterations_{self.max_iterations}",
        )
