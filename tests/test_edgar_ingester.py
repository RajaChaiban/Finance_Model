"""Tests for src.data.edgar_ingester and the EDGAR -> MI seed path.

No real network calls in the default suite: ``httpx.Client.get`` is
monkeypatched. A single live probe is gated behind ``RUN_LIVE_EDGAR``
so CI / casual runs never hit SEC.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx
import pytest

from src.data.edgar_ingester import (
    DEFAULT_QUERIES,
    fetch_edgar_filings,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: Dict[str, Any], status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            # Mirror httpx surface so the ingester catches the right type.
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", "https://efts.sec.gov/"),
                response=httpx.Response(self.status_code),
            )


class _FakeClient:
    """Minimal stand-in for httpx.Client. Returns canned responses by call
    order, or by query string if a callable router is supplied."""

    def __init__(self, responses):
        # responses: list[_FakeResponse | Exception] OR callable(params)->resp
        self._responses = responses
        self._calls: List[Dict[str, Any]] = []

    @property
    def calls(self) -> List[Dict[str, Any]]:
        return self._calls

    def get(self, url, params=None, headers=None, timeout=None):
        self._calls.append(
            {"url": url, "params": dict(params or {}), "headers": dict(headers or {})}
        )
        if callable(self._responses):
            r = self._responses(params or {})
        else:
            r = self._responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def close(self):
        pass


def _hit(adsh: str, form: str = "424B2", file_date: str = "2026-04-29",
         issuer: str = "Big Bank N.A.", tickers=None, cik: str = "0000019617"):
    return {
        "_source": {
            "adsh": adsh,
            "form": form,
            "file_date": file_date,
            "display_names": [issuer],
            "tickers": tickers or ["SPY"],
            "ciks": [cik],
        }
    }


def _payload(hits: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"hits": {"total": {"value": len(hits)}, "hits": hits}}


# ---------------------------------------------------------------------------
# Required functional tests
# ---------------------------------------------------------------------------


def test_fetch_edgar_filings_parses_response():
    """Happy path: one hit per query, each rendered with the right schema."""
    payload = _payload([_hit("0001193125-26-100001")])
    fake = _FakeClient(lambda params: _FakeResponse(payload))

    docs = fetch_edgar_filings(
        user_agent="VolDesk admin@example.com",
        queries=["autocallable"],
        http_client=fake,
    )

    assert len(docs) == 1
    d = docs[0]
    assert d["id"] == "edgar-0001193125-26-100001"
    assert d["doc_type"] == "deal"
    assert d["asset_class"] == "EQUITY"
    assert d["as_of"] == "2026-04-29"
    assert d["issuer"] == "Big Bank N.A."
    assert d["form_type"] == "424B2"
    assert d["matched_query"] == "autocallable"
    assert d["accession"] == "0001193125-26-100001"
    assert "SEC EDGAR 424B2 filing" in d["content"]
    assert "Big Bank N.A." in d["content"]
    assert "autocallable" in d["content"]
    assert "SPY" in d["content"]
    # User-Agent header was actually sent.
    ua_sent = fake.calls[0]["headers"].get("User-Agent")
    assert ua_sent == "VolDesk admin@example.com"


def test_fetch_edgar_filings_dedups_by_accession():
    """Same accession in two query responses -> one doc, first-seen wins."""
    shared = _hit("0001193125-26-200002")
    other = _hit("0001193125-26-200003")

    def router(params):
        q = params.get("q")
        if q == "autocallable":
            return _FakeResponse(_payload([shared, other]))
        if q == "buffered note":
            return _FakeResponse(_payload([shared]))  # duplicate of "autocallable"
        return _FakeResponse(_payload([]))

    fake = _FakeClient(router)

    docs = fetch_edgar_filings(
        user_agent="VolDesk admin@example.com",
        queries=["autocallable", "buffered note"],
        http_client=fake,
    )
    ids = [d["id"] for d in docs]
    assert sorted(ids) == [
        "edgar-0001193125-26-200002",
        "edgar-0001193125-26-200003",
    ]
    # First-seen "autocallable" wins for the duplicate.
    dup = next(d for d in docs if d["accession"] == "0001193125-26-200002")
    assert dup["matched_query"] == "autocallable"


def test_fetch_edgar_filings_handles_http_error(caplog):
    """Per-query httpx.HTTPError -> warning + empty list. No raise."""
    err = httpx.RequestError("boom", request=httpx.Request("GET", "https://efts.sec.gov/"))
    fake = _FakeClient(lambda params: (_ for _ in ()).throw(err))

    with caplog.at_level("WARNING"):
        docs = fetch_edgar_filings(
            user_agent="VolDesk admin@example.com",
            queries=["autocallable"],
            http_client=fake,
        )

    assert docs == []
    assert any("EDGAR network error" in r.message for r in caplog.records)


def test_fetch_edgar_filings_requires_user_agent():
    """Empty / whitespace UA must raise — never send anonymous traffic."""
    with pytest.raises(ValueError):
        fetch_edgar_filings(user_agent="", queries=["autocallable"])
    with pytest.raises(ValueError):
        fetch_edgar_filings(user_agent="   ", queries=["autocallable"])


def test_fetch_edgar_filings_respects_max_per_query():
    """100 hits in -> max_per_query=25 out (per query, before dedup)."""
    big_hits = [_hit(f"0001193125-26-9{i:05d}") for i in range(100)]
    fake = _FakeClient(lambda params: _FakeResponse(_payload(big_hits)))

    docs = fetch_edgar_filings(
        user_agent="VolDesk admin@example.com",
        queries=["autocallable"],  # one query so dedup doesn't shrink it further
        max_per_query=25,
        http_client=fake,
    )
    assert len(docs) == 25


def test_seed_from_edgar_calls_seed_from_dicts(monkeypatch):
    """End-to-end: seed_from_edgar forwards fetcher output to seed_from_dicts."""
    from src.agents.market_intelligence import (
        MarketIntelligence,
        RetrievalEngine,
    )
    from tests.test_market_intelligence_integration import FakeVectorStore  # type: ignore

    mi = MarketIntelligence.__new__(MarketIntelligence)
    fake_store = FakeVectorStore()
    mi.vector_store = fake_store
    mi.retrieval = RetrievalEngine(fake_store)
    mi.llm = lambda prompt, system=None: "ok"

    fake_docs = [
        {
            "id": "edgar-0001193125-26-300004",
            "content": "SEC EDGAR 424B2 filing - Issuer X on 2026-04-15.",
            "doc_type": "deal",
            "asset_class": "EQUITY",
            "as_of": "2026-04-15",
            "issuer": "Issuer X",
            "form_type": "424B2",
            "matched_query": "autocallable",
            "accession": "0001193125-26-300004",
            "filing_url": "https://www.sec.gov/cgi-bin/browse-edgar?...",
            "source": "EDGAR",
        }
    ]

    captured: Dict[str, Any] = {}

    def fake_fetch(*, user_agent: str, days_back: int = 90):
        captured["user_agent"] = user_agent
        captured["days_back"] = days_back
        return list(fake_docs)

    import src.data.edgar_ingester as edgar_mod
    monkeypatch.setattr(edgar_mod, "fetch_edgar_filings", fake_fetch)

    n = mi.seed_from_edgar(user_agent="VolDesk test@example.com", days_back=30)

    assert n == 1
    assert captured == {"user_agent": "VolDesk test@example.com", "days_back": 30}
    assert fake_store.count() == 1
    assert fake_store.docs[0]["id"] == "edgar-0001193125-26-300004"
    assert fake_store.docs[0]["metadata"]["source"] == "EDGAR"
    assert fake_store.docs[0]["metadata"]["doc_type"] == "deal"


def test_seed_from_edgar_returns_zero_when_no_docs(monkeypatch):
    """Empty fetch -> zero seeded, never raises."""
    from src.agents.market_intelligence import (
        MarketIntelligence,
        RetrievalEngine,
    )
    from tests.test_market_intelligence_integration import FakeVectorStore  # type: ignore

    mi = MarketIntelligence.__new__(MarketIntelligence)
    fake_store = FakeVectorStore()
    mi.vector_store = fake_store
    mi.retrieval = RetrievalEngine(fake_store)
    mi.llm = lambda prompt, system=None: "ok"

    import src.data.edgar_ingester as edgar_mod
    monkeypatch.setattr(edgar_mod, "fetch_edgar_filings",
                        lambda *, user_agent, days_back=90: [])

    n = mi.seed_from_edgar(user_agent="VolDesk test@example.com")
    assert n == 0
    assert fake_store.count() == 0


# ---------------------------------------------------------------------------
# Defaults exposed for callers
# ---------------------------------------------------------------------------


def test_default_queries_constant():
    """Sanity: the default search list is the structured-note quartet."""
    assert DEFAULT_QUERIES == [
        "autocallable",
        "buffered note",
        "barrier note",
        "structured note",
    ]


# ---------------------------------------------------------------------------
# Optional live probe — opt-in only
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("RUN_LIVE_EDGAR"),
    reason="Set RUN_LIVE_EDGAR=1 to hit the live SEC EDGAR API.",
)
def test_live_edgar_smoke():
    docs = fetch_edgar_filings(
        user_agent="VolDesk admin@example.com",
        days_back=30,
    )
    # We can't assert > 0 deterministically (SEC may be quiet), but we can
    # assert the call shape didn't raise and emitted a list of dicts.
    assert isinstance(docs, list)
    if docs:
        assert "edgar-" in docs[0]["id"]
