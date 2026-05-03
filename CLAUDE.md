# CLAUDE.md — Repo guide for AI agents

This file is loaded automatically by Claude Code at session start. **If the user has not told you where to look, start here.** Do not blindly grep the whole repo or assume layout from training data — read this map first, then `architecture.md`, then jump to the right directory.

## What this repo is

A **FastAPI + React derivatives pricing platform** ("Vol Desk") with three layers on top of the same QuantLib-backed engines:

1. **Quick Pricer** — REST `POST /api/price` for 12 option types (Eu/Am × C/P, KO/KI × C/P, Asian × 2, Lookback × 2)
2. **Vol Desk market intelligence** — live indices, top movers, click-to-prefill
3. **Structuring Co-pilot** — 7-agent (Gemini-driven) workflow with HITL gates

Backend port **8002**, frontend port **5173**. **1286 backend tests.**

## Where to look first

When asked about a topic, check these locations **in this order** before searching elsewhere:

| Topic | First read | Then |
|---|---|---|
| **Overall architecture** | `architecture.md` | this file |
| **Setup, install, env vars** | `SETUP.md`, `.env.example` | `requirements.txt`, `frontend/package.json` |
| **Pricing engines / option types** | `src/engines/router.py` (the dispatch table) | `src/engines/{black_scholes,knockout,asian,lookback,quantlib_engine,monte_carlo_lsm}.py` |
| **REST API endpoints** | `src/api/main.py` (all `/api/market/*` + `POST /api/price`) | `src/api/handlers.py`, `src/api/agent_router.py` |
| **Request/response shapes** | `src/api/models.py` (`PricingRequest`, `PricingResult`) | `src/api/agent_models.py` |
| **Vol surface / smile pricing** | `src/data/{iv_grid,vol_surface}.py` | `architecture.md` "Smile-aware pricing" |
| **Market data (yfinance)** | `src/data/market_data.py`, `src/api/market_data.py` | `src/data/movers.py` for the dashboard payload |
| **Multi-agent co-pilot** | `src/agents/__init__.py` (state types), `src/agents/orchestrator.py` | individual agents in `src/agents/{intake,strategist,pricing,scenario,validator,narrator}.py` |
| **Frontend pages** | `frontend/src/components/Dashboard.tsx` (page shell) | sibling components in `frontend/src/components/` |
| **Frontend API client** | `frontend/src/api/client.ts`, `frontend/src/api/agentClient.ts` | `frontend/src/types.ts` for shared types |
| **Frontend styling** | `frontend/src/App.css` (single file, design tokens at top) | — |
| **CLI batch pricing** | `main.py` (root) | `configs/*.yaml`, `src/config/loader.py` |
| **HTML reports** | `src/report/generator.py` | output goes to `reports/` (gitignored) |
| **Tests (backend)** | `tests/` (one file per topic, e.g. `test_asian.py`, `test_knockin.py`) | `tests/fixtures/demo_replay.json` for agent replay |
| **Tests (e2e)** | `frontend/tests/pricing-pipeline.spec.ts`, `frontend/tests/vol-desk-platform.spec.ts` | `frontend/playwright.config.ts` |
| **Design specs / plans** | `docs/superpowers/specs/`, `docs/superpowers/plans/` | — |

## Map of the repo

```
Finance_Model/
├── README.md                  Top-level overview (start here for users)
├── architecture.md            Full pipeline diagram, engine routing, conventions
├── CLAUDE.md                  This file (search guidance for AI agents)
├── SETUP.md                   Install + env vars + troubleshooting
├── QUANTLIB_INTEGRATION.md    QL migration history
├── SOLVER_COMPONENT.md        IV solver internals
├── main.py                    CLI entry point (batch pricing from YAML)
├── requirements.txt           Python deps (numpy, scipy, yfinance, QuantLib, gemini, fastapi)
├── conftest.py                pytest fixtures
├── .env.example               Env vars template (copy to .env)
│
├── src/                       ALL Python source code lives here
│   ├── api/                   FastAPI app (main.py is the app, NOT the root main.py)
│   ├── engines/               Pricing engines + router
│   ├── data/                  Market data + IV grid + vol surface + movers
│   ├── agents/                7-agent structuring co-pilot
│   ├── analysis/              Single-shot strategist (legacy, used by Quick Pricer report)
│   ├── config/                Config loaders (PricingConfig, agent_config)
│   ├── report/                HTML report generator
│   ├── backtesting/           Backtest engine + reporter (CLI)
│   └── scenarios/             Scenario engine + reporter (CLI)
│
├── frontend/                  React 19 + Vite 8 + TS app
│   ├── src/
│   │   ├── api/               client.ts (pricing) + agentClient.ts (co-pilot SSE)
│   │   ├── components/        Header, IndexTickerStrip, MoversGrid, Dashboard,
│   │   │                       ConfigForm, ReportDisplay, PayoffChart, GreeksBar,
│   │   │                       CopilotPanel
│   │   ├── hooks/             useMarketMovers (60s poll)
│   │   ├── types.ts           Shared TS types + OPTION_TYPES dict
│   │   └── App.css            Single-file styling (design tokens at top)
│   ├── tests/                 Playwright e2e specs
│   ├── package.json           recharts 3, react 19, vite 8, playwright
│   └── playwright.config.ts
│
├── tests/                     pytest backend tests (~1286)
│   └── fixtures/demo_replay.json   Canned LLM responses for agent tests
│
├── configs/                   YAML configs for batch CLI runs
├── docs/superpowers/          Plans + specs (design docs)
├── examples/                  Smoke tests + smile demo
├── reports/                   Generated HTML reports (gitignored)
├── raja_notes/                Local-only learning notes (gitignored)
└── backend/                   EMPTY — placeholder dir, ignore (real backend is src/api/)
```

## Key invariants and gotchas

Before changing code, know these:

- **Backend port is 8002**, not 8003. Both `src/api/main.py:222` and `frontend/src/api/client.ts:9` agree. Some older docs may say 8003 — they're wrong.
- **Router is the single dispatch point.** Every option_type → engine mapping lives in `src/engines/router.py`. Adding a product means adding a row there + an engine module.
- **Direction (Down/Up) for barriers is inferred from B vs S**, not user-specified. See `architecture.md` "Barrier direction & kind".
- **KO + KI = Vanilla parity** is verified to machine precision in `test_knockin.py`. Don't break this.
- **Vega is per 1% absolute σ, Theta is per calendar day, Rho is per 1% absolute r.** Tests enforce this.
- **Smile-aware barriers force FDM-with-local-vol.** The analytic engine collapses the smile to a scalar and mis-prices. See `handlers.py:103-104`.
- **Agents never call each other directly.** The orchestrator mediates every step. Adding agent logic means a new method on the orchestrator + a new state transition.
- **MC fallback path is logically dead** (the wrapper now requires QL itself). Don't rely on it.
- **`backend/` at repo root is empty.** The real backend is `src/api/`.
- **`raja_notes/`, `.env`, `reports/`, `e2e-*-result.png` are gitignored.** Don't add them.
- **`README_structured_pricing.md`** appears to be from an unrelated project (StructuredFinance.AI / vector embeddings). Treat as orphaned; don't reference.

## How to run things

```bash
# Backend
python -m uvicorn src.api.main:app --reload --port 8002

# Frontend
cd frontend && npm run dev   # → http://localhost:5173

# Backend tests
python -m pytest tests/

# Single test file
python -m pytest tests/test_asian.py -v

# Frontend e2e
cd frontend && npx playwright test

# Frontend type-check + bundle (use this as the "did I break the frontend?" gate)
cd frontend && npm run build

# CLI batch pricing
python main.py --config configs/american_put_spy.yaml --fetch-market-data
python main.py --config configs/knockout_call_spy.yaml --use-vol-surface
```

## Test-fix-proceed loop (REQUIRED for every non-trivial change)

**This is a standing instruction.** After any code change that is more than a
typo or a one-line doc fix, run the loop below until it terminates green
before declaring the work done. Do NOT report success on a change that has
not been verified through this loop.

```
                ┌──────────────────────┐
                │  Make a change       │
                └──────────┬───────────┘
                           ▼
                ┌──────────────────────┐
                │  Run the gates       │
                │  (see Gates below)   │
                └──────────┬───────────┘
                           ▼
                  ┌────────────────┐
                  │  All green?    │
                  └─┬────────────┬─┘
                yes │            │ no
                    ▼            ▼
              ┌─────────┐  ┌────────────────────┐
              │ Proceed │  │ Diagnose root cause│
              │ (move on│  │ — DO NOT silence   │
              │  / next │  │ the failure or     │
              │  task / │  │ delete the test.   │
              │  PR)    │  └─────────┬──────────┘
              └─────────┘            ▼
                           ┌──────────────────────┐
                           │ Apply minimal fix    │
                           └──────────┬───────────┘
                                      └─► back to "Run the gates"
```

### Gates (run all that apply to the surface you touched)

| Surface touched | Required gate | Command |
|---|---|---|
| Any Python in `src/` | Full backend tests | `python -m pytest tests/` |
| Pricing engine / router | Backend tests **and** a sample `POST /api/price` against a running uvicorn | see "How to run things" |
| Agent / orchestrator | Backend tests **and** a sample `POST /api/agent/sessions` round-trip | — |
| RAG / `market_intelligence.py` | `pytest tests/test_market_intelligence_integration.py tests/test_fred_ingester.py` **and** a live retrieval probe via `get_market_intelligence()` | — |
| Frontend | `cd frontend && npm run build` (catches type errors + bundle errors) | — |
| Anything that changes API request/response shape | Backend tests **and** frontend build (frontend types live in `frontend/src/types.ts`) | — |
| Test infrastructure / fixtures | Run the **whole** suite at least twice in different orders to catch isolation leaks (see `tests/test_llm_provider_claude_code.py` for prior example) | — |

### Loop rules — do not skip

- **Pre-existing failures are not "free passes."** If the suite was red before
  your change, capture the baseline first (`git stash && pytest …`) and prove
  your change didn't add new failures. Don't assume; verify.
- **Test isolation matters.** Failures that only appear when the full suite
  runs together are real bugs (usually env-var / singleton leaks). Diagnose
  by bisecting test order, not by re-running until lucky.
- **Don't delete or `xfail` failing tests to make the loop terminate.**
  That's not "green," it's "hidden red." Fix the root cause.
- **Don't claim work is done until the loop has actually terminated green.**
  "I think it should work" is not a gate.
- **The loop applies recursively.** If a fix introduces a new failure, the
  loop restarts from that failure — don't carry forward.

When the loop terminates green, only THEN write the end-of-turn summary,
commit, or open a PR.

## Default search policy for AI agents

When the user asks about something and **doesn't tell you where to look**:

1. **First**, consult the table above (Where to look first).
2. **If still unclear**, read `architecture.md` for context.
3. **Only then** Grep the codebase. Prefer `src/` and `frontend/src/` over the repo root.
4. **Skip** these directories unless explicitly asked: `node_modules/`, `__pycache__/`, `.pytest_cache/`, `dist/`, `reports/`, `raja_notes/`, `.playwright-mcp/`, `.claude/`, `frontend/dist/`.
5. **For a feature change**, read the matching design spec under `docs/superpowers/specs/` first if one exists.
6. **For a bug**, find the matching test file under `tests/` first — the test name often pinpoints the responsible module.

When updating code:
- Update tests in the same change. Backend tests live in `tests/test_<topic>.py`; e2e in `frontend/tests/`.
- If you add a new option type, update: `router.py` + an engine module + `models.py` (PricingRequest) + `types.ts` (OPTION_TYPES) + `ConfigForm.tsx` + a new test file.
- If you add a market endpoint, update: `src/api/main.py` + `frontend/src/api/client.ts` + the calling component.
- Update `architecture.md` and this file when adding a new top-level directory or major subsystem.
