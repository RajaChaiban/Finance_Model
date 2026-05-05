# ArgoPilot — Agentic Derivatives Pricing Platform

A FastAPI + React platform for pricing equity options against a calibrated implied-vol surface, with a multi-agent structuring co-pilot, XVA overlay, hedge-ticket emission, and a PRIIPs-style termsheet/KID generator on top.

```
ArgoPilot Platform
├── Market intelligence layer    Live indices, top movers, HV30 ranking
├── Quick Pricer                 18 product types via QuantLib + multi-asset MC
├── Structuring Co-pilot         7-agent planner+specialists workflow (Gemini-driven)
└── Structurer enrichments       XVA, bid/offer, vanna/volga, vega buckets, hedge tickets,
                                 termsheet PDF, PRIIPs KID, lifecycle re-marking, book view
```

## What's in here

| Layer | Stack | Where |
|---|---|---|
| Pricing engines | QuantLib 1.42, NumPy, SciPy | `src/engines/` |
| Multi-asset MC | NumPy correlated GBM (Cholesky + antithetic) | `src/engines/multi_asset_mc.py`, `autocallable.py` |
| FastAPI backend | FastAPI, Pydantic, Uvicorn | `src/api/` (port **8002**) |
| Vol-surface build | yfinance option chain → IV grid → `BlackVarianceSurface` | `src/data/` |
| XVA / risk overlays | FVA + bilateral CVA, vanna/volga, vega buckets | `src/analysis/` |
| Discount + dividend curves | OIS-projection split, dividend-decay forecast | `src/data/discounting.py`, `dividend_curve.py` |
| Implied correlation | Bakshi-Kapadia-Madan equicorrelation | `src/data/correlation.py` |
| Agentic co-pilot | Pydantic state machine, Gemini SDK, SSE streaming | `src/agents/` |
| Hedge ticket / lifecycle / book | Post-trade desk artefacts | `src/agents/hedge_ticket.py`, `lifecycle.py`, `book.py` |
| Termsheet + PRIIPs KID | reportlab PDF + JSON SRI/cost/scenarios | `src/report/term_sheet.py`, `kid.py` |
| Frontend | React 19, TypeScript, Vite 8, recharts 3 | `frontend/src/` (port **5173**) |
| Tests | pytest (1444 tests), Playwright e2e | `tests/`, `frontend/tests/` |

## Supported products

The router (`src/engines/router.py`) dispatches 18 product types. QuantLib is the primary engine for single-asset products; multi-asset products dispatch to a correlated-GBM Monte-Carlo engine.

| Product | Engine | Notes |
|---|---|---|
| `european_call/put` | `ql.AnalyticEuropeanEngine` | Closed-form |
| `american_call/put` | `ql.BinomialVanillaEngine` (LR tree) | MC LSM fallback |
| `knockout_call/put` | `ql.AnalyticBarrierEngine` (or FDM-LV under smile) | BGK shift for discrete monitoring |
| `knockin_call/put` | `ql.AnalyticBarrierEngine` (DnIn/UpIn) | KO+KI=Vanilla parity verified |
| `asian_call/put` | Geometric: closed-form. Arithmetic: MC + geometric control variate | `src/engines/asian.py` |
| `lookback_call/put` | `ql.AnalyticContinuousFloating/FixedLookbackEngine` | Fixed and floating strike |
| `digital_call/put` | Black-Scholes cash-or-nothing closed-form | `src/engines/digitals.py` |
| `phoenix_autocall` | Multi-asset MC, worst-of basket | `src/engines/autocallable.py` |
| `worst_of_call/put` | Correlated-GBM MC | `src/engines/multi_asset_mc.py` |
| `variance_swap` | Carr-Madan log-contract replication over IV strip | `src/engines/variance_swap.py` |

Reverse convertible engine ships (`reverse_convertible.py`) but is not router-wired — it requires structural inputs (coupon rate, par-pricing solver) that don't fit the vanilla pricer signature; v2 wiring left as a follow-up. FX vanilla (Garman-Kohlhagen) and Heston calibration ship as skeletons in `src/data/fx.py` and `src/engines/heston.py`.

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
- `GET /api/market/movers?universe=default` — ArgoPilot dashboard payload (60s cache)
- `POST /api/price` — full pricing + Greeks + HTML report. Response now also carries XVA overlay, bid/mid/offer quote, vanna/volga (auto for KO/KI), vega-bucket grid, surface age.

Agent router (`src/api/agent_router.py`, mounted at `/api/agent/*`):

- `POST /api/agent/sessions` — start session (Intake)
- `GET /api/agent/sessions/{id}` — current state view
- `POST /api/agent/sessions/{id}/gate/{a|b|c}` — HITL gate decision
- `GET /api/agent/sessions/{id}/events` — SSE stream
- `GET /api/agent/sessions/{id}/termsheet` — PRIIPs-style termsheet PDF
- `GET /api/agent/sessions/{id}/kid` — JSON KID payload (SRI bucket, RIY, scenarios)
- `GET /api/agent/sessions/{id}/hedge_tickets` — hedge tickets emitted at Gate C
- `POST /api/agent/sessions/{id}/lifecycle` — re-mark prior trade + reshape options
- `GET /api/agent/book` — book-level aggregated Greeks across stored sessions

## Senior-structurer enrichments

Every priced quote now carries the artefacts a real desk needs to defend a price:

- **XVA overlay** — FVA from funding spread × EPE × T; bilateral CVA from CDS hazard rate; CSA-aware. Configurable per-trade via `xva_inputs`.
- **Bid / mid / offer** — derived from mid + XVA cost; spread reported in bps.
- **Vanna and volga** — auto-computed for KO/KI products (skew-sensitive); togglable elsewhere.
- **Vega-bucket grid** — tenor × strike sensitivity decomposition.
- **Surface age** — age in seconds of the live IV surface; UI surfaces a "stale" badge.
- **Hedge ticket** — emitted on Gate C approval: opening delta/vega/gamma, listed-proxy suggestions, daily gamma-rebalance budget, recommended frequency.
- **Lifecycle re-mark** — re-prices a prior trade against today's regime; emits `close` / `roll` / `enhance` reshape options with Greek-attribution P&L decomposition.
- **Book aggregation** — sums per-underlier and book-level Greeks across all stored sessions.
- **PRIIPs KID** — SRI bucket from MRM × CRM matrix, RIY cost table, multi-horizon performance scenarios.

## Testing

```bash
python -m pytest tests/                # ~1444 backend tests, ~90s
cd frontend && npx playwright test     # 2 e2e specs (pricing-pipeline + vol-desk-platform)
```

Coverage layers: parity/no-arb identities, closed-form references (BS, Reiner-Rubinstein, geometric Asian), convention checks (Vega per 1%, Theta per day, BGK shift), full pipeline smoke (`test_pipeline_with_structurer.py`, `test_agents_smoke.py`), Phase-7 unit (`test_phase7_engines.py`, 26 tests) + stress (`test_phase7_stress.py`, 58 tests covering edge regimes, monotonicity invariants, API round-trips, concurrency, random seed sweeps).

## Documentation

- **`architecture.md`** — full pipeline diagram, engine routing, smile-aware pricing, Greeks conventions, agent layer
- **`SETUP.md`** — install, env vars, troubleshooting
- **`QUANTLIB_INTEGRATION.md`** — QL migration history and engine choice rationale
- **`SOLVER_COMPONENT.md`** — implied-vol solver internals
- **`CLAUDE.md`** — directory map and search guidance for AI agents working in this repo
- **`docs/superpowers/specs/`** — design specs for major features (e.g. ArgoPilot platform UI)
- **`docs/superpowers/plans/`** — implementation plans
- **`raja_notes/`** — learning notes (gitignored, local only)

## Project status

- **1444** backend tests passing (1 pre-existing baseline `asian_put` greek-sign failure unrelated to current work)
- Production-grade QuantLib integration (binomial, FDM-LV, analytic barrier, lookback, Asian)
- 18 product types via single dispatch table (vanilla / barrier / Asian / lookback / digital / variance swap / phoenix autocall / worst-of basket)
- Smile-aware pricing with live SPY surface calibration
- 7-agent structuring co-pilot (Intake → Strategist → Pricing → Scenario → Validator → Narrator with HITL gates)
- ArgoPilot platform UI (live movers, mode switcher, payoff chart, Greeks bar)
- XVA overlay (FVA + bilateral CVA), bid/offer quote, vanna/volga, vega buckets on every priced quote
- Hedge ticket emission at Gate C; book-level aggregation across sessions
- Termsheet PDF + PRIIPs KID JSON endpoints
- Lifecycle agent for post-trade re-marking and reshape suggestions

## License

MIT — built by RajaChaiban (rajachaiban@gmail.com).
