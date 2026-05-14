"""Tests for the TCA critic."""

from __future__ import annotations

import pytest

from src.agents.esmm.tca_critic import CriticThresholds, TCACritic
from src.esmm.schemas import TCABreakdown


def _tca(
    spread: float = 10.0,
    inventory: float = 0.0,
    hedge: float = 0.0,
    adverse: float = 0.0,
    fees: float = 0.0,
    n_fills: int = 5,
) -> TCABreakdown:
    return TCABreakdown(
        spread_capture_pnl=spread,
        inventory_pnl=inventory,
        hedge_pnl=hedge,
        adverse_selection_pnl=adverse,
        fees_pnl=fees,
        total_pnl=spread + inventory + hedge + adverse + fees,
        n_fills=n_fills,
        avg_fill_size=20.0,
    )


def test_perfectly_clean_book_scores_high():
    """All P&L from spread, no costs → near-max score."""
    score = TCACritic().score(_tca(spread=100, adverse=0, hedge=0, inventory=0))
    assert score.score >= 75.0
    assert any("healthy" in r.lower() for r in score.recommendations)


def test_book_dominated_by_adverse_selection_scores_low():
    score = TCACritic().score(_tca(spread=10, adverse=-12))
    # adverse_selection_ratio ≥ 1.0 → triggers a heavy penalty + critical rec.
    assert score.score < 50.0
    assert any("adverse selection" in r.lower() for r in score.recommendations)


def test_book_dominated_by_hedge_drag_warns():
    score = TCACritic().score(_tca(spread=10, hedge=-8))
    assert any("hedge" in r.lower() for r in score.recommendations)


def test_book_with_volatile_inventory_warns():
    score = TCACritic().score(_tca(spread=10, inventory=-15))
    assert any("inventory" in r.lower() for r in score.recommendations)


def test_score_clamped_to_0_100():
    # Extreme adverse + hedge + inventory hit → would push below 0 mathematically.
    score = TCACritic().score(_tca(spread=1, adverse=-100, hedge=-50, inventory=-50))
    assert 0.0 <= score.score <= 100.0


def test_zero_total_pnl_does_not_crash():
    """Edge: total_pnl == 0 → spread_capture_ratio uses denom guard."""
    score = TCACritic().score(_tca(spread=0, adverse=0, hedge=0, inventory=0, fees=0))
    assert 0.0 <= score.score <= 100.0


def test_thresholds_are_overridable():
    permissive = CriticThresholds(adverse_selection_warn=999, adverse_selection_critical=999)
    strict = CriticThresholds(adverse_selection_warn=0.05, adverse_selection_critical=0.1)
    tca = _tca(spread=10, adverse=-2)  # adv ratio = 0.2

    perm_score = TCACritic(thresholds=permissive).score(tca)
    strict_score = TCACritic(thresholds=strict).score(tca)
    # Permissive: no adv-sel warning. Strict: a "critical" rec.
    assert not any("adverse selection" in r.lower() for r in perm_score.recommendations)
    assert any("critical adverse selection" in r.lower() for r in strict_score.recommendations)


def test_at_least_one_recommendation_always_returned():
    # Even a perfect book gets the "healthy — no adjustment" rec.
    score = TCACritic().score(_tca(spread=100))
    assert len(score.recommendations) >= 1


def test_score_monotonic_in_spread_capture():
    """Doubling spread capture (with everything else flat) should not lower the score."""
    low = TCACritic().score(_tca(spread=10))
    high = TCACritic().score(_tca(spread=100))
    assert high.score >= low.score
