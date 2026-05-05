# Architecture — ArgoPilot Platform

A FastAPI + React derivatives pricing platform with QuantLib as the primary numerical engine. Five layers stack on top of the same engines: a market-intelligence layer (live indices and movers), the Quick Pricer (18 product types via REST), a 7-agent structuring co-pilot with human-in-the-loop gates, a RAG-based market-intelligence (MI) corpus that grounds the agents' reasoning in dealer commentary, and a senior-structurer enrichment layer that adds XVA, bid/offer, cross-Greeks, hedge tickets, termsheets, KIDs, lifecycle re-marking, and book aggregation.

## High-level flow

```
React UI (Vite, port 5173)
    │
    ├── /api/market/movers    → 60s polling → IndexTickerStrip + MoversGrid
    │                                          (click row → prefill ConfigForm)
    │
    ├── POST /api/price       → ConfigForm → ReportDisplay + PayoffChart + GreeksBar
    │
    └── /api/agent/sessions   → CopilotPanel → Intake form → SSE stream
                                              → Gate A/B/C decisions
                                              → final memo + 3 priced candidates
                                              → hedge ticket emitted on Gate C
    │
    ▼
FastAPI app (src/api/main.py, port 8002)
    │
    ├── handlers.price_option (src/api/handlers.py)
    │       │
    │       ├── (optional) Live IV surface build
    │       │     market_data.fetch_option_chain → iv_grid → vol_surface
    │       │
    │       ├── router.route(option_type)        ← dispatch table
    │       │     returns (pricer_fn, greeks_fn, method_label)
    │       │
    │       ├── pricer_fn(...)  → price, std_error, paths
    │       ├── greeks_fn(...)  → {delta, gamma, vega, theta, rho, ...}
    │       │
    │       ├── analysis.xva.compute_xva     → FVA + CVA + DVA overlay
    │       ├── analysis.vanna_volga         → cross-Greeks (auto for KO/KI)
    │       ├── bid/mid/offer derivation     → from mid + xva total
    │       ├── surface_age stamp            → seconds since vol-surface build
    │       ├── report.generator             → HTML report
    │       └── analysis.structurer_agent    → strategist commentary
    │
    ├── /api/market/*   spot, dividend, rate, hist-vol, dividend-info, movers
    │       (Yahoo Finance via market_data.py + movers.py)
    │
    └── /api/agent/*    multi-agent structuring co-pilot + structurer artefacts
            (src/api/agent_router.py → src/agents/orchestrator.py)
            • /sessions/{id}/termsheet      reportlab PDF
            • /sessions/{id}/kid            PRIIPs KID JSON
            • /sessions/{id}/hedge_tickets  emitted at Gate C
            • /sessions/{id}/lifecycle      re-mark prior trade
            • /book                         book-level aggregator
```

## Repository layout

```
src/
├── api/                      FastAPI layer
│   ├── main.py               App + CORS + market data + movers endpoints
│   ├── handlers.py           price_option orchestrator (xva + bid/offer + vanna/volga)
│   ├── models.py             Pydantic request/response schemas (PricingRequest, PricingResult)
│   ├── agent_router.py       /api/agent/* routes (sessions, gates, SSE,
│   │                          termsheet, KID, hedge_tickets, lifecycle, book)
│   ├── agent_models.py       Pydantic schemas for agent endpoints
│   └── market_data.py        Yahoo Finance adapters (option chain, retry/cache)
│
├── engines/                  Pricing engines
│   ├── router.py             Dispatch: option_type → (pricer, greeks, label)
│   ├── quantlib_engine.py    PRIMARY engine (binomial, FDM, barrier, lookback, asian)
│   ├── black_scholes.py      Analytic European fallback
│   ├── knockout.py           Reiner-Rubinstein KO + BGK discrete-monitor shift
│   ├── monte_carlo_lsm.py    Wraps ql.MCAmericanEngine (Longstaff-Schwartz)
│   ├── asian.py              Geometric closed-form + arithmetic MC with geometric CV
│   ├── lookback.py           Fixed-strike + floating-strike lookback (continuous)
│   ├── digitals.py           Cash-or-nothing + asset-or-nothing + shark-fin
│   ├── variance_swap.py      Carr-Madan log-contract replication
│   ├── multi_asset_mc.py     Correlated GBM (Cholesky + antithetic) + worst-of put
│   ├── autocallable.py       Phoenix autocallable MC pricer
│   ├── reverse_convertible.py  Bond + short put composition (NOT router-wired; v2)
│   ├── heston.py             Heston calibration shim (skeleton)
│   └── solver.py             Implied-vol Brent / Newton solver
│
├── data/                     Vol-surface construction + market data + curves
│   ├── iv_grid.py            Invert option-chain quotes → IV grid
│   ├── vol_surface.py        Build ql.BlackVarianceSurface from grid
│   ├── rate_conventions.py   Continuous/discrete rate conversions
│   ├── rate_curve.py         FlatRateCurve + FRED SOFR fetch
│   ├── discounting.py        DiscountingContext (OIS + projection) — single-curve shim default
│   ├── dividend_curve.py     Dividend-yield forecast (linear-decay)
│   ├── correlation.py        Implied correlation (Bakshi-Kapadia-Madan)
│   ├── fx.py                 FX vanilla (Garman-Kohlhagen) — skeleton, v2
│   ├── market_data.py        yfinance adapters with retry/cache
│   └── movers.py             Movers payload (gainers/losers/volatile + indices)
│
├── agents/                   Multi-agent structuring co-pilot
│   ├── state.py              Pydantic StructuringSession + all state types
│   │                          (now includes XVAOverlayState, BidOfferQuote,
│   │                           HedgeTicketState; new StructureKind enums)
│   ├── base.py               Common base + AgentError + market_context helper
│   ├── llm_client.py         Gemini SDK wrapper, cost tracking, replay mode
│   ├── orchestrator.py       State machine + SessionStore + event queue
│   │                          (Gate C approval triggers hedge-ticket emission)
│   ├── persistence.py        SQLiteSessionStore (env-flag opt-in: VOL_DESK_PERSIST=1)
│   ├── intake.py             RFQ form/NL → ClientObjective (+ MI general_query)
│   ├── strategist.py         3 candidates (+ MI query_market_window pre-build)
│   ├── pricing.py            Price + Greeks (+ MI query_pricing post-engine)
│   ├── scenario.py           Outcome scenarios — uses ScenarioLibrary
│   │                          (3 default + 3 historical: 2008, COVID, flash crash)
│   ├── validator.py          Invariants (+ MI query_deal_analysis for outliers)
│   ├── narrator.py           Memo (+ Market Intelligence Citations from session.market_context)
│   ├── hedge_ticket.py       HedgeTicket builder (opening Δ/ν/Γ + listed proxies)
│   ├── lifecycle.py          LifecycleAgent — re-mark + reshape (close/roll/enhance)
│   ├── book.py               BookSession aggregator across StructuringSessions
│   ├── market_intelligence.py  Self-contained RAG layer (Chroma + sentence-transformers)
│   └── rules/strategy_rules.py   Programmatic structural rules
│
├── analysis/                 Single-shot strategist (legacy) + structurer overlays
│   ├── structurer_agent.py   StructurerReview class (legacy Quick Pricer)
│   ├── structurer_report.py  HTML structurer review (legacy)
│   ├── xva.py                FVA + bilateral CVA overlay
│   ├── vanna_volga.py        Cross-Greeks via bump-and-reprice
│   ├── vega_bucket.py        Tenor × strike vega decomposition
│   ├── bid_list.py           Synthetic dealer bid-list (sales-tool mock)
│   ├── pnl_explain.py        Greeks Taylor-expansion P&L attribution
│   └── sensitivities.py      Scenario grid + gamma ladder
│
├── config/
│   ├── loader.py             YAML config + validation (PricingConfig)
│   ├── agent_config.py       Agent layer config (model selection, per-session +
│   │                          tenant cost ceilings)
│   └── market_config.py      Market data env-var overrides
│
├── report/
│   ├── generator.py          HTML report rendering (Jinja2)
│   ├── term_sheet.py         PRIIPs-style termsheet PDF (reportlab)
│   └── kid.py                PRIIPs KID — SRI bucket, RIY, multi-horizon scenarios
│
├── backtesting/              Backtest engine + reporter (CLI flow)
├── scenarios/                Scenario engine + reporter (CLI flow)
│                              ScenarioLibrary now consumed by ScenarioAgent
├── institutional_pipeline.py Multi-config orchestration entry point
└── solver_pipeline.py        IV solver entry point

frontend/src/
├── api/
│   ├── client.ts             APIClient (pricing + market endpoints)
│   └── agentClient.ts        Agent endpoints + SSE subscription
├── components/
│   ├── Header.tsx            ArgoPilot wordmark + market-status pill + clock
│   ├── IndexTickerStrip.tsx  SPY/QQQ/IWM/DIA/VIX cards with sparklines
│   ├── MoversGrid.tsx        Gainers/Losers/Volatile, click row to prefill
│   ├── Dashboard.tsx         Page shell, mode switcher, step indicator
│   ├── ConfigForm.tsx        Pricing input form (collapsible sections)
│   ├── ReportDisplay.tsx     Backend HTML report + PayoffChart + GreeksBar
│   ├── PayoffChart.tsx       recharts AreaChart of P&L vs S_T
│   ├── GreeksBar.tsx         recharts BarChart of Δ Γ ν Θ ρ
│   └── CopilotPanel.tsx      Agent layer UI (intake, gates, memo)
├── hooks/useMarketMovers.ts  60s polling hook
├── types.ts                  Shared TS types + OPTION_TYPES dict
└── App.css                   Design tokens + layout

configs/                      YAML batch-mode configs
├── american_put_spy.yaml
├── european_put_spy.yaml
├── knockout_call_spy.yaml
├── solver_test_strike.yaml
└── test_american_put.yaml

scripts/                      Operational CLI scripts
└── seed_market_intel.py      Ingest data/market_intel_seed/*.json into Chroma

data/
└── market_intel_seed/        JSON files seeded into the MI corpus
    └── spy_sample.json       Sample equity-exotic seed (term sheets, MWs, benchmarks)

tests/                        pytest, 1217+ tests, ~25 s
├── test_market_intelligence_integration.py  Unit + integration tests for the RAG layer
└── fixtures/demo_replay.json LLM replay for offline demo

frontend/tests/               Playwright e2e
├── pricing-pipeline.spec.ts
└── vol-desk-platform.spec.ts

docs/superpowers/
├── plans/                    Implementation plans (e.g. knockout pricer)
└── specs/                    Design specs (e.g. vol-desk-platform-design)
```

## Engine routing

`src/engines/router.py` is the single point of dispatch. Each `option_type` string maps to a `(pricer_fn, greeks_fn, method_label)` tuple. QuantLib is preferred; if `import QuantLib` fails at module load, the router falls back to pure-Python implementations.

| `option_type`       | Primary engine                               | Fallback / notes                            |
|---------------------|----------------------------------------------|---------------------------------------------|
| `european_call/put` | `ql.AnalyticEuropeanEngine`                  | `black_scholes.price_european`              |
| `american_call/put` | `ql.BinomialVanillaEngine` (LR tree)         | `monte_carlo_lsm.price_american` (QL MC)    |
| `knockout_call/put` | `ql.AnalyticBarrierEngine` or FDM-LV         | `knockout.price_knockout` (Reiner-Rubinstein) |
| `knockin_call/put`  | `ql.AnalyticBarrierEngine` (DnIn/UpIn)       | KO + parity: `KI = Vanilla − KO`            |
| `asian_call/put`    | Geometric: `ql.AnalyticDiscrete/ContinuousGeometricAveragePriceAsianEngine`. Arithmetic: MC with geometric control variate | — |
| `lookback_call/put` | `ql.AnalyticContinuousFixed/FloatingLookbackEngine` | — |
| `digital_call/put`  | Black-Scholes cash-or-nothing closed-form (`digitals.price_digital_cash`) | Greeks via bump-and-reprice closed-form for stability near strike |
| `phoenix_autocall`  | `autocallable.price_phoenix_autocallable` over `multi_asset_mc.simulate_correlated_gbm` | Worst-of basket; observation schedule + autocall/coupon/protection barriers via kwargs |
| `worst_of_call/put` | `multi_asset_mc.price_worst_of_european_put` (put) / Cholesky GBM + worst-of payoff (call) | Single-asset path collapses to vanilla |
| `variance_swap`     | `variance_swap.fair_strike_from_strip` (Carr-Madan) | Returned "price" is the fair vol strike, not USD |

### Barrier direction & kind

`quantlib_engine.price_knockout_ql` takes `barrier_kind ∈ {"out", "in"}`. Direction (Down vs Up) is **inferred from B vs S**, not user-specified — barrier below spot → Down, barrier above spot → Up. The mapping table:

```python
_BARRIER_TYPE_MAP = {
    ("out", True):  ql.Barrier.DownOut,
    ("out", False): ql.Barrier.UpOut,
    ("in",  True):  ql.Barrier.DownIn,
    ("in",  False): ql.Barrier.UpIn,
}
```

The no-arb parity `KO + KI = Vanilla` holds to machine precision and is verified in `tests/test_knockin.py`.

### Discrete-monitoring (BGK shift)

`knockout.bgk_adjusted_barrier` shifts the barrier before any continuous-formula engine sees it: `B_adj = B · exp(±0.5826 · σ · √Δt)`. Applied unconditionally when `monitoring ≠ "continuous"` (daily/weekly/monthly or numeric Δt). Without this, the engine systematically over-estimates knock-out probability.

### Smile-aware pricing

When `use_vol_surface=True`:
1. Handler fetches option chain → `iv_grid` → `BlackVarianceSurface`.
2. Surface is passed to engines as a `vol_handle`.
3. For barriers (KO and KI), the handler also forces `use_local_vol_pde=True` because `AnalyticBarrierEngine` collapses any vol surface to a single scalar — under skew it mis-prices the knock-probability term. The FDM engine consumes Dupire local vol derived from the supplied surface.
4. Greeks against a surface use the FD-with-LV path for **price** but bump-reprice against a flat-σ analytic engine for **delta/gamma/vega/theta/rho** (FD discretisation noise on small bumps dominates the signal). The price/Greek labelling reflects this split.

### Asian options

`src/engines/asian.py`:
- **Geometric average price**: closed-form via the QuantLib geometric Asian engines (continuous and discrete). Match BS exactly under no-skew.
- **Arithmetic average price**: Monte Carlo with the geometric Asian as control variate. Variance reduction is dramatic (~50× for typical maturities) — the control collapses most of the path-dependent variance.
- `averaging_method ∈ {"geometric", "arithmetic"}`, `averaging_frequency ∈ {"daily", "weekly", "monthly"}`.

### Lookback options

`src/engines/lookback.py`:
- **Fixed-strike**: payoff `max(S_max − K, 0)` for call (continuous monitoring).
- **Floating-strike**: payoff `S_T − S_min` for call. Caller passes K as the running extremum (K=S for a fresh option).
- Both use the QuantLib analytic continuous engines (Conze-Viswanathan / Goldman-Sosin-Gatto).

## Greeks conventions

Uniform across the pipeline:

| Greek  | Convention                          | Sign for long ATM put |
|--------|-------------------------------------|-----------------------|
| Delta  | per $1 spot move                    | negative              |
| Gamma  | per $1 spot move (second deriv)     | positive              |
| Vega   | **per 1% absolute σ** (i.e. /100)   | positive              |
| Theta  | **per calendar day**, ∂V/∂t (forward) | negative            |
| Rho    | **per 1% absolute r** (i.e. /100)   | negative              |

Theta convention matches QuantLib (`option.theta() / 365`). The MC engine's `greeks_american` uses the same per-day forward-difference formula.

## Monte Carlo

`src/engines/monte_carlo_lsm.py` was originally a hand-rolled NumPy LSM. It now wraps `ql.MCAmericanEngine` (Longstaff-Schwartz, cubic monomial basis, antithetic optional, fixed seed for common-random-numbers across bump-reprice Greeks).

**Live status**: in production, MC is reached *only* if `import QuantLib` fails (the same module now requires QL itself, so the fallback is logically dead). The frontend's `n_paths` / `variance_reduction` controls are sent in every request but ignored by the engine that actually runs (binomial tree). To genuinely expose MC, add an `engine` field to `PricingRequest`.

## ArgoPilot market intelligence layer

`src/data/movers.py` exposes `get_movers_payload(universe="default")` consumed by `GET /api/market/movers`:

```json
{
  "as_of": "2026-04-29T15:30:00Z",
  "indices": [{"ticker": "SPY", "price": 711.69, "change_pct": 0.42, "spark": [...]}, ...],
  "gainers":  [{"ticker": "NVDA", "price": ..., "change_pct": ..., "hv30": ...}, ...],
  "losers":   [...],
  "volatile": [...]
}
```

- **Universe (default, ~28 tickers)**: indices `SPY QQQ IWM DIA VIX`, Mag 7, sector SPDRs, high-beta names.
- **Implementation**: batch fetch via `yfinance.Tickers(...)`; HV30 from log returns of last 30 closes.
- **Cache**: 60s server-side TTL. Stale data is served if a refresh fails (with a `source: "cache"` marker the UI surfaces as a "stale" badge).
- **Top-K**: 10 per category; indices section returns all 5 always.
- Frontend polls every 60s via `useMarketMovers`. Clicking a row dispatches `onPickTicker(symbol, price)` which prefills `ConfigForm` and scrolls to it.

## Multi-agent structuring co-pilot

A planner-and-specialists architecture (`src/agents/`) on top of the engines. State flows through a single Pydantic `StructuringSession`. Agents never call each other directly — the orchestrator mediates every step.

```
Intake → [Gate A] → BuildRegime → Strategist → [Gate B] →
   Pricing → Scenario → Validator → Narrator → [Gate C] → DONE
```

| Agent | File | Role |
|---|---|---|
| Intake | `intake.py` | RFQ (form or NL) → typed `ClientObjective` |
| Strategist | `strategist.py` | `ClientObjective + MarketRegime` → 3 candidate structures |
| Pricing | `pricing.py` | Each candidate's legs → price + Greeks via engines |
| Scenario | `scenario.py` | Each priced candidate → client-outcome scenarios + hedgeability |
| Validator | `validator.py` | No-arb / parity / structural invariants |
| Narrator | `narrator.py` | 3-way comparison memo + recommendation |
| Orchestrator | `orchestrator.py` | State machine, gates A/B/C, audit trail, SSE event queue |

- **LLM**: Gemini SDK (`llm_client.py`). Two tiers: smart (Strategist, Narrator) and fast (Intake, Validator, Scenario commentary). Per-agent overrides via env vars.
- **HITL gates**: Gate A (after Intake), Gate B (after Strategist), Gate C (after Narrator). The API stream closes after each gate event so the client re-subscribes after deciding.
- **Replay mode**: `DEMO_REPLAY=1` replays canned LLM responses from `tests/fixtures/demo_replay.json` for offline demos.
- **Cost ceiling**: `AGENT_COST_CEILING_USD` (default 0.50) kills runaway sessions.
- **Storage**: in-memory `SessionStore` (RLock-guarded). Phase 6 will swap for SQLite.

## Market-intelligence (RAG) layer

`src/agents/market_intelligence.py` is a self-contained retrieval-augmented-generation layer that **sits alongside, not inside, the pricing engines**. The engines compute model prices; the RAG layer supplies the market context that turns those numbers into a defensible quote. Sources are dealer commentary, comparable term sheets, and pricing benchmarks ingested as JSON into a local Chroma store with sentence-transformers embeddings. The same Gemini client the agents already use is wrapped via `existing_llm_adapter` — no second SDK instance, full cost tracking, full DEMO_REPLAY support.

The layer is gated by `MARKET_INTEL_ENABLED` (default `1`). When off, agents fall back to pure-QuantLib behaviour with no MI calls, no citations in the memo, and no errors — useful for environments without `chromadb` / `sentence-transformers` installed.

```
                                ┌──────────────────────────┐
                                │ src/engines/             │
                                │ (QuantLib pricers)       │
                                │   • Black-Scholes        │
                                │   • KO/KI barriers       │
                                │   • Asian / Lookback     │
                                │   • Monte Carlo LSM      │
                                └────────▲─────────────────┘
                                         │ price + Greeks (deterministic)
                                         │
   ┌────────────────────────┐        ┌───┴────────────────────────────┐
   │ src/agents/             │ run() │  Intake → Strategist → Pricing │
   │ orchestrator.py         │──────▶│   → Scenario → Validator       │
   │ (state machine,         │       │   → Narrator                   │
   │  HITL gates A/B/C)      │       └───────┬────────────────────────┘
   └────┬───────────────────┘                │ each agent appends a
        │                                    │ QueryResponse to
        │ emits market_context SSE event     │ session.market_context
        │ on every new entry                 │
        ▼                                    ▼
   ┌────────────────────────┐        ┌───────────────────────────────────┐
   │ /api/agent/.../events  │        │ src/agents/market_intelligence.py │
   │   (SSE stream)         │        │   • ChromaVectorStore             │
   └────────────────────────┘        │   • EmbeddingsManager             │
                                     │   • RetrievalEngine               │
                                     │   • PromptManager                 │
                                     │   • MarketIntelligence (facade)   │
                                     │       .general_query              │
                                     │       .query_market_window        │
                                     │       .query_pricing              │
                                     │       .query_deal_analysis        │
                                     └────────────▲──────────────────────┘
                                                  │
                                                  │ same Gemini client via
                                                  │ existing_llm_adapter
                                                  │
                                     ┌────────────┴──────────────────────┐
                                     │ src/agents/llm_client.py          │
                                     │   (cost, retries, DEMO_REPLAY)    │
                                     └───────────────────────────────────┘
```

| Agent | Call site | Method |
|---|---|---|
| Intake | After parsing the RFQ | `general_query(query=<rfq+ticker>, asset_class=ticker)` — surface relevant corpus context at Gate A |
| Strategist | Before building candidates | `query_market_window(asset_class=ticker)` — if the answer contains "CLOSED", **soften** all candidate rationales with a window-warning prefix (don't refuse — keep 3 candidates) |
| Pricing | After each QuantLib pricing call | `query_pricing(asset_class, tranche_type=structure_kind, deal_size)` — overlay market spread context on the model price |
| Scenario | Once per session | `general_query(<worst-shock question>, asset_class=ticker)` — ground stress narratives in real history |
| Validator | Per priced candidate | `query_deal_analysis(deal_summary, asset_class)` — surface "no precedent" / "outlier" answers as **WARN** findings (HITL via Gate C) |
| Narrator | Reads `session.market_context` only | No new query — appends a "Market Intelligence Citations" section to the memo (Markdown + HTML) |

**State plumbing.** Every MI call appends `{"agent": ..., "intent": ..., **QueryResponse.to_dict()}` to `StructuringSession.market_context`. The orchestrator wraps every `agent.run()` call in `_run_agent`, diffs `len(session.market_context)` before and after, and emits a `market_context` SSE event for each new entry. The Narrator stitches the same list into the final memo as citations — so what the user sees in real time on the SSE stream is what the memo's bibliography ends up containing.

**Initialisation.** A FastAPI `startup` event in `src/api/main.py` resolves `get_market_intelligence()` once at process start and exposes the singleton on `app.state.market_intel`. The `OrchestratorAgent` constructor lazily resolves the same singleton via `_resolve_market_intel()` (or accepts an explicit injection — used by tests). Agents receive the reference at construction; `mi=None` cleanly disables every MI call.

**Seeding.** `python scripts/seed_market_intel.py` reads `data/market_intel_seed/*.json` (each file a list of `{id, content, doc_type, asset_class, ...}` dicts) and persists them into Chroma at `MARKET_INTEL_PERSIST_DIR` (default `./data/market_intel`). Seed quality gates the layer's value — under ~50 relevant documents, the Validator's outlier check produces noisy false positives. Recommended: ~50+ equity-exotic term sheets and dated dealer vol commentary before exposing the citations to end users.

**Configuration** (env vars):
- `MARKET_INTEL_ENABLED` — master flag (default `1`).
- `MARKET_INTEL_PERSIST_DIR` — Chroma persist dir (default `./data/market_intel`).
- `MARKET_INTEL_COLLECTION` — collection name (default `market-intelligence`).
- `MARKET_INTEL_EMBEDDINGS_MODEL` — sentence-transformers model (default `sentence-transformers/all-MiniLM-L6-v2`).
- `AGENT_MODEL_MARKET_INTEL` — LLM tier override; defaults to fast tier (`gemini-2.5-flash`).

## Frontend wiring

`frontend/src/api/client.ts` defaults to `${protocol}//${hostname}:8002` (override with `VITE_API_URL`). The form auto-fetches market data on mount:

```
ConfigForm.tsx mount
  → GET /api/market/spot-price
  → GET /api/market/dividend-yield
  → GET /api/market/historical-volatility
  → GET /api/market/risk-free-rate
```

On submit:
```
POST /api/price → PricingResult
  → ReportDisplay renders the inline HTML report
  → PayoffChart shows P&L vs S_T (computed client-side)
  → GreeksBar shows Δ Γ ν Θ ρ
  → user can download HTML or PDF
```

The `option_type` dropdown lists all 12 product flavours: 2× exercise (American/European) × 2× side (call/put) + 4× barrier (KO/KI × call/put) + 2× Asian + 2× lookback. Barrier options surface a `barrier_level` field; direction is inferred from B vs S server-side. Asian surfaces `averaging_method` + `averaging_frequency`. Lookback surfaces `lookback_type`.

## Configuration

Two equivalent input formats:

- **YAML config** (`configs/*.yaml`) for batch runs via `python main.py --config <path>` — validated by `src/config/loader.py:PricingConfig`.
- **REST request** (`PricingRequest` Pydantic model) for the UI — converted to `PricingConfig` in `handlers._request_to_config`.

Both paths converge at `router.route()`.

Environment variables (see `.env.example`):
- `GEMINI_API_KEY` — required for the agent layer
- `FRED_API_KEY`, `POLYGON_API_KEY` — optional upgrades for regime/surface
- `AGENT_MODEL_SMART`, `AGENT_MODEL_FAST` — model tier selection
- `MARKET_DATA_CACHE_TTL`, `MARKET_DATA_TIMEOUT`, `MARKET_DATA_MAX_RETRIES`, `MARKET_DATA_RETRY_BACKOFF` — yfinance tuning

## Testing

`pytest tests/` — **1444** tests, ~90s. Coverage layers:

- **Engine correctness**: `test_quantlib_correctness.py`, `test_smile_pricing.py`, `test_knockin.py` (KO+KI=Vanilla parity), `test_combinations.py` (all 12 option types via router), `test_asian.py`, `test_lookback.py`, `test_multi_underlier_exotics.py`, `test_autocallable.py`, `test_multi_asset_mc.py`.
- **Conventions**: `test_mc_theta.py` (per-day, sign), `test_rate_conventions.py`, `test_dividend_normaliser.py`, `test_evaluation_date.py`.
- **Numerical methods**: `test_fdm_greeks.py`, `test_mc_antithetic.py`, `test_step_count_policy.py`, `test_bgk_shift.py`, `test_discrete_monitoring.py`, `test_discrete_dividend.py`, `test_adaptive_bump.py`.
- **Solver / IV**: `test_solver_iv_default.py`, `test_iv_grid.py`, `test_vol_surface.py`, `test_option_chain.py`.
- **Engine consistency**: `test_engine_consistency.py`, `test_engines.py`, `test_engine_selection.py`.
- **Pipeline**: `test_pipeline_with_structurer.py`, `test_market_data.py`.
- **Agents**: `test_agents_smoke.py` (full 7-agent flow with replay fixtures), `test_session_persistence.py`, `test_basket_state.py`, `test_copilot_scenarios.py`.
- **Movers**: `test_movers.py`, `test_movers_endpoint.py`.
- **Phase-7 senior-structurer**: `test_phase7_engines.py` (26 unit tests covering digitals, var swap, RC, vega buckets, vanna/volga, hedge tickets, KID, lifecycle, book, implied correlation, dividend curve, discounting, XVA), `test_phase7_stress.py` (58 stress tests covering edge regimes, monotonicity invariants, end-to-end hedge-ticket emission, FastAPI endpoint round-trips, concurrency, random seed sweeps).

E2E: `frontend/tests/pricing-pipeline.spec.ts`, `frontend/tests/vol-desk-platform.spec.ts` (Playwright).

Engine priorities: parity / no-arb identities first (cross-checks engines against themselves), then closed-form references where available (Black-Scholes for European, Reiner-Rubinstein for KO, geometric Asian for arithmetic-via-CV).

## Senior-structurer enrichment layer (Phase 7)

The pipeline above produces a model price + Greeks. A trader cannot quote off mid alone — there's no carry, no funding charge, no capital, no hedge plan, no termsheet. The Phase-7 enrichment layer adds the artefacts that turn a price into a tradeable quote and a defensible memo.

### Per-quote enrichments (attached to `PricingResult`)

```
PricingResult
├── price                    Model mid (existing)
├── greeks                   {delta, gamma, vega, theta, rho} (existing)
│
├── xva_overlay              FVA + bilateral CVA + DVA + ask/bid prices
│                              (src/analysis/xva.py)
│       FVA = funding_spread_bps · EPE_avg · T
│       CVA = LGD · EPE_avg · (1 − exp(−λ_cp · T))     [λ_cp from CDS]
│       Always 0 when the trade is CSA-protected
│
├── quote_bid / mid / offer  Derived from mid + xva.total_xva
├── quote_spread_bps         Bid-offer spread in bps of mid (or strike for var swaps)
│
├── vanna                    ∂²V/∂S∂σ — auto-on for KO/KI; bump-and-reprice
├── volga                    ∂²V/∂σ² — auto-on for KO/KI
├── vega_buckets             Tenor × strike sensitivity grid (analysis/vega_bucket.py)
│
└── surface_age_seconds      Set when use_vol_surface=True; UI surfaces "stale" badge > 60s
```

### Agent-pipeline enrichments

```
StructuringSession (after Gate C approval)
├── memo                     3-way comparison memo (existing)
├── hedge_tickets            One per priced candidate, emitted at Gate C
│                              (src/agents/hedge_ticket.py)
│       opening_delta_shares          Shares to short/long against position
│       opening_vega_per_pct          $ vega per 1% σ
│       gamma_rebal_budget_per_day    0.5 · |Γ| · (σS)² · dt
│       rebalance_frequency           daily | weekly | on-event (heuristic on |Γ|·S²)
│       listed_proxies                Listed-strike approximations of OTC vega tilt
│
└── (per-candidate)
    ├── xva                  Same XVAOverlayState as PricingResult
    └── quote                BidOfferQuote
```

### Standalone enrichment endpoints

| Endpoint | Output | Module |
|---|---|---|
| `GET /api/agent/sessions/{id}/termsheet` | reportlab PRIIPs-style PDF | `src/report/term_sheet.py` |
| `GET /api/agent/sessions/{id}/kid` | JSON KID (SRI bucket, RIY cost table, multi-horizon scenarios) | `src/report/kid.py` |
| `GET /api/agent/sessions/{id}/hedge_tickets` | List of HedgeTickets | `src/agents/hedge_ticket.py` |
| `POST /api/agent/sessions/{id}/lifecycle` | Re-mark prior trade vs. supplied current regime; emits `close` / `roll` / `enhance` reshape options + Greek-attribution P&L | `src/agents/lifecycle.py` (uses `analysis/pnl_explain.py`) |
| `GET /api/agent/book` | Book-level aggregated Greeks across all stored sessions, by underlier | `src/agents/book.py` |

### Curve refactor groundwork

- **`DiscountingContext`** (`src/data/discounting.py`) — pair of curves: discount + projection. `flat(rate)` collapses both to the same `FlatRateCurve` for back-compat. v2 plugs an OIS bootstrap.
- **`DividendCurve`** (`src/data/dividend_curve.py`) — yield-vs-maturity forecast. `flat(q)` for back-compat; `decay(q_today, decay_per_year)` for a linear-decay forecast curve. v2 plugs CME dividend futures.
- **`implied_correlation`** (`src/data/correlation.py`) — Bakshi-Kapadia-Madan equicorrelation solver from index σ vs. component σs and weights.

These curves are *additions* to `src/data/` — they do not replace the scalar `r` and `q` paths through the existing engines. Wiring them deeper into the pricing pipeline is a v2 follow-up.

### Cost-ceiling guards

Per-session ceiling (`AGENT_COST_CEILING_USD`, default 0.50) was already in place. Phase 7 added a per-process tenant ceiling (`AGENT_TENANT_COST_CEILING_USD`, default 0.0 = disabled) checked against `sum(session.total_cost_usd for all sessions in store)` — a runaway agent on one client cannot exhaust the desk's budget.

### Test coverage for the enrichment layer

- `tests/test_phase7_engines.py` — 26 unit/smoke tests for the new modules.
- `tests/test_phase7_stress.py` — 58 stress tests: edge regimes (T→0, σ→0), monotonicity invariants (XVA scaling with T and funding spread, bid ≤ mid ≤ ask), multi-asset basket robustness, variance-swap convergence with grid width, end-to-end hedge-ticket emission via OrchestratorAgent.decide_gate, FastAPI endpoint round-trips via TestClient, concurrency (8-thread router calls), random seed sweeps for phoenix + worst-of, KID PRIIPs SRI matrix correctness for IG and sub-IG counterparties.

## Known caveats

1. **MC is not user-selectable on the legacy path**. The frontend MC controls are inert in the original Quick Pricer flow; `route_with_engine(engine="mc")` is wired correctly in the router but isn't surfaced through the UI yet.
2. **American + barrier is not supported**. The router has no `american_knockout_*`. The QL `AnalyticBarrierEngine` is European-exercise only; American barriers would need a tree or PDE engine wired in.
3. **Theta beyond 1 day** is not exposed — only the standard per-day decay.
4. **Greeks for barriers near the barrier** use a barrier-distance-aware bump step (`greeks_knockout_ql:604-609`) to avoid pin-risk noise; this is intentional but means delta/gamma reported very close to the barrier are smoothed.
5. **MC fallback module requires QuantLib** (post-swap), so the `if not QUANTLIB_AVAILABLE` branch in `router.py` is logically unreachable in MC paths. Cleaning this up is a follow-up.
6. **Agent SessionStore defaults to in-memory** — set `VOL_DESK_PERSIST=1` to use the SQLite-backed store at `vol_desk_sessions.db` (configurable via `VOL_DESK_DB_PATH`). Production deployments should set this; the in-memory default keeps the existing test suite stable.
7. **`backend/`** at repo root is an empty placeholder (an early-design artifact); the real backend lives under `src/api/`.
8. **Reverse convertible engine** (`src/engines/reverse_convertible.py`) ships but is not router-wired. The vanilla pricer signature doesn't carry the structural inputs (coupon rate, par-pricing solver) that an RC needs.
9. **FX (`src/data/fx.py`) and Heston (`src/engines/heston.py`)** are skeletons. FX vanilla via Garman-Kohlhagen works; FX vol-surface conventions (delta-strike, premium-included delta, NY/Tokyo cuts) are v2. Heston calibration via QL works; multi-tenor SLV hybrid for proper barrier pricing under skew is v2.
10. **Vega-bucket grid is approximate**. Phase 7 places the scalar vega in the (T, K) cell closest to the product's actual expiry and ATM; surrounding cells are 0. A real (T, K)-localised vol-surface bump is v2.
11. **Strategist rule table (`src/agents/rules/strategy_rules.py`) hasn't been extended with the new multi-asset structures**. The rule pattern uses single-asset `Leg` objects, while phoenix/worst-of need multi-asset `Structure` objects. Surfacing autocallables and worst-of through the structuring co-pilot's Gate B candidate selection is a v2 effort.
12. **Multi-curve discounting + dividend curve are skeletons in `src/data/`** — they do not yet replace the scalar `r` and `q` paths through the existing engines. Front-end of the wiring is in place; the engine-layer plumbing is v2.
