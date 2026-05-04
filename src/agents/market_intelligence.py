"""
market_intelligence.py
======================

Self-contained market-intelligence (RAG) module to drop into the Vol Desk
repo (`StructuredFinanceAI` → Vol Desk Platform). Gives the existing
QuantLib-driven structuring agents (Intake, Strategist, Pricing, Scenario,
Validator, Narrator) grounded market knowledge: market windows, comparable
deals, pricing benchmarks, free-form Q&A.

Everything below is vendored from the StructuredFinanceAI repo and collapsed
into a single module so you can copy ONE file across.

------------------------------------------------------------------------
WHERE TO PUT THIS FILE IN THE OTHER REPO
------------------------------------------------------------------------
    src/agents/market_intelligence.py

------------------------------------------------------------------------
DEPENDENCIES (add to requirements.txt of the Vol Desk repo)
------------------------------------------------------------------------
    chromadb>=0.4
    sentence-transformers>=2.2
    loguru>=0.7

The LLM is injected — you reuse the Gemini client the Vol Desk agents
already use. No Anthropic dep required.

------------------------------------------------------------------------
QUICK INTEGRATION (3 STEPS)
------------------------------------------------------------------------
1. Build the intelligence layer once at app startup:

       from src.agents.market_intelligence import MarketIntelligence, gemini_adapter
       from google import genai

       gemini = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
       mi = MarketIntelligence(
           llm_call=gemini_adapter(gemini, model="gemini-2.0-flash"),
           persist_dir="./data/market_intel",
       )

2. Seed it once with whatever market data you have (deals, market-window
   notes, pricing benchmarks):

       mi.seed_from_dicts([
           {
               "id": "deal-2024-clo-tech-i",
               "doc_type": "deal",
               "asset_class": "CLO",
               "content": "Tech Portfolio CLO 2024-I, $500M, AAA +110bps...",
           },
           {
               "id": "mw-clo-2025q1",
               "doc_type": "market_window",
               "asset_class": "CLO",
               "content": "CLO market remains OPEN with strong demand for AAA...",
           },
       ])

3. Inside any structuring agent, ask for market context before building
   its tool call:

       ctx = mi.query_pricing(asset_class="CLO", tranche_type="BBB",
                              deal_size=500)
       # Pass ctx.answer + ctx.sources into the agent's prompt so it
       # frames the QuantLib pricing in terms of where the market is today.

That's it — your QuantLib pricing pipeline keeps producing numbers, and
the agents now reason about them with grounded market knowledge.
"""

from __future__ import annotations

import json
import logging as _stdlib_logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

# loguru is the upstream module's logger of choice but it's an optional dep
# from the host repo's perspective. Fall back to stdlib logging cleanly so
# `from src.agents.market_intelligence import ...` never fails just because
# loguru wasn't pip-installed yet.
try:  # pragma: no cover — covered by both branches at import time
    from loguru import logger  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    logger = _stdlib_logging.getLogger("market_intelligence")

from .prompts import load_prompt


# =====================================================================
# Types
# =====================================================================

@dataclass
class Document:
    id: str
    content: str
    metadata: Dict[str, Any]


@dataclass
class SearchResult:
    doc_id: str
    content: str
    metadata: Dict[str, Any]
    score: float


@dataclass
class QueryResponse:
    answer: str
    sources: List[Dict[str, Any]]
    confidence: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "answer": self.answer,
            "sources": self.sources,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


# `LLMCall` is the only contract market_intelligence needs from the host
# LLM. Any callable matching this signature works (Gemini, Claude, OpenAI,
# a mock for tests, etc.).
LLMCall = Callable[[str, Optional[str]], str]
# signature: llm_call(prompt: str, system: Optional[str]) -> str


# =====================================================================
# Embeddings (sentence-transformers)
# =====================================================================

class EmbeddingsManager:
    """sentence-transformers wrapper. Loads once, embeds many."""

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer  # local import: heavy

        logger.info(f"Loading embeddings model: {model_name}")
        self.model = SentenceTransformer(model_name)
        self.model_name = model_name
        self.embedding_dim = self.model.get_sentence_embedding_dimension()

    def embed_text(self, text: str) -> List[float]:
        return self.model.encode(text, convert_to_tensor=False).tolist()

    def embed_texts(self, texts: Sequence[str], batch_size: int = 32) -> List[List[float]]:
        emb = self.model.encode(
            list(texts), batch_size=batch_size, show_progress_bar=False,
            convert_to_tensor=False,
        )
        return emb.tolist()


# =====================================================================
# Vector store (Chroma; abstract base lets you swap in Pinecone later)
# =====================================================================

class VectorStore(ABC):
    @abstractmethod
    def add_documents(self, docs: Sequence[Document]) -> None: ...
    @abstractmethod
    def search(self, query: str, k: int = 5,
               filters: Optional[Dict[str, Any]] = None) -> List[SearchResult]: ...
    @abstractmethod
    def count(self) -> int: ...


class ChromaVectorStore(VectorStore):
    """Local Chroma store with cosine similarity."""

    def __init__(
        self,
        collection_name: str = "market-intelligence",
        persist_dir: str = "./data/chroma",
        embeddings: Optional[EmbeddingsManager] = None,
    ):
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        self.embeddings = embeddings or EmbeddingsManager()
        self.collection_name = collection_name

        os.makedirs(persist_dir, exist_ok=True)
        try:
            # Newer chromadb (>=0.4)
            self.client = chromadb.PersistentClient(path=persist_dir)
        except AttributeError:
            # Older chromadb fallback
            settings = ChromaSettings(
                chroma_db_impl="duckdb+parquet",
                persist_directory=persist_dir,
                anonymized_telemetry=False,
            )
            self.client = chromadb.Client(settings)

        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(f"Chroma collection ready: {collection_name}")

    def add_documents(self, docs: Sequence[Document]) -> None:
        if not docs:
            return
        ids = [d.id for d in docs]
        contents = [d.content for d in docs]
        metadatas = [d.metadata for d in docs]
        embeddings = self.embeddings.embed_texts(contents)
        self.collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=contents,
            metadatas=metadatas,
        )
        logger.info(f"Added {len(docs)} documents to {self.collection_name}")

    def search(
        self,
        query: str,
        k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        query_embedding = self.embeddings.embed_text(query)
        # Chroma >= 0.5 requires $and for multi-key where clauses; single-key
        # dicts pass through unchanged.
        where_clause: Optional[Dict[str, Any]] = filters or None
        if where_clause and len(where_clause) > 1:
            where_clause = {"$and": [{k: v} for k, v in where_clause.items()]}
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=k,
            where=where_clause,
        )
        out: List[SearchResult] = []
        if results.get("ids") and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                # Chroma returns squared L2 in [0,2] for cosine; convert to similarity.
                distance = results["distances"][0][i]
                score = 1.0 - (distance / 2.0)
                out.append(SearchResult(
                    doc_id=doc_id,
                    content=results["documents"][0][i],
                    metadata=results["metadatas"][0][i] or {},
                    score=score,
                ))
        return out

    def count(self) -> int:
        return self.collection.count()


# =====================================================================
# Retrieval engine
# =====================================================================

class RetrievalEngine:
    """Filtered retrieval over a VectorStore."""

    def __init__(self, vector_store: VectorStore):
        self.vector_store = vector_store

    def retrieve(
        self,
        query: str,
        k: int = 5,
        asset_class: Optional[str] = None,
        doc_type: Optional[str] = None,
        min_score: float = 0.45,
    ) -> List[SearchResult]:
        filters: Dict[str, Any] = {}
        if asset_class:
            filters["asset_class"] = asset_class
        if doc_type:
            filters["doc_type"] = doc_type
        results = self.vector_store.search(query, k=k, filters=filters or None)
        return [r for r in results if r.score >= min_score]

    def comparable_deals(
        self,
        deal_characteristics: Dict[str, Any],
        k: int = 5,
    ) -> List[SearchResult]:
        parts: List[str] = []
        for key in ("asset_class", "rating", "collateral_type", "tranche_type"):
            if key in deal_characteristics and deal_characteristics[key]:
                parts.append(str(deal_characteristics[key]))
        query = " ".join(parts) or "comparable deals"
        filters: Dict[str, Any] = {"doc_type": "deal"}
        if "asset_class" in deal_characteristics:
            filters["asset_class"] = deal_characteristics["asset_class"]
        return self.vector_store.search(query, k=k, filters=filters)

    def market_windows(self, asset_class: str, k: int = 3) -> List[SearchResult]:
        return self.vector_store.search(
            query=f"{asset_class} market window issuance conditions",
            k=k,
            filters={"doc_type": "market_window", "asset_class": asset_class},
        )

    def pricing_benchmarks(
        self,
        asset_class: str,
        tranche_type: Optional[str] = None,
        k: int = 5,
    ) -> List[SearchResult]:
        q = f"{asset_class} pricing benchmark"
        if tranche_type:
            q += f" {tranche_type}"
        return self.vector_store.search(
            query=q,
            k=k,
            filters={"doc_type": "pricing_benchmark", "asset_class": asset_class},
        )


# =====================================================================
# Prompt templates
# =====================================================================

class PromptManager:
    SYSTEM_MARKET_INTELLIGENCE = load_prompt("market_intelligence/system_market_intelligence.md")
    SYSTEM_PRICING = load_prompt("market_intelligence/system_pricing.md")
    SYSTEM_DEAL_ANALYSIS = load_prompt("market_intelligence/system_deal_analysis.md")

    TEMPLATE_MARKET_WINDOW = load_prompt("market_intelligence/template_market_window.md")
    TEMPLATE_PRICING_BENCHMARK = load_prompt("market_intelligence/template_pricing_benchmark.md")
    TEMPLATE_DEAL_INTELLIGENCE = load_prompt("market_intelligence/template_deal_intelligence.md")

    @staticmethod
    def format_deal_data(deal: Dict[str, Any]) -> str:
        return "\n".join(f"- {k}: {v}" for k, v in deal.items())

    @staticmethod
    def format_comparables(comparables: Iterable[Dict[str, Any]]) -> str:
        out: List[str] = []
        for i, deal in enumerate(comparables, 1):
            out.append(f"\nDeal {i}:")
            out.append(PromptManager.format_deal_data(deal))
        return "".join(out)


# =====================================================================
# LLM adapters (so this module is LLM-agnostic)
# =====================================================================

def gemini_adapter(client: Any, model: str = "gemini-2.0-flash") -> LLMCall:
    """Wrap a `google.genai` client into the LLMCall contract.

    Usage:
        from google import genai
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        llm_call = gemini_adapter(client, model="gemini-2.0-flash")
    """
    def _call(prompt: str, system: Optional[str] = None) -> str:
        contents = prompt if not system else f"{system}\n\n{prompt}"
        resp = client.models.generate_content(model=model, contents=contents)
        return getattr(resp, "text", "") or ""
    return _call


def anthropic_adapter(
    api_key: Optional[str] = None,
    model: str = "claude-opus-4-7",
    max_tokens: int = 2000,
    temperature: float = 0.7,
) -> LLMCall:
    """Optional fallback if you'd rather use Claude."""
    from anthropic import Anthropic  # only imported if you call this

    client = Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    def _call(prompt: str, system: Optional[str] = None) -> str:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text
    return _call


def openrouter_adapter(
    api_key: Optional[str] = None,
    model: str = "anthropic/claude-haiku-4-5",
    max_tokens: int = 2000,
    temperature: float = 0.7,
    timeout_s: float = 60.0,
    referer: Optional[str] = None,
    title: Optional[str] = None,
    http_client: Optional[Any] = None,
) -> LLMCall:
    """Wrap OpenRouter's chat-completions endpoint into the LLMCall contract.

    OpenRouter aggregates 100+ models (Anthropic, OpenAI, Google, Meta,
    Mistral, ...) behind one OpenAI-compatible API. Pass any provider/model
    id and OpenRouter routes it. Sign up at https://openrouter.ai —
    pay-as-you-go, no minimum.

    Args:
        api_key: OpenRouter API key. Falls back to OPENROUTER_API_KEY env var.
        model: OpenRouter model id (provider/model). Examples:
            'anthropic/claude-opus-4-7', 'anthropic/claude-haiku-4-5',
            'openai/gpt-5', 'google/gemini-3-pro-preview',
            'meta-llama/llama-3.3-70b-instruct'.
        max_tokens: cap on response tokens.
        temperature: sampling temperature.
        timeout_s: per-call timeout (httpx).
        referer: optional HTTP-Referer header (improves OpenRouter rankings).
        title: optional X-Title header (improves OpenRouter rankings).
        http_client: optional httpx.Client (for testing or pooling). When
            None, a one-shot client is created and closed per call.

    Failure mode:
        Network / HTTP errors are logged and return "" so the structuring
        pipeline degrades the same way it does on Gemini failures (no
        citations rather than crashing the agent).
    """
    import httpx

    key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise ValueError(
            "openrouter_adapter requires an api_key argument or "
            "OPENROUTER_API_KEY env var."
        )

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if referer:
        headers["HTTP-Referer"] = referer
    if title:
        headers["X-Title"] = title

    url = "https://openrouter.ai/api/v1/chat/completions"

    def _call(prompt: str, system: Optional[str] = None) -> str:
        messages: List[Dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        client = http_client or httpx.Client(timeout=timeout_s)
        try:
            resp = client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            logger.warning(f"OpenRouter call failed: {exc}")
            return ""
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"OpenRouter call returned unparseable body: {exc}")
            return ""
        finally:
            if http_client is None:
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    pass

        choices = data.get("choices") or []
        if not choices:
            logger.warning(f"OpenRouter returned no choices: {data}")
            return ""
        msg = choices[0].get("message") or {}
        return msg.get("content") or ""

    return _call


# =====================================================================
# Public facade — what the structuring agents call
# =====================================================================

class MarketIntelligence:
    """Drop-in market-knowledge layer for the Vol Desk structuring agents.

    Build once at startup, share across agents.
    """

    def __init__(
        self,
        llm_call: LLMCall,
        persist_dir: str = "./data/market_intel",
        collection_name: str = "market-intelligence",
        embeddings_model: str = "sentence-transformers/all-MiniLM-L6-v2",
    ):
        embeddings = EmbeddingsManager(model_name=embeddings_model)
        self.vector_store: VectorStore = ChromaVectorStore(
            collection_name=collection_name,
            persist_dir=persist_dir,
            embeddings=embeddings,
        )
        self.retrieval = RetrievalEngine(self.vector_store)
        self.llm = llm_call
        logger.info("MarketIntelligence initialized")

    # -----------------------------------------------------------------
    # Ingestion helpers
    # -----------------------------------------------------------------

    def seed_from_dicts(self, items: Sequence[Dict[str, Any]]) -> None:
        """Each item must have: id, content. Optional: doc_type, asset_class, ...

        Recognised doc_type values used by the query helpers:
            - "deal"               (a comparable deal)
            - "market_window"      (a market-window note for an asset class)
            - "pricing_benchmark"  (a pricing benchmark)
            - "macro"              (FRED-sourced macro observations)
            - any free-form string falls through to general_query

        Recognised asset_class values are free-form (e.g. "CLO", "RMBS",
        "CMBS", "ABS", "MACRO"). Match what your agents pass in.
        """
        docs: List[Document] = []
        for raw in items:
            if "id" not in raw or "content" not in raw:
                raise ValueError("Each item must have 'id' and 'content' keys")
            metadata = {k: v for k, v in raw.items() if k not in ("id", "content")}
            docs.append(Document(id=raw["id"], content=raw["content"], metadata=metadata))
        self.vector_store.add_documents(docs)

    def seed_from_fred(
        self,
        api_key: Optional[str] = None,
        series: Optional[Sequence[Any]] = None,
    ) -> int:
        """Pull macro series from FRED and seed them into the vector store.

        Wraps :func:`src.data.fred_ingester.fetch_fred_documents` so a host
        app can wire FRED → MI in one call. Safe to call repeatedly: each
        observation's doc id includes its date, so re-running the same day
        is a no-op against Chroma's upsert semantics.

        Args:
            api_key: FRED API key. Falls back to ``FRED_API_KEY`` env var.
            series: Optional override for the curated series list. ``None``
                means use :data:`DEFAULT_FRED_SERIES`.

        Returns:
            Number of documents successfully seeded. ``0`` when the key is
            missing or every fetch failed.
        """
        from src.data.fred_ingester import fetch_fred_documents, DEFAULT_FRED_SERIES

        docs = fetch_fred_documents(
            api_key=api_key,
            series=series if series is not None else DEFAULT_FRED_SERIES,
        )
        if not docs:
            return 0
        self.seed_from_dicts(docs)
        logger.info(f"Seeded {len(docs)} FRED macro documents into MI corpus")
        return len(docs)

    def seed_from_cboe(self) -> int:
        """Pull VIX history (and optional term structure) from CBOE and seed MI.

        Wraps :func:`src.data.cboe_ingester.fetch_cboe_documents`. Idempotent
        across calls on the same trading day because each doc id is keyed off
        the observation date. Not auto-called on startup — the host app must
        invoke this explicitly so a slow CBOE fetch can't block boot.

        Returns:
            Number of documents seeded. ``0`` when the CDN is unreachable or
            the CSV came back empty.
        """
        from src.data.cboe_ingester import fetch_cboe_documents

        docs = fetch_cboe_documents()
        if not docs:
            return 0
        self.seed_from_dicts(docs)
        logger.info(f"Seeded {len(docs)} CBOE VIX docs into MI corpus")
        return len(docs)

    def seed_from_edgar(
        self,
        user_agent: Optional[str] = None,
        days_back: int = 90,
    ) -> int:
        """Pull recent structured-note filings from SEC EDGAR and seed them.

        Wraps :func:`src.data.edgar_ingester.fetch_edgar_filings`. Not auto-
        called from ``get_market_intelligence`` startup — a 90-day fetch
        is too slow to block app boot. Callers should invoke this from
        a seed script.

        Args:
            user_agent: SEC-required ``"<name> <email>"`` string. Falls
                back to ``EDGAR_USER_AGENT`` env var, then to a safe
                default identifying VolDesk.
            days_back: Trailing window in days (default 90).

        Returns:
            Number of documents seeded. ``0`` when EDGAR returned nothing.
        """
        from src.data.edgar_ingester import fetch_edgar_filings

        ua = user_agent or os.environ.get(
            "EDGAR_USER_AGENT", "VolDesk MarketIntel admin@example.com"
        )
        docs = fetch_edgar_filings(user_agent=ua, days_back=days_back)
        if not docs:
            return 0
        self.seed_from_dicts(docs)
        logger.info(
            f"Seeded {len(docs)} EDGAR structured-note filings into MI corpus"
        )
        return len(docs)

    def count(self) -> int:
        return self.vector_store.count()

    # -----------------------------------------------------------------
    # Agent-facing queries
    # -----------------------------------------------------------------

    def query_market_window(
        self,
        asset_class: str,
        context: Optional[str] = None,
    ) -> QueryResponse:
        """Used by the Strategist / Validator agents to assess timing."""
        market_docs = self.retrieval.retrieve(
            query=f"{asset_class} market window issuance",
            k=5, asset_class=asset_class, doc_type="market_window",
        )
        recent_deals = self.retrieval.retrieve(
            query=f"{asset_class} recent deals pricing",
            k=10, asset_class=asset_class, doc_type="deal",
        )

        market_data = "\n".join(d.content[:500] for d in market_docs)
        deals_summary = "\n".join(d.content[:300] for d in recent_deals)

        prompt = PromptManager.TEMPLATE_MARKET_WINDOW.format(
            asset_class=asset_class,
            recent_deals=deals_summary or "(none)",
            market_data=market_data or "(none)",
        )
        if context:
            prompt += f"\n\nAdditional Context: {context}"

        answer = self.llm(prompt, PromptManager.SYSTEM_MARKET_INTELLIGENCE)
        sources = _build_sources(market_docs + recent_deals)
        confidence = "high" if len(market_docs) > 3 else "medium"
        return QueryResponse(answer=answer, sources=sources,
                             confidence=confidence,
                             metadata={"asset_class": asset_class})

    def query_pricing(
        self,
        asset_class: str,
        tranche_type: str,
        deal_size: Optional[float] = None,
        collateral_info: Optional[Dict[str, Any]] = None,
    ) -> QueryResponse:
        """Used by the Pricing agent to ground QuantLib output in market context."""
        deal_chars: Dict[str, Any] = {
            "asset_class": asset_class,
            "tranche_type": tranche_type,
        }
        if collateral_info:
            deal_chars.update(collateral_info)

        comparables = self.retrieval.comparable_deals(deal_chars, k=5)
        benchmarks = self.retrieval.pricing_benchmarks(asset_class, tranche_type)
        market_intel = self.retrieval.retrieve(
            query=f"{asset_class} market conditions spreads",
            k=3, asset_class=asset_class,
        )

        comparable_summary = "\n".join(d.content[:400] for d in comparables)
        benchmark_summary = "\n".join(d.content[:400] for d in benchmarks)
        market_summary = "\n".join(d.content[:300] for d in market_intel)

        prompt = PromptManager.TEMPLATE_PRICING_BENCHMARK.format(
            tranche_type=tranche_type,
            asset_class=asset_class,
            comparable_deals=comparable_summary or "(none)",
            market_conditions=market_summary or benchmark_summary or "(none)",
        )
        if deal_size:
            prompt += f"\nDeal Size: ${deal_size}M"

        answer = self.llm(prompt, PromptManager.SYSTEM_PRICING)
        sources = _build_sources(comparables + benchmarks + market_intel)
        confidence = "high" if len(comparables) > 3 else "medium"
        return QueryResponse(
            answer=answer, sources=sources, confidence=confidence,
            metadata={"asset_class": asset_class, "tranche_type": tranche_type},
        )

    def query_deal_analysis(
        self,
        deal_summary: Dict[str, Any],
        asset_class: str,
    ) -> QueryResponse:
        """Used by the Strategist / Narrator to position a deal vs. market."""
        comparables = self.retrieval.comparable_deals(
            {"asset_class": asset_class}, k=5,
        )

        deal_text = PromptManager.format_deal_data(deal_summary)
        comparables_text = PromptManager.format_comparables(
            d.metadata for d in comparables
        )

        prompt = PromptManager.TEMPLATE_DEAL_INTELLIGENCE.format(
            asset_class=asset_class,
            deal_summary=deal_text,
            comparables=comparables_text or "(none)",
        )

        answer = self.llm(prompt, PromptManager.SYSTEM_DEAL_ANALYSIS)
        sources = _build_sources(comparables)
        confidence = "high" if len(comparables) > 3 else "medium"
        return QueryResponse(
            answer=answer, sources=sources, confidence=confidence,
            metadata={"asset_class": asset_class},
        )

    def general_query(
        self,
        query: str,
        asset_class: Optional[str] = None,
    ) -> QueryResponse:
        """Free-form Q&A over the corpus. Used by Intake / Scenario agents."""
        retrieved = self.retrieval.retrieve(
            query=query, k=5, asset_class=asset_class,
        )

        if not retrieved:
            return QueryResponse(
                answer="No relevant documents found in the knowledge base.",
                sources=[], confidence="low", metadata={"query": query},
            )

        context = "\n\n".join(
            f"Source {i+1}: {d.content[:500]}"
            for i, d in enumerate(retrieved[:3])
        )
        prompt = (
            "Based on the following market intelligence, please answer "
            "this question:\n\n"
            f"Question: {query}\n\n"
            f"Market Intelligence Context:\n{context}\n\n"
            "Provide a specific, evidence-based answer that cites the "
            "relevant information from the context."
        )

        answer = self.llm(prompt, PromptManager.SYSTEM_MARKET_INTELLIGENCE)
        sources = _build_sources(retrieved)
        top_score = max((d.score for d in retrieved), default=0.0)
        confidence = "high" if top_score > 0.8 else "medium"
        return QueryResponse(
            answer=answer, sources=sources, confidence=confidence,
            metadata={"query": query},
        )

    # -----------------------------------------------------------------
    # Convenience JSON wrapper for tool-style invocation by agents
    # -----------------------------------------------------------------

    def as_tool(self, intent: str, **params: Any) -> Dict[str, Any]:
        """Single entry point an agent can call by name.

        intent ∈ {"market_window", "pricing", "deal_analysis", "general"}.
        Returns a JSON-serialisable dict for splicing into an agent's
        scratchpad / SSE payload.
        """
        if intent == "market_window":
            return self.query_market_window(**params).to_dict()
        if intent == "pricing":
            return self.query_pricing(**params).to_dict()
        if intent == "deal_analysis":
            return self.query_deal_analysis(**params).to_dict()
        if intent == "general":
            return self.general_query(**params).to_dict()
        raise ValueError(f"Unknown intent: {intent}")


# =====================================================================
# Internals
# =====================================================================

def _build_sources(results: Sequence[SearchResult]) -> List[Dict[str, Any]]:
    """Pass through enough doc metadata to render comparable-deals citations.

    The Narrator consumes this list (via ``session.market_context``) to render
    the "Recent Comparable Deals" section. Including ``as_of``, ``asset_class``,
    and a content snippet here means the Narrator never has to re-query the
    corpus — citations are self-contained and the memo proves corpus freshness
    without an extra round-trip.
    """
    out: List[Dict[str, Any]] = []
    for r in results:
        meta = r.metadata or {}
        src: Dict[str, Any] = {
            "id": r.doc_id,
            "type": meta.get("doc_type"),
            "score": round(r.score, 3),
        }
        if meta.get("as_of"):
            src["as_of"] = meta["as_of"]
        if meta.get("asset_class"):
            src["asset_class"] = meta["asset_class"]
        if r.content:
            snippet = r.content.strip().replace("\n", " ")
            src["snippet"] = snippet[:220] + ("…" if len(snippet) > 220 else "")
        out.append(src)
    return out


# =====================================================================
# Adapter for the host repo's existing LLMClient (Gemini, with cost +
# replay support). Reuses one LLMClient — no second SDK instance.
# =====================================================================

def existing_llm_adapter(
    client: Any,
    model: str,
    agent_name: str = "MarketIntelligence",
    replay_key: Optional[str] = None,
) -> LLMCall:
    """Wrap the host repo's `src.agents.llm_client.LLMClient` into LLMCall.

    The point: route MI's prompts through the same client the structuring
    agents already use, so DEMO_REPLAY, retries, cost tracking, and JSON
    coercion stay consistent across the platform.
    """

    def _call(prompt: str, system: Optional[str] = None) -> str:
        try:
            res = client.complete(
                agent_name=agent_name,
                model=model,
                system=system or "",
                messages=[{"role": "user", "content": prompt}],
                json_mode=False,
                replay_key=replay_key or f"{agent_name}:default",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"MarketIntelligence LLM call failed: {exc}")
            return ""
        return getattr(res, "text", "") or ""

    return _call


# =====================================================================
# Singleton — built once at app startup, shared across agents.
# =====================================================================

_GLOBAL_MI: Optional["MarketIntelligence"] = None


def get_market_intelligence() -> Optional["MarketIntelligence"]:
    """Lazy singleton. Returns None when MARKET_INTEL_ENABLED is off OR when
    initialisation fails (missing deps, no API key in non-replay mode, etc.).

    Agents call this at construction; the orchestrator passes the resolved
    reference (or None) into each agent. None disables MI cleanly — agents
    fall back to pure-QuantLib behaviour with no errors.
    """
    global _GLOBAL_MI
    if _GLOBAL_MI is not None:
        return _GLOBAL_MI

    try:
        from src.config.agent_config import get_agent_config
        from src.agents.llm_client import get_llm_client
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"MarketIntelligence cannot resolve config/llm_client: {exc}")
        return None

    cfg = get_agent_config()
    if not cfg.market_intel_active:
        return None

    try:
        # Pick the synthesis-LLM adapter. DEMO_REPLAY always routes through
        # the existing LLMClient so canned responses are returned. Otherwise
        # branch on the configured provider.
        if cfg.demo_replay or cfg.market_intel_llm_provider == "gemini":
            client = get_llm_client()
            llm_call = existing_llm_adapter(
                client,
                model=cfg.market_intel_model,
                agent_name="MarketIntelligence",
                replay_key="MarketIntelligence:default",
            )
        elif cfg.market_intel_llm_provider == "openrouter":
            llm_call = openrouter_adapter(
                api_key=cfg.openrouter_api_key,
                model=cfg.market_intel_openrouter_model,
                referer=cfg.market_intel_openrouter_referer or None,
                title=cfg.market_intel_openrouter_title or None,
            )
            logger.info(
                f"MarketIntelligence: using OpenRouter "
                f"({cfg.market_intel_openrouter_model})"
            )
        else:
            logger.warning(
                f"Unknown MARKET_INTEL_LLM_PROVIDER={cfg.market_intel_llm_provider!r}; "
                "disabling MI."
            )
            return None

        _GLOBAL_MI = MarketIntelligence(
            llm_call=llm_call,
            persist_dir=cfg.market_intel_persist_dir,
            collection_name=cfg.market_intel_collection,
            embeddings_model=cfg.market_intel_embeddings_model,
        )

        # Auto-seed FRED macro corpus if a key is configured. Failure is
        # non-fatal — MI still serves whatever is already in the store.
        if cfg.fred_api_key:
            try:
                seeded = _GLOBAL_MI.seed_from_fred(api_key=cfg.fred_api_key)
                logger.info(f"MarketIntelligence: FRED auto-seed added {seeded} docs")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"FRED auto-seed failed: {exc}")

        return _GLOBAL_MI
    except Exception as exc:  # noqa: BLE001
        # Heavy deps (chromadb, sentence-transformers) might not be installed
        # yet; or the persist dir might be unwritable. Either way, MI off is
        # always a safe degraded mode for the structuring pipeline.
        logger.warning(
            f"MarketIntelligence initialisation failed; running without RAG layer: {exc}"
        )
        _GLOBAL_MI = None
        return None


def reset_market_intelligence() -> None:
    """For tests."""
    global _GLOBAL_MI
    _GLOBAL_MI = None


def set_market_intelligence(mi: Optional["MarketIntelligence"]) -> None:
    """Inject a pre-built MI (used by tests and FastAPI startup)."""
    global _GLOBAL_MI
    _GLOBAL_MI = mi


__all__ = [
    "MarketIntelligence",
    "QueryResponse",
    "Document",
    "SearchResult",
    "VectorStore",
    "ChromaVectorStore",
    "RetrievalEngine",
    "EmbeddingsManager",
    "PromptManager",
    "LLMCall",
    "gemini_adapter",
    "anthropic_adapter",
    "openrouter_adapter",
    "existing_llm_adapter",
    "get_market_intelligence",
    "reset_market_intelligence",
    "set_market_intelligence",
]
