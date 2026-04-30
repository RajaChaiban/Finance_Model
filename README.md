# Vol Desk — Derivatives Pricing Platform

A FastAPI + React vol-desk platform for pricing equity options against a calibrated implied-vol surface, with a multi-agent structuring co-pilot layered on top.

```
Vol Desk Platform
├── Market intelligence layer    Live indices, top movers, HV30 ranking
├── Quick Pricer                 8 vanilla/barrier/Asian/lookback products via QuantLib
└── Structuring Co-pilot         7-agent planner+specialists workflow (Gemini-driven)
```

## What's in here

| Layer | Stack | Where |
|---|---|---|
| Pricing engines | QuantLib 1.42, NumPy, SciPy | `src/engines/` |
| FastAPI backend | FastAPI, Pydantic, Uvicorn | `src/api/` (port **8002**) |
| Vol-surface build | yfinance option chain → IV grid → `BlackVarianceSurface` | `src/data/` |
| Agentic co-pilot | Pydantic state machine, Gemini SDK, SSE streaming | `src/agents/` |
| Frontend | React 19, TypeScript, Vite 8, recharts 3 | `frontend/src/` (port **5173**) |
| Reports | Jinja2 HTML | `src/report/` → `reports/` |
| Tests | pytest (1202 tests), Playwright e2e | `tests/`, `frontend/tests/` |

## Supported products

The router (`src/engines/router.py`) dispatches 12 product types. QuantLib is the primary engine; the no-QL fallbacks exist mainly for portability.

| Product | Engine | Notes |
|---|---|---|
| `european_call/put` | `ql.AnalyticEuropeanEngine` | Closed-form |
| `american_call/put` | `ql.BinomialVanillaEngine` (LR tree) | MC LSM fallback |
| `knockout_call/put` | `ql.AnalyticBarrierEngine` (or FDM-LV under smile) | BGK shift for discrete monitoring |
| `knockin_call/put` | `ql.AnalyticBarrierEngine` (DnIn/UpIn) | KO+KI=Vanilla parity verified |
| `asian_call/put` | Geometric: closed-form. Arithmetic: MC + geometric control variate | `src/engines/asian.py` |
| `lookback_call/put` | `ql.AnalyticContinuousFloating/FixedLookbackEngine` | Fixed and floating strike |

Smile-aware pricing (`use_vol_surface=true`) calibrates a live `BlackVarianceSurface` from the option chain and forces FDM-with-local-vol for barriers (the analytic engine collapses the smile to a scalar and mis-prices the knock-probability term).

## Quick start

```bash
# 1. Install
pip install -r requirements.txt
cp .env.example .env   # add GEMINI_API_KEY if you want the co-pilot

# 2. Backend (port 8002)
python -m uvicorn src.api.main:app --reload --port 8002

# 3. Frontend (port 5173)
cd frontend
npm install
npm run dev

# 4. Browse
open http://localhost:5173
```

### CLI batch pricing (no UI)

```bash
python main.py --config configs/american_put_spy.yaml --fetch-market-data
python main.py --config configs/knockout_call_spy.yaml --use-vol-surface
```

## API surface

Backend (`src/api/main.py`):

- `GET /health`
- `GET /api/market/spot-price?ticker=…`
- `GET /api/market/dividend-yield?ticker=…`
- `GET /api/market/risk-free-rate?days_to_expiration=…`
- `GET /api/market/historical-volatility?ticker=…&lookback_days=…`
- `GET /api/market/dividend-info?ticker=…`
- `GET /api/market/movers?universe=default` — Vol Desk dashboard payload (60s cache)
- `POST /api/price` — full pricing + Greeks + HTML report

Agent router (`src/api/agent_router.py`, mounted at `/api/agent/*`):

- `POST /api/agent/sessions` — start session (Intake)
- `GET /api/agent/sessions/{id}` — current state view
- `POST /api/agent/sessions/{id}/gate/{a|b|c}` — HITL gate decision
- `GET /api/agent/sessions/{id}/events` — SSE stream

## Testing

```bash
python -m pytest tests/                # ~1202 backend tests, ~25s
cd frontend && npx playwright test     # 2 e2e specs (pricing-pipeline + vol-desk-platform)
```

Coverage layers: parity/no-arb identities, closed-form references (BS, Reiner-Rubinstein, geometric Asian), convention checks (Vega per 1%, Theta per day, BGK shift), and full pipeline smoke (`test_pipeline_with_structurer.py`, `test_agents_smoke.py`).

## Documentation

- **`architecture.md`** — full pipeline diagram, engine routing, smile-aware pricing, Greeks conventions, agent layer
- **`SETUP.md`** — install, env vars, troubleshooting
- **`QUANTLIB_INTEGRATION.md`** — QL migration history and engine choice rationale
- **`SOLVER_COMPONENT.md`** — implied-vol solver internals
- **`CLAUDE.md`** — directory map and search guidance for AI agents working in this repo
- **`docs/superpowers/specs/`** — design specs for major features (e.g. Vol Desk platform UI)
- **`docs/superpowers/plans/`** — implementation plans
- **`raja_notes/`** — learning notes (gitignored, local only)

## Project status

- **1202** backend tests passing
- Production-grade QuantLib integration (binomial, FDM-LV, analytic barrier, lookback, Asian)
- Smile-aware pricing with live SPY surface calibration
- 7-agent structuring co-pilot (Intake → Strategist → Pricing → Scenario → Validator → Narrator with HITL gates)
- Vol Desk platform UI (live movers, mode switcher, payoff chart, Greeks bar)

## License

MIT — built by RajaChaiban (rajachaiban@gmail.com).
