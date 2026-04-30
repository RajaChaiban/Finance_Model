# Architecture — Vol Desk Platform

A FastAPI + React derivatives pricing platform with QuantLib as the primary numerical engine. Four layers stack on top of the same engines: a market-intelligence layer (live indices and movers), the Quick Pricer (8 product types via REST), a 7-agent structuring co-pilot with human-in-the-loop gates, and a RAG-based market-intelligence (MI) corpus that grounds the agents' reasoning in dealer commentary, comparable term sheets, and pricing benchmarks.

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
    │       ├── report.generator   → HTML report
    │       └── analysis.structurer_agent → strategist commentary
    │
    ├── /api/market/*   spot, dividend, rate, hist-vol, dividend-info, movers
    │       (Yahoo Finance via market_data.py + movers.py)
    │
    └── /api/agent/*    multi-agent structuring co-pilot
            (src/api/agent_router.py → src/agents/orchestrator.py)
```

## Repository layout

```
src/
├── api/                      FastAPI layer
│   ├── main.py               App + CORS + market data + movers endpoints
│   ├── handlers.py           price_option orchestrator
│   ├── models.py             Pydantic request/response schemas (PricingRequest)
│   ├── agent_router.py       /api/agent/* routes (sessions, gates, SSE)
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
│   └── solver.py             Implied-vol Brent / Newton solver
│
├── data/                     Vol-surface construction + market data
│   ├── iv_grid.py            Invert option-chain quotes → IV grid
│   ├── vol_surface.py        Build ql.BlackVarianceSurface from grid
│   ├── rate_conventions.py   Continuous/discrete rate conversions
│   ├── market_data.py        yfinance adapters with retry/cache
│   └── movers.py             Movers payload (gainers/losers/volatile + indices)
│
├── agents/                   Multi-agent structuring co-pilot
│   ├── state.py              Pydantic StructuringSession + all state types
│   ├── base.py               Common base + AgentError + market_context helper
│   ├── llm_client.py         Gemini SDK wrapper, cost tracking, replay mode
│   ├── orchestrator.py       State machine + SessionStore + event queue
│   ├── intake.py             RFQ form/NL → ClientObjective (+ MI general_query)
│   ├── strategist.py         3 candidates (+ MI query_market_window pre-build)
│   ├── pricing.py            Price + Greeks (+ MI query_pricing post-engine)
│   ├── scenario.py           Outcome scenarios (+ MI general_query for history)
│   ├── validator.py          Invariants (+ MI query_deal_analysis for outliers)
│   ├── narrator.py           Memo (+ Market Intelligence Citations from session.market_context)
│   ├── market_intelligence.py  Self-contained RAG layer (Chroma + sentence-transformers)
│   └── rules/strategy_rules.py   Programmatic structural rules
│
├── analysis/                 Single-shot strategist (legacy, used by Quick Pricer)
│   ├── structurer_agent.py   StructurerReview class
│   └── structurer_report.py  HTML structurer review
│
├── config/
│   ├── loader.py             YAML config + validation (PricingConfig)
│   ├── agent_config.py       Agent layer config (model selection, cost ceiling)
│   └── market_config.py      Market data env-var overrides
│
├── report/generator.py       HTML report rendering (Jinja2)
├── backtesting/              Backtest engine + reporter (CLI flow)
├── scenarios/                Scenario engine + reporter (CLI flow)
├── institutional_pipeline.py Multi-config orchestration entry point
└── solver_pipeline.py        IV solver entry point

frontend/src/
├── api/
│   ├── client.ts             APIClient (pricing + market endpoints)
│   └── agentClient.ts        Agent endpoints + SSE subscription
├── components/
│   ├── Header.tsx            Vol Desk wordmark + market-status pill + clock
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

| `option_type`       | Primary engine                               | Fallback                                    |
|---------------------|----------------------------------------------|---------------------------------------------|
| `european_call/put` | `ql.AnalyticEuropeanEngine`                  | `black_scholes.price_european`              |
| `american_call/put` | `ql.BinomialVanillaEngine` (LR tree)         | `monte_carlo_lsm.price_american` (QL MC)    |
| `knockout_call/put` | `ql.AnalyticBarrierEngine` or FDM-LV         | `knockout.price_knockout` (Reiner-Rubinstein) |
| `knockin_call/put`  | `ql.AnalyticBarrierEngine` (DnIn/UpIn)       | KO + parity: `KI = Vanilla − KO`            |
| `asian_call/put`    | Geometric: `ql.AnalyticDiscrete/ContinuousGeometricAveragePriceAsianEngine`. Arithmetic: MC with geometric control variate | — |
| `lookback_call/put` | `ql.AnalyticContinuousFixed/FloatingLookbackEngine` | — |

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

## Vol Desk market intelligence layer

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

`pytest tests/` — **1202** tests, ~25s. Coverage layers:

- **Engine correctness**: `test_quantlib_correctness.py`, `test_smile_pricing.py`, `test_knockin.py` (KO+KI=Vanilla parity), `test_combinations.py` (all 12 option types via router), `test_asian.py`, `test_lookback.py`, `test_multi_underlier_exotics.py`.
- **Conventions**: `test_mc_theta.py` (per-day, sign), `test_rate_conventions.py`, `test_dividend_normaliser.py`, `test_evaluation_date.py`.
- **Numerical methods**: `test_fdm_greeks.py`, `test_mc_antithetic.py`, `test_step_count_policy.py`, `test_bgk_shift.py`, `test_discrete_monitoring.py`, `test_discrete_dividend.py`, `test_adaptive_bump.py`.
- **Solver / IV**: `test_solver_iv_default.py`, `test_iv_grid.py`, `test_vol_surface.py`, `test_option_chain.py`.
- **Engine consistency**: `test_engine_consistency.py`, `test_engines.py`.
- **Pipeline**: `test_pipeline_with_structurer.py`, `test_market_data.py`.
- **Agents**: `test_agents_smoke.py` (full 7-agent flow with replay fixtures).
- **Movers**: `test_movers.py`, `test_movers_endpoint.py`.

E2E: `frontend/tests/pricing-pipeline.spec.ts`, `frontend/tests/vol-desk-platform.spec.ts` (Playwright).

Engine priorities: parity / no-arb identities first (cross-checks engines against themselves), then closed-form references where available (Black-Scholes for European, Reiner-Rubinstein for KO, geometric Asian for arithmetic-via-CV).

## Known caveats

1. **MC is not user-selectable**. The frontend MC controls are inert in production. Fix: add `engine` to `PricingRequest`.
2. **American + barrier is not supported**. The router has no `american_knockout_*`. The QL `AnalyticBarrierEngine` is European-exercise only; American barriers would need a tree or PDE engine wired in.
3. **Theta beyond 1 day** is not exposed — only the standard per-day decay.
4. **Greeks for barriers near the barrier** use a barrier-distance-aware bump step (`greeks_knockout_ql:604-609`) to avoid pin-risk noise; this is intentional but means delta/gamma reported very close to the barrier are smoothed.
5. **MC fallback module requires QuantLib** (post-swap), so the `if not QUANTLIB_AVAILABLE` branch in `router.py` is logically unreachable in MC paths. Cleaning this up is a follow-up.
6. **Agent SessionStore is in-memory only** — sessions are lost on restart. Phase 6 swaps for SQLite.
7. **`backend/`** at repo root is an empty placeholder (a Vol Desk-era artifact); the real backend lives under `src/api/`.
