"""CBOE → MarketIntelligence ingester.

Fetches CBOE public volatility data (VIX historical close, optional VIX
futures term structure) and formats the result as a list of ``Document``-
shaped dicts that drop straight into ``MarketIntelligence.seed_from_dicts()``.

Why this exists
---------------
``fred_ingester`` already grabs VIXCLS via FRED, but FRED:

* lags by a day or two,
* gives you a single number with no regime context, and
* is gated by an API key.

CBOE's CSV (no key, refreshed intraday) is the canonical equity-vol regime
input every structurer reads first thing. We emit:

* ONE summary "regime" doc (latest close + 5-day / 30-day stats + qualitative
  classification),
* up to N daily-close docs (default 5 most-recent rows),
* and OPTIONALLY one term-structure doc when CBOE's JSON endpoint is reachable.

The summary doc is also re-emitted with ``asset_class="EQUITY"`` so equity
queries surface the regime context without the agent needing to know to ask
for "VIX".
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)


VIX_HISTORY_URL = (
    "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
)
VIX_TERM_STRUCTURE_URL = (
    "https://cdn.cboe.com/api/global/delayed_quotes/term_structure/"
    "cboe_term_structure.json"
)
USER_AGENT = "VolDesk MarketIntel research"


def _classify_regime(avg_30d: float) -> str:
    """Quick qualitative bucket for a 30-day VIX average."""
    if avg_30d < 15:
        return "low"
    if avg_30d < 25:
        return "normal"
    if avg_30d < 35:
        return "elevated"
    return "crisis"


def _parse_vix_csv(text: str) -> List[Dict[str, Any]]:
    """Parse the VIX_History.csv body into a list of typed rows.

    Returns rows sorted oldest → newest. Each row is::

        {"date": date, "open": float, "high": float, "low": float, "close": float}

    Malformed rows are silently skipped (CBOE has historically had a couple of
    blank lines / header-repeats — we don't want one of those to nuke the
    whole ingestion).
    """
    rows: List[Dict[str, Any]] = []
    reader = csv.DictReader(io.StringIO(text))
    for raw in reader:
        try:
            d = datetime.strptime((raw.get("DATE") or "").strip(), "%m/%d/%Y").date()
            rows.append(
                {
                    "date": d,
                    "open": float(raw["OPEN"]),
                    "high": float(raw["HIGH"]),
                    "low": float(raw["LOW"]),
                    "close": float(raw["CLOSE"]),
                }
            )
        except (KeyError, ValueError, TypeError):
            continue
    rows.sort(key=lambda r: r["date"])
    return rows


def _fetch_text(
    url: str, client: Optional[httpx.Client], timeout_s: float = 10.0
) -> Optional[str]:
    """GET ``url`` and return the response body as text, or None on any failure."""
    headers = {"User-Agent": USER_AGENT}
    try:
        if client is not None:
            r = client.get(url, headers=headers, timeout=timeout_s)
        else:
            r = httpx.get(url, headers=headers, timeout=timeout_s)
        r.raise_for_status()
        return r.text
    except httpx.HTTPStatusError as exc:
        logger.warning("CBOE HTTP %d for %s", exc.response.status_code, url)
        return None
    except httpx.RequestError as exc:
        logger.warning("CBOE network error for %s: %s", url, exc)
        return None
    except Exception as exc:  # noqa: BLE001 - keep "never raise" contract
        logger.warning("CBOE unexpected error for %s: %s (%s)", url, exc, type(exc).__name__)
        return None


def _fetch_json(
    url: str, client: Optional[httpx.Client], timeout_s: float = 10.0
) -> Optional[Dict[str, Any]]:
    """GET ``url`` as JSON. Returns None on any failure (404, non-JSON, etc.)."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        if client is not None:
            r = client.get(url, headers=headers, timeout=timeout_s)
        else:
            r = httpx.get(url, headers=headers, timeout=timeout_s)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as exc:
        logger.info(
            "CBOE term-structure HTTP %d (skipping)", exc.response.status_code
        )
        return None
    except httpx.RequestError as exc:
        logger.info("CBOE term-structure network error: %s (skipping)", exc)
        return None
    except (ValueError, TypeError) as exc:
        logger.info("CBOE term-structure non-JSON body: %s (skipping)", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "CBOE term-structure unexpected error: %s (%s) (skipping)",
            exc,
            type(exc).__name__,
        )
        return None


def _summary_stats(rows: List[Dict[str, Any]]) -> Tuple[float, float, float, float, float, date]:
    """Compute (latest_close, avg_5d, avg_30d, high_30d, low_30d, latest_date)."""
    latest = rows[-1]
    last_5 = rows[-5:] if len(rows) >= 5 else rows
    last_30 = rows[-30:] if len(rows) >= 30 else rows
    avg_5d = sum(r["close"] for r in last_5) / len(last_5)
    avg_30d = sum(r["close"] for r in last_30) / len(last_30)
    high_30d = max(r["high"] for r in last_30)
    low_30d = min(r["low"] for r in last_30)
    return latest["close"], avg_5d, avg_30d, high_30d, low_30d, latest["date"]


def _build_summary_content(
    today: date,
    latest_close: float,
    avg_5d: float,
    avg_30d: float,
    high_30d: float,
    low_30d: float,
) -> str:
    return (
        f"VIX volatility regime as of {today.isoformat()}: "
        f"latest close {latest_close:.2f}, "
        f"5-day average {avg_5d:.2f}, "
        f"30-day average {avg_30d:.2f}, "
        f"30-day high {high_30d:.2f}, "
        f"30-day low {low_30d:.2f}. "
        f"Compare to long-run mean ~19.5 and 2008-crisis peak 80. "
        f"Regime classification: {_classify_regime(avg_30d)}."
    )


def _build_term_structure_doc(
    payload: Dict[str, Any], today: date
) -> Optional[Dict[str, Any]]:
    """Best-effort parse of CBOE's term-structure JSON.

    The exact schema isn't load-bearing for the prototype; we accept either a
    list-of-dicts or a dict with a ``"data"`` / ``"contracts"`` key. If we
    can't find anything that smells like (tenor, value), skip.
    """
    candidates: List[Tuple[str, float]] = []

    def _try_consume(obj: Any) -> None:
        if not isinstance(obj, list):
            return
        for item in obj:
            if not isinstance(item, dict):
                continue
            tenor = (
                item.get("expiration")
                or item.get("expiry")
                or item.get("tenor")
                or item.get("contract")
                or item.get("symbol")
            )
            value = (
                item.get("price")
                or item.get("last")
                or item.get("value")
                or item.get("settle")
            )
            if tenor is None or value is None:
                continue
            try:
                candidates.append((str(tenor), float(value)))
            except (TypeError, ValueError):
                continue

    if isinstance(payload, list):
        _try_consume(payload)
    elif isinstance(payload, dict):
        for key in ("data", "contracts", "term_structure", "results"):
            if key in payload:
                _try_consume(payload[key])
                if candidates:
                    break
        if not candidates:
            # Sometimes the dict IS the row — flatten its values if they look like rows.
            for v in payload.values():
                _try_consume(v)
                if candidates:
                    break

    if not candidates:
        return None

    per_tenor = ", ".join(f"{t}={v:.2f}" for t, v in candidates[:12])
    return {
        "id": f"cboe-vix-termstructure-{today.isoformat()}",
        "doc_type": "market_window",
        "asset_class": "VIX",
        "as_of": today.isoformat(),
        "source": "CBOE",
        "content": f"VIX futures term structure on {today.isoformat()}: {per_tenor}.",
    }


def fetch_cboe_documents(
    http_client: Optional[httpx.Client] = None,
    max_history_days: int = 5,
) -> List[Dict[str, Any]]:
    """Fetch CBOE VIX docs ready for ``MarketIntelligence.seed_from_dicts()``.

    Args:
        http_client: Optional pre-built ``httpx.Client`` (used by tests).
        max_history_days: How many recent daily-close docs to emit (in
            addition to the regime summary). Default 5; 0 suppresses dailies.

    Returns:
        List of seed dicts. Empty list on total failure (never raises).
    """
    csv_text = _fetch_text(VIX_HISTORY_URL, http_client)
    if not csv_text:
        logger.warning("CBOE VIX CSV fetch failed; emitting no documents")
        return []

    rows = _parse_vix_csv(csv_text)
    if not rows:
        logger.warning("CBOE VIX CSV parsed to zero usable rows")
        return []

    latest_close, avg_5d, avg_30d, high_30d, low_30d, latest_date = _summary_stats(rows)
    today = latest_date  # Use the latest *observation* date, not wall-clock.

    summary_content = _build_summary_content(
        today, latest_close, avg_5d, avg_30d, high_30d, low_30d
    )

    docs: List[Dict[str, Any]] = []

    # 1) Regime summary tagged VIX.
    summary_id = f"cboe-vix-summary-{today.isoformat()}"
    docs.append(
        {
            "id": summary_id,
            "doc_type": "market_window",
            "asset_class": "VIX",
            "as_of": today.isoformat(),
            "source": "CBOE",
            "content": summary_content,
        }
    )

    # 2) Equity-tagged alias of the same summary so equity queries pick it up.
    docs.append(
        {
            "id": f"{summary_id}-equity",
            "doc_type": "market_window",
            "asset_class": "EQUITY",
            "as_of": today.isoformat(),
            "source": "CBOE",
            "content": summary_content,
        }
    )

    # 3) Up to N most-recent daily closes.
    if max_history_days > 0:
        for r in rows[-max_history_days:]:
            docs.append(
                {
                    "id": f"cboe-vix-close-{r['date'].isoformat()}",
                    "doc_type": "market_window",
                    "asset_class": "VIX",
                    "as_of": r["date"].isoformat(),
                    "source": "CBOE",
                    "content": (
                        f"VIX closed at {r['close']:.2f} on {r['date'].isoformat()} "
                        f"(open {r['open']:.2f}, high {r['high']:.2f}, low {r['low']:.2f})."
                    ),
                }
            )

    # 4) Optional term-structure doc (graceful skip).
    ts_payload = _fetch_json(VIX_TERM_STRUCTURE_URL, http_client)
    if ts_payload is not None:
        ts_doc = _build_term_structure_doc(ts_payload, today)
        if ts_doc is not None:
            docs.append(ts_doc)

    logger.info("CBOE ingestion: emitted %d documents (latest %s)", len(docs), today)
    return docs
