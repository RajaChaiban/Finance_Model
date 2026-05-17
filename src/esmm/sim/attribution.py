"""P&L attribution and counterfactual analysis.

Extends :mod:`src.esmm.tca` with cuts that only make sense in a sim:

  * **By regime** — P&L bucketed by which regime label was active at
    each fill timestamp (CALM/TRENDING/VOLATILE/STRESS).
  * **By participant** — which counterparty we filled against, so we
    can see how much of our edge comes from informed vs. noise flow.
  * **Counterfactual passive** — what P&L would have been had we *not*
    quoted (held our prior inventory mark-to-market) over the same
    window. Edge over passive is the honest measure of strategy value.

Phase-3 implementation. Stub today.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AttributionReport:
    by_regime: list[dict[str, Any]]
    by_participant: list[dict[str, Any]]
    counterfactual_passive_pnl: float
    edge_over_passive: float


def attribute(*args, **kwargs) -> AttributionReport:
    """Phase 3."""
    raise NotImplementedError("Phase 3")


__all__ = ["AttributionReport", "attribute"]
