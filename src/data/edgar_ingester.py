"""SEC EDGAR -> MarketIntelligence ingester.

Fetches recent structured-note filings from SEC EDGAR's full-text search
API and formats each hit as a ``Document`` dict that can be passed to
``MarketIntelligence.seed_from_dicts()``.

Why this exists
---------------
Structured-note pricing supplements (424B2) and free-writing prospectuses
(FWP) are the canonical "comparable deal" universe for autocallables,
buffered notes, and barrier notes. Pulling a rolling 90-day window gives
the structuring agents a real precedent set without anyone manually
curating one.

EDGAR API specifics
-------------------
- Endpoint: ``https://efts.sec.gov/LATEST/search-index``
- SEC requires a ``User-Agent`` like ``"<name> <email>"`` and rate-limits
  to 10 req/sec. We sleep 0.15s between calls and never raise on a
  per-query failure (mirrors :mod:`fred_ingester` "best-effort" contract).
- Response shape: ``{"hits": {"total": {...}, "hits": [{"_source": {...}}]}}``

The default queries below are the ones a structured-note desk actually
cares about; override via ``queries=`` on the fetcher.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import httpx

logger = logging.getLogger(__name__)


EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
EDGAR_DEFAULT_FORMS = "424B2,FWP"
EDGAR_RATE_LIMIT_SLEEP_S = 0.15  # SEC: 10 req/sec/IP; 0.15s gives margin.

DEFAULT_QUERIES: List[str] = [
    "autocallable",
    "buffered note",
    "barrier note",
    "structured note",
]


@dataclass(frozen=True)
class EdgarHit:
    """One row out of EDGAR's full-text search response.

    Mirrors the subset of ``_source`` we care about so downstream code
    isn't coupled to EDGAR's raw schema.
    """

    accession: str
    form: str
    file_date: str
    issuer: str
    tickers: List[str]
    cik: Optional[str]
    matched_query: str


def _build_filing_url(cik: Optional[str], form: str) -> str:
    """Construct a browse-edgar URL the agent / human can click through."""
    cik_part = cik or ""
    return (
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK="
        f"{cik_part}&type={form}&dateb=&owner=include&count=40&search_text="
    )


def _parse_hit(raw: Dict[str, Any], matched_query: str) -> Optional[EdgarHit]:
    """Coerce one ``_source`` dict into an ``EdgarHit``. Returns ``None``
    if the hit is missing fields we can't synthesise (accession, form)."""
    src = raw.get("_source") or raw  # tolerate either wrapper or flat
    adsh = (src.get("adsh") or "").strip()
    form = (src.get("form") or "").strip()
    if not adsh or not form:
        return None
    file_date = (src.get("file_date") or "").strip()
    display_names = src.get("display_names") or []
    issuer = display_names[0] if display_names else "Unknown Issuer"
    tickers = src.get("tickers") or []
    if isinstance(tickers, str):
        tickers = [t.strip() for t in tickers.split(",") if t.strip()]
    # CIK can sit on the hit envelope or inside _source.ciks.
    ciks = src.get("ciks") or raw.get("ciks") or []
    cik = ciks[0] if ciks else None
    return EdgarHit(
        accession=adsh,
        form=form,
        file_date=file_date,
        issuer=issuer,
        tickers=list(tickers),
        cik=cik,
        matched_query=matched_query,
    )


def _format_doc(hit: EdgarHit) -> Dict[str, Any]:
    """Render one ``EdgarHit`` as a seed dict for MarketIntelligence."""
    tickers_str = ", ".join(hit.tickers) if hit.tickers else "n/a"
    content = (
        f"SEC EDGAR {hit.form} filing - {hit.issuer} on {hit.file_date}. "
        f"Matched on query: '{hit.matched_query}'. "
        f"Accession: {hit.accession}. "
        f"Structured-note precedent (form {hit.form} = pricing supplement / "
        f"preliminary term sheet). "
        f"Tickers: {tickers_str}."
    )
    return {
        "id": f"edgar-{hit.accession}",
        "content": content,
        "doc_type": "deal",
        "asset_class": "EQUITY",
        "as_of": hit.file_date,
        "issuer": hit.issuer,
        "form_type": hit.form,
        "matched_query": hit.matched_query,
        "accession": hit.accession,
        "filing_url": _build_filing_url(hit.cik, hit.form),
        "source": "EDGAR",
    }


def _run_query(
    query: str,
    user_agent: str,
    days_back: int,
    max_per_query: int,
    client: httpx.Client,
    timeout_s: float,
) -> List[EdgarHit]:
    """Issue one EDGAR full-text search and return parsed hits.

    Per-query failures are logged as warnings and yield ``[]``; the
    fetcher's contract is "never raise on a single bad query".
    """
    # EDGAR uses dateRange=custom + startdt/enddt (YYYY-MM-DD); compute on the fly.
    from datetime import date, timedelta

    today = date.today()
    start = today - timedelta(days=max(days_back, 1))
    params = {
        "q": query,
        "dateRange": "custom",
        "startdt": start.isoformat(),
        "enddt": today.isoformat(),
        "forms": EDGAR_DEFAULT_FORMS,
    }
    headers = {"User-Agent": user_agent, "Accept": "application/json"}

    try:
        resp = client.get(
            EDGAR_SEARCH_URL,
            params=params,
            headers=headers,
            timeout=timeout_s,
        )
        resp.raise_for_status()
        payload = resp.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        logger.warning("EDGAR HTTP %s for query=%r: %s", status, query, exc)
        return []
    except httpx.HTTPError as exc:
        logger.warning("EDGAR network error for query=%r: %s", query, exc)
        return []
    except (KeyError, ValueError) as exc:
        logger.error("EDGAR schema/parse error for query=%r: %s", query, exc)
        return []
    except Exception as exc:  # noqa: BLE001 - intentional residual catch
        logger.warning(
            "EDGAR unexpected error for query=%r: %s (%s)",
            query, exc, type(exc).__name__,
        )
        return []

    raw_hits = ((payload.get("hits") or {}).get("hits")) or []
    out: List[EdgarHit] = []
    for raw in raw_hits[:max_per_query]:
        parsed = _parse_hit(raw, matched_query=query)
        if parsed is not None:
            out.append(parsed)
    logger.info("EDGAR query=%r returned %d usable hits", query, len(out))
    return out


def fetch_edgar_filings(
    user_agent: str,
    days_back: int = 90,
    queries: Optional[Sequence[str]] = None,
    max_per_query: int = 25,
    http_client: Optional[httpx.Client] = None,
    timeout_s: float = 10.0,
) -> List[Dict[str, Any]]:
    """Fetch recent structured-note filings from SEC EDGAR.

    Args:
        user_agent: Required. SEC blocks requests without a contact-style
            ``"<name> <email>"`` UA. Empty / whitespace strings raise
            ``ValueError`` so we never silently send anonymous traffic.
        days_back: Trailing window in days (default 90).
        queries: Override the default search terms. ``None`` uses
            :data:`DEFAULT_QUERIES`.
        max_per_query: Cap hits per query before dedup (default 25).
        http_client: Optional pre-built ``httpx.Client`` (tests / pooling).
        timeout_s: Per-request timeout passed to httpx.

    Returns:
        Deduped list of doc dicts (first-seen accession wins) ready for
        ``MarketIntelligence.seed_from_dicts()``. Empty list is valid.
    """
    if not user_agent or not user_agent.strip():
        raise ValueError(
            "EDGAR requires a User-Agent like '<name> <email>'. "
            "Set EDGAR_USER_AGENT or pass user_agent= explicitly."
        )

    qs = list(queries) if queries is not None else list(DEFAULT_QUERIES)
    if not qs:
        return []

    owns_client = http_client is None
    client = http_client or httpx.Client()
    seen: Dict[str, Dict[str, Any]] = {}
    try:
        for i, q in enumerate(qs):
            if i > 0:
                # SEC fair-use rate limit; cheap insurance.
                time.sleep(EDGAR_RATE_LIMIT_SLEEP_S)
            hits = _run_query(
                query=q,
                user_agent=user_agent,
                days_back=days_back,
                max_per_query=max_per_query,
                client=client,
                timeout_s=timeout_s,
            )
            for h in hits:
                if h.accession in seen:
                    continue  # first-seen wins; keeps matched_query stable.
                seen[h.accession] = _format_doc(h)
    finally:
        if owns_client:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

    docs = list(seen.values())
    logger.info(
        "EDGAR ingestion: %d unique filings across %d queries (window=%dd)",
        len(docs), len(qs), days_back,
    )
    return docs


def default_user_agent() -> str:
    """Build the SEC-required UA string from env (with a safe default)."""
    email = os.environ.get("EDGAR_USER_AGENT_EMAIL", "admin@example.com")
    return f"VolDesk MarketIntel {email}"
