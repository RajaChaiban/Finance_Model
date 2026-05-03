"""FRED → MarketIntelligence ingester.

Fetches a curated set of macro series from the St. Louis Fed FRED API and
formats each observation as a ``Document`` dict that can be passed straight
into ``MarketIntelligence.seed_from_dicts()``.

Why this exists
---------------
``src/agents/market_intelligence.py`` is a pure RAG layer (vector store +
LLM). Without something feeding it, the structuring agents have no macro
grounding — they only see whatever was manually seeded. This module is the
bridge: read FRED today's prints, produce one document per series, hand the
list to MI's seeder. Re-seeding is idempotent because each doc id includes
the observation date.

Curated series (default)
------------------------
The default list is small on purpose — these are the macro signals a
derivatives desk actually looks at every morning, not a kitchen sink:

- ``DFF``      Federal Funds Effective Rate (overnight benchmark)
- ``SOFR``     Secured Overnight Financing Rate (collateralised overnight)
- ``DGS3MO``   3-Month Treasury Constant Maturity (short-end of the curve)
- ``DGS10``    10-Year Treasury Constant Maturity (long-end)
- ``T10Y2Y``   10-Year minus 2-Year spread (yield-curve slope / recession proxy)
- ``VIXCLS``   CBOE VIX index close (equity-vol regime)

Override via ``DEFAULT_FRED_SERIES`` or pass ``series_ids`` to the fetcher.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

import httpx

logger = logging.getLogger(__name__)


FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"


@dataclass(frozen=True)
class FredSeriesSpec:
    """One row in the curated FRED series table.

    Attributes:
        series_id: FRED series identifier (e.g. ``"SOFR"``).
        label: Human-friendly label that goes into the doc content.
        unit: Display unit ("%", "index", "ratio") — appended to the value.
        is_percent: True when FRED returns the value already in percent
            (e.g. SOFR=5.34 means 5.34%). Used only for the human-readable
            text — we don't rescale the underlying number.
    """

    series_id: str
    label: str
    unit: str = "%"
    is_percent: bool = True


DEFAULT_FRED_SERIES: List[FredSeriesSpec] = [
    FredSeriesSpec("DFF", "Federal Funds Effective Rate", "%", True),
    FredSeriesSpec("SOFR", "Secured Overnight Financing Rate", "%", True),
    FredSeriesSpec("DGS3MO", "3-Month Treasury Constant Maturity", "%", True),
    FredSeriesSpec("DGS10", "10-Year Treasury Constant Maturity", "%", True),
    FredSeriesSpec("T10Y2Y", "10Y minus 2Y Treasury Spread", "%", True),
    FredSeriesSpec("VIXCLS", "CBOE VIX Index (Close)", "index", False),
]


def _fetch_latest_observation(
    series_id: str,
    api_key: str,
    timeout_s: float = 5.0,
    client: Optional[httpx.Client] = None,
) -> Optional[Dict[str, str]]:
    """Pull the most recent non-empty observation for ``series_id``.

    Returns a dict ``{"date": "YYYY-MM-DD", "value": "5.34"}`` on success,
    ``None`` on any failure (network, auth, malformed response, dot-value).
    """
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 5,  # FRED uses '.' for missing prints; pull a few and pick the freshest non-empty.
    }
    try:
        if client is not None:
            r = client.get(FRED_BASE_URL, params=params, timeout=timeout_s)
        else:
            r = httpx.get(FRED_BASE_URL, params=params, timeout=timeout_s)
        r.raise_for_status()
        payload = r.json()
    except httpx.HTTPStatusError as exc:
        # Distinguish so the desk can react to auth/rate-limit (4xx) vs.
        # server outage (5xx). Previously a single catch-all logged the same
        # line for "API key revoked" and "FRED is down for maintenance".
        status = exc.response.status_code
        if status == 401:
            logger.error("FRED auth failed for %s (HTTP 401) — check FRED_API_KEY", series_id)
        elif status == 429:
            logger.warning("FRED rate-limited for %s (HTTP 429); skipping", series_id)
        elif 500 <= status < 600:
            logger.warning("FRED server error for %s (HTTP %d); skipping", series_id, status)
        else:
            logger.warning("FRED HTTP %d for %s: %s", status, series_id, exc)
        return None
    except httpx.RequestError as exc:
        # Network-level failure (timeout, DNS, connection refused).
        logger.warning("FRED network error for %s: %s", series_id, exc)
        return None
    except (KeyError, ValueError) as exc:
        # Schema drift (FRED changed field names) or malformed JSON.
        logger.error("FRED schema/parse error for %s: %s — investigate API change", series_id, exc)
        return None
    except Exception as exc:  # noqa: BLE001 - intentional residual catch
        # Anything else we hadn't anticipated — log loudly so the next
        # operator can either narrow this to the right httpx/json error type
        # or fix the underlying cause. Returning None preserves the function's
        # documented "never raise" contract that callers (and tests) rely on.
        logger.warning("FRED unexpected error for %s: %s (%s)",
                       series_id, exc, type(exc).__name__)
        return None

    obs = payload.get("observations") or []
    for ob in obs:
        val = (ob.get("value") or "").strip()
        if val and val != ".":
            return {"date": ob.get("date", ""), "value": val}
    logger.warning("FRED returned no usable observation for %s", series_id)
    return None


def _format_doc(spec: FredSeriesSpec, observation: Dict[str, str]) -> Dict[str, Any]:
    """Turn one FRED observation into a seed dict for MI."""
    date_str = observation.get("date", "unknown")
    raw = observation.get("value", "")
    try:
        num = float(raw)
        pretty = f"{num:.4f}".rstrip("0").rstrip(".")
    except ValueError:
        pretty = raw
        num = None

    if spec.is_percent and num is not None:
        # FRED gives whole-percent (e.g. 5.34 → 5.34%). Surface decimal too so
        # the LLM/agents don't have to re-scale.
        decimal_str = f" (decimal {num / 100.0:.4f})"
    else:
        decimal_str = ""

    content = (
        f"FRED:{spec.series_id} — {spec.label} on {date_str}: "
        f"{pretty}{spec.unit}{decimal_str}."
    )

    return {
        "id": f"fred-{spec.series_id.lower()}-{date_str}",
        "content": content,
        "doc_type": "macro",
        "asset_class": "MACRO",
        "series_id": spec.series_id,
        "observation_date": date_str,
        "value_raw": raw,
        "source": "FRED",
    }


def fetch_fred_documents(
    api_key: Optional[str] = None,
    series: Sequence[FredSeriesSpec] = DEFAULT_FRED_SERIES,
    timeout_s: float = 5.0,
    client: Optional[httpx.Client] = None,
) -> List[Dict[str, Any]]:
    """Fetch latest observations for ``series`` and format them for MI.

    Args:
        api_key: FRED API key. If ``None``, reads ``FRED_API_KEY`` from env.
            Returns ``[]`` (with a warning) when no key is available.
        series: Curated list of series to fetch. Defaults to
            :data:`DEFAULT_FRED_SERIES`.
        timeout_s: Per-request timeout passed to httpx.
        client: Optional pre-built ``httpx.Client`` (used by tests / pooling).

    Returns:
        List of dicts ready for ``MarketIntelligence.seed_from_dicts()``.
        Series whose fetch fails are skipped — the function never raises.
    """
    key = (api_key or os.getenv("FRED_API_KEY", "")).strip()
    if not key:
        logger.info("FRED_API_KEY not set; skipping macro ingestion.")
        return []

    docs: List[Dict[str, Any]] = []
    for spec in series:
        obs = _fetch_latest_observation(spec.series_id, key, timeout_s=timeout_s, client=client)
        if obs is None:
            continue
        docs.append(_format_doc(spec, obs))

    logger.info(
        "FRED ingestion: %d/%d series fetched at %s",
        len(docs),
        len(series),
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    return docs
