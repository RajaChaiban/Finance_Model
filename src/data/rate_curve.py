"""Risk-free rate curve abstraction.

Phase 2: FlatRateCurve (single rate) + FRED-backed flat (today's SOFR).
Phase 3 future: OIS bootstrap with term structure.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_RATE = 0.045


@dataclass
class FlatRateCurve:
    rate: float

    def spot_rate(self, maturity_years: float) -> float:  # noqa: ARG002
        return self.rate

    @property
    def kind(self) -> str:
        return "flat"


def _fetch_sofr_overnight(api_key: str) -> Optional[float]:
    """Fetch today's overnight SOFR from FRED. Returns decimal (e.g. 0.0532)."""
    url = (
        "https://api.stlouisfed.org/fred/series/observations"
        f"?series_id=SOFR&api_key={api_key}&file_type=json"
        "&sort_order=desc&limit=1"
    )
    try:
        r = httpx.get(url, timeout=5.0)
        r.raise_for_status()
        obs = r.json()["observations"][0]["value"]
        if obs == "." or obs == "":
            return None
        return float(obs) / 100.0
    except Exception as exc:  # noqa: BLE001
        logger.warning("FRED SOFR fetch failed: %s", exc)
        return None


class RateCurve:
    """Factory entry-point for whatever curve env+config say to build."""

    @staticmethod
    def from_env() -> FlatRateCurve:
        api_key = os.getenv("FRED_API_KEY")
        if api_key:
            sofr = _fetch_sofr_overnight(api_key)
            if sofr is not None:
                logger.info("Using SOFR=%.4f from FRED", sofr)
                return FlatRateCurve(rate=sofr)
        return FlatRateCurve(rate=DEFAULT_RATE)
