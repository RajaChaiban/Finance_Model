"""Tests for src.data.cboe_ingester and the CBOE → MI seed path.

No real network calls in the default suite: httpx is monkeypatched. Covers:

* CSV happy path — summary stats correct, daily docs emitted.
* CSV 404 — graceful empty list.
* Term-structure 404 — summary + dailies still emit, no TS doc.
* Equity alias — summary doc duplicated under asset_class="EQUITY".
* seed_from_cboe pass-through — calls seed_from_dicts.

A live probe is gated behind RUN_LIVE_CBOE so CI doesn't depend on CBOE.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any, Dict, List, Optional

import httpx
import pytest

from src.data.cboe_ingester import (
    VIX_HISTORY_URL,
    VIX_TERM_STRUCTURE_URL,
    _classify_regime,
    _parse_vix_csv,
    fetch_cboe_documents,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(
        self,
        text: str = "",
        status_code: int = 200,
        json_payload: Optional[Any] = None,
    ):
        self.text = text
        self.status_code = status_code
        self._json_payload = json_payload

    def json(self) -> Any:
        if self._json_payload is None:
            raise ValueError("no json")
        return self._json_payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", "http://x"),
                response=httpx.Response(self.status_code),
            )


def _fake_csv(num_days: int = 30, base_close: float = 16.0) -> str:
    """Build a CSV with ``num_days`` of synthetic rows.

    Row i has close = base_close + i*0.1, dates Jan-2026 forward, so the
    "latest" row is the one with the highest close.
    """
    lines = ["DATE,OPEN,HIGH,LOW,CLOSE"]
    for i in range(num_days):
        d = f"01/{i+1:02d}/2026"
        close = base_close + i * 0.1
        lines.append(f"{d},{close-0.1:.2f},{close+0.5:.2f},{close-0.5:.2f},{close:.2f}")
    return "\n".join(lines) + "\n"


def _make_get(csv_status: int = 200, ts_status: int = 200, ts_payload: Optional[Any] = None,
              csv_text: Optional[str] = None):
    """Build a fake httpx.get(...) that routes by URL."""
    csv_body = csv_text if csv_text is not None else _fake_csv(30)

    def fake_get(url, headers=None, timeout=None, **kwargs):
        if url == VIX_HISTORY_URL:
            if csv_status >= 400:
                return _FakeResponse(text="", status_code=csv_status)
            return _FakeResponse(text=csv_body, status_code=200)
        if url == VIX_TERM_STRUCTURE_URL:
            if ts_status >= 400:
                return _FakeResponse(text="", status_code=ts_status)
            return _FakeResponse(text="", status_code=200, json_payload=ts_payload)
        raise AssertionError(f"unexpected url: {url}")

    return fake_get


# ---------------------------------------------------------------------------
# _classify_regime
# ---------------------------------------------------------------------------


def test_classify_regime_buckets():
    assert _classify_regime(10.0) == "low"
    assert _classify_regime(20.0) == "normal"
    assert _classify_regime(30.0) == "elevated"
    assert _classify_regime(45.0) == "crisis"


# ---------------------------------------------------------------------------
# _parse_vix_csv
# ---------------------------------------------------------------------------


def test_parse_vix_csv_skips_malformed_rows():
    csv_text = (
        "DATE,OPEN,HIGH,LOW,CLOSE\n"
        "01/01/2026,10,11,9,10.5\n"
        "garbage,row,here,oops,bad\n"
        "01/02/2026,11,12,10,11.5\n"
    )
    rows = _parse_vix_csv(csv_text)
    assert len(rows) == 2
    assert rows[0]["date"] == date(2026, 1, 1)
    assert rows[1]["close"] == 11.5


# ---------------------------------------------------------------------------
# fetch_cboe_documents
# ---------------------------------------------------------------------------


def test_fetch_cboe_documents_parses_csv(monkeypatch):
    """Happy path: 30 days of CSV → summary doc with correct stats + 5 dailies."""
    monkeypatch.setattr(httpx, "get", _make_get(ts_status=404))

    docs = fetch_cboe_documents()

    # 1 summary (VIX) + 1 summary (EQUITY) + 5 dailies = 7. No TS doc (404).
    assert len(docs) == 7

    # Find the VIX summary.
    summaries = [d for d in docs if d["id"].startswith("cboe-vix-summary-")
                 and d["asset_class"] == "VIX"]
    assert len(summaries) == 1
    summary = summaries[0]

    # 30 days, base 16.0, +0.1 per day → closes are [16.0, 16.1, …, 18.9].
    # Latest = 18.9, avg_5d = mean of last 5 (18.5..18.9) = 18.7,
    # avg_30d = mean of all 30 = (16.0 + 18.9)/2 = 17.45.
    assert "latest close 18.90" in summary["content"]
    assert "5-day average 18.70" in summary["content"]
    assert "30-day average 17.45" in summary["content"]
    # 30-day high = 18.9 + 0.5 = 19.40, 30-day low = 16.0 - 0.5 = 15.50.
    assert "30-day high 19.40" in summary["content"]
    assert "30-day low 15.50" in summary["content"]
    # Regime: 17.45 < 25 → normal.
    assert "Regime classification: normal" in summary["content"]

    # 5 daily docs, each with content shape "VIX closed at X on YYYY-MM-DD".
    dailies = [d for d in docs if d["id"].startswith("cboe-vix-close-")]
    assert len(dailies) == 5
    for d in dailies:
        assert d["doc_type"] == "market_window"
        assert d["asset_class"] == "VIX"
        assert d["source"] == "CBOE"
        assert "VIX closed at" in d["content"]


def test_fetch_cboe_documents_handles_csv_404(monkeypatch):
    """CSV endpoint 404s → return [] without raising, no TS attempt either."""
    calls: List[str] = []

    def fake_get(url, headers=None, timeout=None, **kwargs):
        calls.append(url)
        if url == VIX_HISTORY_URL:
            return _FakeResponse(text="", status_code=404)
        # If we ever hit TS we want the test to notice — return a deterministic value.
        return _FakeResponse(text="", status_code=200, json_payload=[])

    monkeypatch.setattr(httpx, "get", fake_get)

    docs = fetch_cboe_documents()
    assert docs == []
    # Sanity: we did try the CSV (and only the CSV).
    assert VIX_HISTORY_URL in calls
    assert VIX_TERM_STRUCTURE_URL not in calls


def test_fetch_cboe_documents_handles_term_structure_404(monkeypatch):
    """CSV ok, TS 404 → summary + dailies emit, no TS doc."""
    monkeypatch.setattr(httpx, "get", _make_get(csv_status=200, ts_status=404))

    docs = fetch_cboe_documents()

    ts_docs = [d for d in docs if d["id"].startswith("cboe-vix-termstructure-")]
    assert ts_docs == []

    # Summary still present.
    summaries = [d for d in docs if d["id"].startswith("cboe-vix-summary-")
                 and d["asset_class"] == "VIX"]
    assert len(summaries) == 1


def test_fetch_cboe_documents_emits_equity_alias(monkeypatch):
    """Summary doc must be duplicated under asset_class=EQUITY for equity queries."""
    monkeypatch.setattr(httpx, "get", _make_get(ts_status=404))

    docs = fetch_cboe_documents()

    equity_summaries = [
        d for d in docs
        if d["id"].startswith("cboe-vix-summary-")
        and d["id"].endswith("-equity")
        and d["asset_class"] == "EQUITY"
    ]
    assert len(equity_summaries) == 1

    vix_summaries = [
        d for d in docs
        if d["id"].startswith("cboe-vix-summary-")
        and not d["id"].endswith("-equity")
        and d["asset_class"] == "VIX"
    ]
    assert len(vix_summaries) == 1

    # The content text is identical between VIX and EQUITY aliases.
    assert equity_summaries[0]["content"] == vix_summaries[0]["content"]


def test_fetch_cboe_documents_includes_term_structure_when_reachable(monkeypatch):
    """When TS JSON parses cleanly, emit one TS doc."""
    payload = {
        "data": [
            {"expiration": "2026-05", "price": 17.5},
            {"expiration": "2026-06", "price": 18.0},
            {"expiration": "2026-07", "price": 18.4},
        ]
    }
    monkeypatch.setattr(
        httpx, "get",
        _make_get(csv_status=200, ts_status=200, ts_payload=payload),
    )

    docs = fetch_cboe_documents()

    ts_docs = [d for d in docs if d["id"].startswith("cboe-vix-termstructure-")]
    assert len(ts_docs) == 1
    ts = ts_docs[0]
    assert "2026-05=17.50" in ts["content"]
    assert "2026-06=18.00" in ts["content"]
    assert ts["asset_class"] == "VIX"


# ---------------------------------------------------------------------------
# MarketIntelligence.seed_from_cboe — bridge into the RAG corpus
# ---------------------------------------------------------------------------


def test_seed_from_cboe_calls_seed_from_dicts(monkeypatch):
    """Patch the fetcher and assert MI.seed_from_dicts gets the docs verbatim."""
    from src.agents.market_intelligence import MarketIntelligence, RetrievalEngine
    from tests.test_market_intelligence_integration import FakeVectorStore  # type: ignore

    mi = MarketIntelligence.__new__(MarketIntelligence)
    fake_store = FakeVectorStore()
    mi.vector_store = fake_store
    mi.retrieval = RetrievalEngine(fake_store)
    mi.llm = lambda prompt, system=None: "ok"

    fake_docs: List[Dict[str, Any]] = [
        {
            "id": "cboe-vix-summary-2026-04-29",
            "doc_type": "market_window",
            "asset_class": "VIX",
            "as_of": "2026-04-29",
            "source": "CBOE",
            "content": "VIX volatility regime as of 2026-04-29: ...",
        },
        {
            "id": "cboe-vix-summary-2026-04-29-equity",
            "doc_type": "market_window",
            "asset_class": "EQUITY",
            "as_of": "2026-04-29",
            "source": "CBOE",
            "content": "VIX volatility regime as of 2026-04-29: ...",
        },
    ]

    import src.data.cboe_ingester as ci
    monkeypatch.setattr(ci, "fetch_cboe_documents", lambda: fake_docs)

    n = mi.seed_from_cboe()
    assert n == 2
    assert fake_store.count() == 2
    seeded_ids = {d["id"] for d in fake_store.docs}
    assert "cboe-vix-summary-2026-04-29" in seeded_ids
    assert "cboe-vix-summary-2026-04-29-equity" in seeded_ids


def test_seed_from_cboe_returns_zero_on_empty(monkeypatch):
    from src.agents.market_intelligence import MarketIntelligence, RetrievalEngine
    from tests.test_market_intelligence_integration import FakeVectorStore  # type: ignore

    mi = MarketIntelligence.__new__(MarketIntelligence)
    fake_store = FakeVectorStore()
    mi.vector_store = fake_store
    mi.retrieval = RetrievalEngine(fake_store)
    mi.llm = lambda prompt, system=None: "ok"

    import src.data.cboe_ingester as ci
    monkeypatch.setattr(ci, "fetch_cboe_documents", lambda: [])

    n = mi.seed_from_cboe()
    assert n == 0
    assert fake_store.count() == 0


# ---------------------------------------------------------------------------
# Live probe (opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("RUN_LIVE_CBOE"),
    reason="set RUN_LIVE_CBOE=1 to hit the real CBOE CDN",
)
def test_live_cboe_fetch_smoke():
    docs = fetch_cboe_documents()
    assert len(docs) >= 2  # at minimum the VIX + EQUITY summaries.
    assert any(d["asset_class"] == "VIX" for d in docs)
    assert any(d["asset_class"] == "EQUITY" for d in docs)
