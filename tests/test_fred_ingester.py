"""Tests for src.data.fred_ingester and the FRED → MI seed path.

No real network calls: httpx is monkeypatched. Covers:
  * Happy path — six default series, one doc per series with the right schema.
  * Missing-value robustness — FRED returns '.' as the freshest, fall through.
  * No API key — function returns [] without raising.
  * Network failure — bad responses are skipped, not raised.
  * MarketIntelligence.seed_from_fred end-to-end with the in-memory store.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from src.data.fred_ingester import (
    DEFAULT_FRED_SERIES,
    FredSeriesSpec,
    _format_doc,
    fetch_fred_documents,
)


class _FakeResponse:
    def __init__(self, payload: Dict[str, Any], status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _make_fred_payload(value: str, date: str = "2026-04-29") -> Dict[str, Any]:
    return {"observations": [{"date": date, "value": value}]}


def _make_multi_payload(values: List[str], dates: Optional[List[str]] = None) -> Dict[str, Any]:
    """Simulate FRED returning several rows (newest first), some possibly '.'."""
    dates = dates or [f"2026-04-{29 - i:02d}" for i in range(len(values))]
    return {
        "observations": [
            {"date": d, "value": v} for d, v in zip(dates, values)
        ]
    }


# ---------------------------------------------------------------------------
# fetch_fred_documents
# ---------------------------------------------------------------------------


def test_no_api_key_returns_empty(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    docs = fetch_fred_documents()
    assert docs == []


def test_happy_path_one_doc_per_series(monkeypatch):
    """All six default series resolve cleanly — six docs out, schema enforced."""
    fixed_values = {
        "DFF": "5.33",
        "SOFR": "5.34",
        "DGS3MO": "5.20",
        "DGS10": "4.45",
        "T10Y2Y": "-0.18",
        "VIXCLS": "14.27",
    }
    captured_calls: List[Dict[str, Any]] = []

    def fake_get(url, params=None, timeout=None, **kwargs):
        captured_calls.append({"url": url, "params": dict(params or {})})
        sid = (params or {}).get("series_id")
        return _FakeResponse(_make_fred_payload(fixed_values[sid]))

    import src.data.fred_ingester as fi
    monkeypatch.setattr(fi.httpx, "get", fake_get)

    docs = fetch_fred_documents(api_key="test-key")

    assert len(docs) == len(DEFAULT_FRED_SERIES) == 6
    assert len(captured_calls) == 6
    for call in captured_calls:
        assert call["params"]["api_key"] == "test-key"
        assert call["params"]["file_type"] == "json"
        assert call["params"]["sort_order"] == "desc"

    expected_ids = {f"fred-{s.series_id.lower()}-2026-04-29" for s in DEFAULT_FRED_SERIES}
    assert {d["id"] for d in docs} == expected_ids
    for d in docs:
        assert d["doc_type"] == "macro"
        assert d["asset_class"] == "MACRO"
        assert d["source"] == "FRED"
        assert d["observation_date"] == "2026-04-29"
        assert "FRED:" in d["content"]


def test_dot_value_falls_through_to_next_observation(monkeypatch):
    """When FRED's freshest observation is '.', skip and use the next one."""
    payload = _make_multi_payload([".", ".", "5.30"])

    def fake_get(url, params=None, timeout=None, **kwargs):
        return _FakeResponse(payload)

    import src.data.fred_ingester as fi
    monkeypatch.setattr(fi.httpx, "get", fake_get)

    docs = fetch_fred_documents(
        api_key="test-key",
        series=[FredSeriesSpec("DFF", "Federal Funds", "%", True)],
    )
    assert len(docs) == 1
    assert docs[0]["value_raw"] == "5.30"
    assert docs[0]["observation_date"] == "2026-04-27"


def test_all_dots_yields_no_doc(monkeypatch):
    payload = _make_multi_payload([".", ".", "."])

    def fake_get(url, params=None, timeout=None, **kwargs):
        return _FakeResponse(payload)

    import src.data.fred_ingester as fi
    monkeypatch.setattr(fi.httpx, "get", fake_get)

    docs = fetch_fred_documents(
        api_key="test-key",
        series=[FredSeriesSpec("DFF", "Federal Funds", "%", True)],
    )
    assert docs == []


def test_network_failure_is_skipped_not_raised(monkeypatch):
    """One series throws, the rest succeed — function returns the survivors."""
    sequence = iter([
        _FakeResponse(_make_fred_payload("5.33")),  # DFF ok
        Exception("boom"),                            # SOFR throws
        _FakeResponse(_make_fred_payload("5.20")),  # DGS3MO ok
    ])

    def fake_get(url, params=None, timeout=None, **kwargs):
        nxt = next(sequence)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    import src.data.fred_ingester as fi
    monkeypatch.setattr(fi.httpx, "get", fake_get)

    series = [
        FredSeriesSpec("DFF", "Fed Funds", "%", True),
        FredSeriesSpec("SOFR", "SOFR", "%", True),
        FredSeriesSpec("DGS3MO", "3M Treasury", "%", True),
    ]
    docs = fetch_fred_documents(api_key="test-key", series=series)
    assert len(docs) == 2
    series_ids = {d["series_id"] for d in docs}
    assert series_ids == {"DFF", "DGS3MO"}


def test_format_doc_percent_decimal_helper():
    """Percent series surface a decimal hint for the LLM."""
    spec = FredSeriesSpec("SOFR", "SOFR", "%", True)
    doc = _format_doc(spec, {"date": "2026-04-29", "value": "5.34"})
    assert "5.34%" in doc["content"]
    assert "decimal 0.0534" in doc["content"]


def test_format_doc_index_no_decimal_hint():
    """Index series (VIX) should not get a decimal-percent hint."""
    spec = FredSeriesSpec("VIXCLS", "VIX", "index", False)
    doc = _format_doc(spec, {"date": "2026-04-29", "value": "14.27"})
    assert "14.27index" in doc["content"]
    assert "decimal" not in doc["content"]


# ---------------------------------------------------------------------------
# MarketIntelligence.seed_from_fred — bridge into the RAG corpus
# ---------------------------------------------------------------------------


def test_seed_from_fred_populates_vector_store(monkeypatch):
    from src.agents.market_intelligence import (
        MarketIntelligence,
        RetrievalEngine,
    )

    # Build a MI with an in-memory store (avoid chromadb / sentence-transformers).
    from tests.test_market_intelligence_integration import FakeVectorStore  # type: ignore

    mi = MarketIntelligence.__new__(MarketIntelligence)
    fake_store = FakeVectorStore()
    mi.vector_store = fake_store
    mi.retrieval = RetrievalEngine(fake_store)
    mi.llm = lambda prompt, system=None: "ok"

    fixed_values = {
        s.series_id: f"{i + 1}.0" for i, s in enumerate(DEFAULT_FRED_SERIES)
    }

    def fake_get(url, params=None, timeout=None, **kwargs):
        sid = (params or {}).get("series_id")
        return _FakeResponse(_make_fred_payload(fixed_values[sid]))

    import src.data.fred_ingester as fi
    monkeypatch.setattr(fi.httpx, "get", fake_get)

    n = mi.seed_from_fred(api_key="test-key")
    assert n == len(DEFAULT_FRED_SERIES)
    assert fake_store.count() == len(DEFAULT_FRED_SERIES)

    # Every doc carries the macro tagging — agents that filter on MACRO see them.
    macros = [d for d in fake_store.docs if d["metadata"].get("doc_type") == "macro"]
    assert len(macros) == len(DEFAULT_FRED_SERIES)
    assert all(d["metadata"].get("source") == "FRED" for d in macros)


def test_seed_from_fred_returns_zero_when_no_key(monkeypatch):
    from src.agents.market_intelligence import (
        MarketIntelligence,
        RetrievalEngine,
    )
    from tests.test_market_intelligence_integration import FakeVectorStore  # type: ignore

    monkeypatch.delenv("FRED_API_KEY", raising=False)

    mi = MarketIntelligence.__new__(MarketIntelligence)
    fake_store = FakeVectorStore()
    mi.vector_store = fake_store
    mi.retrieval = RetrievalEngine(fake_store)
    mi.llm = lambda prompt, system=None: "ok"

    n = mi.seed_from_fred()
    assert n == 0
    assert fake_store.count() == 0
