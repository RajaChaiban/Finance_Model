"""P&L attribution and counterfactual analysis.

This module is the *strategic* sibling of :mod:`src.esmm.tca`. TCA
splits P&L by economic source (spread capture / inventory / hedge /
adverse-selection / fees). Attribution splits the same P&L by
*context*:

* **by_regime** — under which market regime did we earn (or lose) the P&L?
* **by_participant** — which counterparty type did we trade against?
* **counterfactual_passive** — what P&L would we have made by *not*
  quoting (just holding our initial inventory mark-to-market)?
* **edge_over_passive** — actual P&L minus the counterfactual. This is
  the honest measure of whether the strategy added value: a passive
  hold can be cheap; a quoting strategy needs to beat it.

The module is intentionally light on dependencies. It consumes
:class:`src.esmm.schemas.Fill` plus a parallel list of
:class:`FillContext` (regime + participant_kind annotations). The
caller (kernel / arena) is responsible for tagging fills as they happen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.esmm.schemas import Fill, Side


@dataclass(frozen=True)
class FillContext:
    """Auxiliary tags attached to a Fill at the moment it occurred.

    The kernel populates these from the regime observer and the
    counterparty's participant id. The attribution module never needs
    to know how they were derived.
    """

    regime: str  # e.g. CALM / TRENDING / VOLATILE / STRESS
    participant_kind: str  # e.g. noise / informed / momentum / mean_reverter
    counterparty_id: str = "external"


@dataclass(frozen=True)
class RegimeBucket:
    regime: str
    n_fills: int
    realized_pnl: float
    notional: float


@dataclass(frozen=True)
class ParticipantBucket:
    participant_kind: str
    n_fills: int
    realized_pnl: float
    notional: float


@dataclass(frozen=True)
class AttributionReport:
    by_regime: list[RegimeBucket]
    by_participant: list[ParticipantBucket]
    counterfactual_passive_pnl: float
    actual_realized_pnl: float
    edge_over_passive: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "by_regime": [b.__dict__ for b in self.by_regime],
            "by_participant": [b.__dict__ for b in self.by_participant],
            "counterfactual_passive_pnl": self.counterfactual_passive_pnl,
            "actual_realized_pnl": self.actual_realized_pnl,
            "edge_over_passive": self.edge_over_passive,
        }


def _fill_signed_qty(f: Fill) -> float:
    """Signed quantity: BUY = +size, SELL = -size."""
    return f.size if f.side is Side.BUY else -f.size


def _fill_realized_pnl(f: Fill) -> float:
    """Mark-to-fair-value P&L per fill.

    For a maker fill: if we BUY at price P with fair value V, the
    instantaneous P&L is ``(V - P) * size`` — we paid less than fair.
    If we SELL at P with fair value V, P&L is ``(P - V) * size`` — we
    sold above fair. Plus the fee rebate (positive) or cost (negative).
    """
    if f.side is Side.BUY:
        edge = (f.fair_value_at_fill - f.price) * f.size
    else:
        edge = (f.price - f.fair_value_at_fill) * f.size
    fee = -f.fee_bps / 10_000.0 * f.price * f.size  # negative bps = rebate
    return edge + fee


def attribute(
    fills: list[Fill],
    contexts: list[FillContext],
    initial_inventory: float,
    initial_mid: float,
    final_mid: float,
) -> AttributionReport:
    """Produce an :class:`AttributionReport` for a window.

    Args:
        fills: ordered list of fills that occurred in the window
            (typically customer fills only; hedges can be filtered out
            by the caller via ``f.is_hedge``).
        contexts: same length as ``fills``, one ``FillContext`` per
            fill — must be in the same order.
        initial_inventory: signed quantity held at the start of the
            window. Used to compute the passive counterfactual.
        initial_mid: mid-price at the start of the window.
        final_mid: mid-price at the end.

    Returns:
        An :class:`AttributionReport` aggregating P&L by regime and by
        participant type, plus the passive counterfactual and the edge
        over it.

    Raises:
        ValueError: if ``len(fills) != len(contexts)``.
    """
    if len(fills) != len(contexts):
        raise ValueError(
            f"fills and contexts must align; got {len(fills)} vs {len(contexts)}"
        )

    # ------------------------------------------------------------------
    # By regime
    # ------------------------------------------------------------------
    regime_n: dict[str, int] = {}
    regime_pnl: dict[str, float] = {}
    regime_notional: dict[str, float] = {}
    # By participant
    part_n: dict[str, int] = {}
    part_pnl: dict[str, float] = {}
    part_notional: dict[str, float] = {}

    total_realized = 0.0
    for f, ctx in zip(fills, contexts):
        pnl = _fill_realized_pnl(f)
        notional = f.price * f.size
        total_realized += pnl

        regime_n[ctx.regime] = regime_n.get(ctx.regime, 0) + 1
        regime_pnl[ctx.regime] = regime_pnl.get(ctx.regime, 0.0) + pnl
        regime_notional[ctx.regime] = regime_notional.get(ctx.regime, 0.0) + notional

        part_n[ctx.participant_kind] = part_n.get(ctx.participant_kind, 0) + 1
        part_pnl[ctx.participant_kind] = part_pnl.get(ctx.participant_kind, 0.0) + pnl
        part_notional[ctx.participant_kind] = (
            part_notional.get(ctx.participant_kind, 0.0) + notional
        )

    by_regime = [
        RegimeBucket(regime=k, n_fills=regime_n[k], realized_pnl=regime_pnl[k], notional=regime_notional[k])
        for k in sorted(regime_n)
    ]
    by_participant = [
        ParticipantBucket(
            participant_kind=k,
            n_fills=part_n[k],
            realized_pnl=part_pnl[k],
            notional=part_notional[k],
        )
        for k in sorted(part_n)
    ]

    # ------------------------------------------------------------------
    # Counterfactual passive
    # ------------------------------------------------------------------
    # "What if I had held my initial_inventory and made no further
    # trades?" Final mark-to-market: inventory * (final - initial).
    counterfactual = initial_inventory * (final_mid - initial_mid)

    return AttributionReport(
        by_regime=by_regime,
        by_participant=by_participant,
        counterfactual_passive_pnl=counterfactual,
        actual_realized_pnl=total_realized,
        edge_over_passive=total_realized - counterfactual,
    )


__all__ = [
    "AttributionReport",
    "FillContext",
    "ParticipantBucket",
    "RegimeBucket",
    "attribute",
]
