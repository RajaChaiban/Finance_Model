# README_ESMM — Equity Single-name Market-Making Lab

> A research-grade market-making sandbox with a 3-agent decision loop layered on top
> of the ArgoPilot pricing engines.

## What this is

**eSMM** is a self-contained sub-system that lives under `src/esmm/` (engine) and
`src/agents/esmm/` (agent loop). It simulates an electronic market-maker for a
single equity symbol and runs an agentic *observe → propose → backtest → score*
loop to tune the maker's config.

It is currently a **cash-equity** MM lab (treats one inventory unit as one delta).
The name says "equity options" because options support is the natural next step —
the architecture is already structured to route per-leg deltas/gammas through
`AutoHedger.evaluate_with_gamma`, but a real option-chain adapter and leg-Greek
map are not yet wired.

## What it can do today

| Capability | Module | Status |
|---|---|---|
| Synthetic L2 book replay (GBM-driven mid + Poisson depth) | `src/esmm/synthetic.py` | ✅ |
| Quote generation (Avellaneda-Stoikov half-spread + inventory skew) | `src/esmm/quote_engine.py` | ✅ |
| Inventory accounting with realised/unrealised split | `src/esmm/inventory.py` | ✅ |
| Auto-hedger (delta band rule + optional gamma trigger) | `src/esmm/hedger.py` | ✅ |
| Central Risk Book (single-symbol + multi-symbol internalisation) | `src/esmm/crb.py` | ✅ |
| Fill-level adversarial backtester | `src/esmm/backtest.py` | ✅ |
| TCA decomposition (spread / adverse / hedge / inventory / fees) | `src/esmm/tca.py` | ✅ |
| SQLite persistence for past backtests (opt-in via env) | `src/esmm/persistence.py` | ✅ |
| FastAPI router (backtest, quote, CRB, history endpoints) | `src/api/esmm_router.py` | ✅ |
| Agentic loop: RegimeObserver → ConfigStrategist → TCACritic | `src/agents/esmm/` | ✅ |
| `DataAdapter` Protocol + sortedness validator | `src/esmm/adapters/`, `schemas.py` | ✅ |
| `SyntheticAdapter` (GBM, no network) | `src/esmm/adapters/synthetic_adapter.py` | ✅ |
| `YFinanceAdapter` (real Yahoo bars, free, no key) | `src/esmm/adapters/yfinance_adapter.py` | ✅ |
| `AlpacaAdapter` (real SIP NBBO, free w/ brokerage signup) | `src/esmm/adapters/alpaca_adapter.py` | ✅ (soft-import `alpaca-py`) |
| `TradierAdapter` (sandbox REST: bars + live L1 quotes) | `src/esmm/adapters/tradier_adapter.py` | ✅ |
| `IBKRAdapter` (TWS / IB Gateway, BID/ASK historical bars) | `src/esmm/adapters/ibkr_adapter.py` | ✅ (soft-import `ib_insync`) |
| `POST /backtest/snapshots` + `/backtest/live` + `GET /adapters` | `src/api/esmm_router.py` | ✅ |
| Databento (L2 / MBP-10) | — | ❌ TODO |
| Per-leg option Greeks in `AutoHedger` | — | ❌ TODO |
| Live quoting against a real venue | — | ❌ TODO |

## Layout

```
src/esmm/                    ← engine (deterministic, no LLM)
├── schemas.py              OrderBookSnapshot, Quote, Fill, Position, configs
├── synthetic.py            GBM-driven L2 book generator (default data source)
├── orderbook.py            mid / micro / OBI / spread_bps primitives
├── features.py             RollingStats + FeatureEngine (rv_fast/slow, momentum, signed_flow)
├── inventory.py            InventoryBook (per-symbol P&L) + inventory_skew_bps
├── quote_engine.py         QuoteEngine (Stoikov-style)
├── hedger.py               AutoHedger (delta-band + optional gamma)
├── crb.py                  CentralRiskBook (single + multi-symbol internalisation)
├── backtest.py             run_backtest + adversarial fill rule
├── tca.py                  attribute_pnl → TCABreakdown
└── persistence.py          SQLite store, ESMM_PERSIST=1 to enable

src/agents/esmm/             ← agentic decision layer (still deterministic v1)
├── schemas.py              Regime, RegimeObservation, ConfigProposal, TCAScore, AgenticDecision
├── regime_observer.py      Threshold-based classifier (calm/trending/volatile/stress)
├── config_strategist.py    Maps regime + critic feedback → MarketMakingConfig
├── tca_critic.py           TCABreakdown → 0–100 score + textual recs
└── orchestrator.py         Runs the loop, returns AgenticRunResult

src/api/esmm_router.py       FastAPI router mounted at /api/esmm
frontend/src/api/esmmClient.ts  TS client used by the UI panel
tests/esmm/*                 68+ engine tests
tests/agents/esmm/*          Agent-layer tests
scripts/regression_esmm_backend.py   End-to-end 10-check regression probe
```

## Quickstart

```bash
# 1. Backend
python -m uvicorn src.api.main:app --reload --port 8002

# 2. Run a synthetic backtest from the CLI
curl -X POST http://localhost:8002/api/esmm/backtest \
  -H "content-type: application/json" \
  -d '{
    "config": {"symbol":"SPY","base_half_spread_bps":5,"quote_size":100,
                "max_inventory":1000,"delta_hedge_threshold":50,"delta_hedge_band":10},
    "n_snaps": 500,
    "seed": 42
  }'

# 3. Full agentic loop (Python)
python - <<'PY'
from src.esmm.schemas import MarketMakingConfig
from src.esmm.synthetic import generate_order_book_path
from src.agents.esmm.orchestrator import AgenticESMMOrchestrator

snaps = generate_order_book_path(n_snaps=500, seed=7)
orch  = AgenticESMMOrchestrator(baseline=MarketMakingConfig(symbol="SPY"))
result = orch.run(snaps)
print("converged:", result.converged, "score:", result.best_decision.score.score)
PY

# 4. Persistence on
ESMM_PERSIST=1 ESMM_DB_PATH=./data/esmm.db \
  python -m uvicorn src.api.main:app --reload --port 8002
```

## Test gates

```bash
# Engine tests
python -m pytest tests/esmm/ -v

# Agent-layer tests
python -m pytest tests/agents/esmm/ -v

# End-to-end probe (requires uvicorn running on 8002)
python scripts/regression_esmm_backend.py
```

The repo-wide invariant: **all backend tests must pass** (`python -m pytest tests/`)
before declaring an eSMM change done. The CLAUDE.md test-fix-proceed loop applies.

## Configuration knobs (`MarketMakingConfig`)

| Field | Default | What it does |
|---|---|---|
| `symbol` | — | Instrument the engine quotes. |
| `base_half_spread_bps` | 5.0 | Symmetric half-spread before adverse-selection adders. |
| `inventory_skew_bps_per_unit` | 0.5 | bps the quote pair shifts per unit of inventory (Stoikov). |
| `max_inventory` | 1000.0 | Hard cap; the offending side's `quote_size` goes to 0 past this. |
| `quote_size` | 100.0 | Size each side. |
| `fee_bps` | -0.2 | Negative = maker rebate. |
| `delta_hedge_threshold` | 50.0 | `\|net_delta\|` past this triggers a hedge fill. |
| `delta_hedge_band` | 10.0 | Hedge brings exposure back to this level. |
| `gamma_hedge_threshold` | 0.0 | 0 disables gamma hedging. Otherwise `\|gamma * S²\|` above this triggers. |
| `gamma_hedge_band` | 0.0 | Target gamma exposure after a gamma trade. |

## Agent loop

```
snapshots ─▶ RegimeObserver ─▶ ConfigStrategist ─▶ run_backtest ─▶ TCACritic
                                       ▲                              │
                                       └────── recommendations ◀──────┘
```

* **RegimeObserver** labels the path as CALM / TRENDING / VOLATILE / STRESS using
  `rv_fast`, `momentum`, `signed_flow` thresholds.
* **ConfigStrategist** maps the regime to a multiplier table (e.g. STRESS:
  half-spread ×2.5, max-inv ×0.2, skew ×4) and applies critic-driven fine-tunes
  on subsequent iterations.
* **TCACritic** scores 0–100: `50 + 30·spread_capture_ratio − 20·adv_sel_ratio
  − 15·hedge_drag − 10·inv_volatility`.
* **Orchestrator** loops until `score ≥ acceptance_score` (default 70) or
  `max_iterations` (default 5).

## Honest limitations

* **No real-data adapter yet.** Today every snapshot comes from
  `synthetic.generate_order_book_path`. Real-data plug-in is documented in
  `architecture_ESMM.md`.
* **Cash-equity model.** Options labelling is aspirational; per-leg Greeks
  not yet plumbed through.
* **Adversarial fill rule.** Backtester only fills you when the touch
  crosses *through* your quote — a conservative lower bound, not a real
  match engine.
* **Single-iteration regime observation.** The agent observes the regime
  once per run and iterates within it. A live variant would re-observe each
  step.
* **Persistence is opt-in & uses one connection.** Fine for the lab; not
  intended for production-load access.

See `architecture_ESMM.md` for the data-flow diagram, contract for a future
`DataAdapter`, and the catalogue of free/paid APIs that can plug in.
