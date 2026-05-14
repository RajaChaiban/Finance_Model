# eSMM Lab — Listed-Options Market-Making Research Platform

> Sub-platform inside ArgoPilot, targeted at the **Goldman Sachs Equities Systematic
> Market Making (eSMM) Strats** role. Reuses the existing pricing / Greeks /
> vol-surface stack as the fair-value layer; adds the market-making layer on top.

## Why this exists

ArgoPilot v1 is OTC-flavoured: RFQ → price one structured product → memo. eSMM
is the opposite end of the spectrum: continuous quoting in listed instruments,
inventory risk, automated hedging, fill-level P&L attribution, central-risk-book
internalisation. This module adds those primitives without disturbing the
existing pricer.

## Architecture

```
                            ┌─────────────────────────┐
                            │  Existing ArgoPilot     │
                            │  src/engines/*  (fair   │
                            │  value: BS, vol surf,   │
                            │  Greeks, QuantLib)      │
                            └────────────┬────────────┘
                                         │ fair_value (mid / micro / model)
                                         ▼
┌──────────────────┐   snapshot  ┌──────────────────────────────────┐
│ src/esmm/        │────────────▶│ src/esmm/quote_engine            │
│ orderbook.py     │             │   fair + skew + spread → bid/ask │
│ features.py      │             └────────────┬─────────────────────┘
│ synthetic.py     │                          │ quote
└──────────────────┘                          ▼
                            ┌────────────────────────────────────────┐
                            │ src/esmm/backtest                       │
                            │   replay snapshots → simulate fills    │
                            │   (queue model: cross-touch fills)     │
                            └─────┬───────────────────────┬──────────┘
                                  │ fills                 │ inventory updates
                                  ▼                       ▼
                            ┌──────────────┐   ┌──────────────────┐
                            │ src/esmm/    │   │ src/esmm/        │
                            │ inventory.py │   │ hedger.py        │
                            │ (avg cost,   │   │ (band-based      │
                            │  realized)   │   │  delta hedge)    │
                            └──────┬───────┘   └────────┬─────────┘
                                   │                    │
                                   │ position           │ hedge fills
                                   ▼                    ▼
                            ┌────────────────────────────────────────┐
                            │ src/esmm/tca                            │
                            │  decompose P&L:                        │
                            │   • spread capture                     │
                            │   • inventory P&L                      │
                            │   • hedge P&L                          │
                            │   • adverse selection (markout)        │
                            │   • fees / rebates                     │
                            └────────────────────────────────────────┘

         ┌─────────────────────────────────────────────────────────┐
         │ src/esmm/crb — Central Risk Book                         │
         │   firm flow → internalise overlap → residual to street  │
         └─────────────────────────────────────────────────────────┘
```

## Modules

| Module | Purpose |
|---|---|
| `schemas.py` | Pydantic types: `OrderBookSnapshot`, `Quote`, `Fill`, `Position`, `MarketMakingConfig`, `CRBInternalisationResult`, `TCABreakdown` |
| `orderbook.py` | Mid / micro / OBI / weighted-mid / spread-bps / depth |
| `features.py` | `FeatureEngine`: rolling RV, micro-mid drift, signed flow, momentum |
| `inventory.py` | `InventoryBook` with avg-cost accounting + position-flip realised P&L; `inventory_skew_bps()` |
| `quote_engine.py` | Avellaneda-Stoikov-style: fair value + inventory skew + symmetric half-spread; pulls quotes past max inventory |
| `hedger.py` | Band-based auto-hedger: trigger if `|net_delta| > threshold`, hedge back to band |
| `crb.py` | Single-symbol Central Risk Book: internalise min(buys, sells) × cap; report residual + bps savings |
| `backtest.py` | Fill-level replay: snapshot → quote → adversarial fill check → hedger → next |
| `tca.py` | P&L attribution to spread / inventory / hedge / adverse-selection / fees |
| `synthetic.py` | Seeded GBM mid + Poisson L2 generator (so platform runs without paid tick data) |

## API surface

Mounted at `/api/esmm/*`:

| Method | Path | Body |
|---|---|---|
| POST | `/api/esmm/synthetic-book` | `{n_snaps, symbol, start_price, sigma_per_step, base_spread_bps, seed}` → `[OrderBookSnapshot]` |
| POST | `/api/esmm/quote` | `{snapshot, config, seed_position?, adverse_selection_bps}` → `Quote` |
| POST | `/api/esmm/crb/internalise` | `{snapshot, incoming_buys, incoming_sells, internalisation_cap_pct}` → `CRBInternalisationResult` |
| POST | `/api/esmm/backtest` | `{config, n_snaps, start_price, sigma_per_step, base_spread_bps, seed}` → `BacktestResponse` (includes `tca` + downsampled `mid_path_sample`) |

All routes are typed end-to-end via Pydantic; the entire surface is callable
from a notebook in 5 lines.

## Tests

`tests/esmm/*` — 52 tests, ~18 s (fastapi tests dominate the time):

- `test_orderbook.py` — 10 tests: mid / micro / OBI / spread-bps / depth / crossed
- `test_inventory.py` — 10 tests: VWAP averaging, partial close, position flip, MTM, skew cap
- `test_quote_engine.py` — 7 tests: skew direction, max-inventory pull, adverse selection widening
- `test_features.py` — 8 tests: rolling stats eviction, RV, momentum, signed flow, micro-mid drift
- `test_crb.py` — 6 tests: full overlap, partial overlap, cap, no overlap, savings bps
- `test_backtest.py` — 6 tests: end-to-end run, P&L sums to TCA, inventory bound, hedger fires
- `test_api.py` — 5 tests: every endpoint round-tripped via FastAPI TestClient

## Mapping to the GS eSMM JD

> *"Quantitative strategist, equities systematic market making … one delta and
> flow vol SMM groups … central risk books across cash, futures and options …
> automate trading technology front-to-back, develop and analyse strategy
> backtests, build pricing, risk and capital models for stocks."*

| JD requirement | Where it lives in this lab |
|---|---|
| Pricing models for stocks | `src/engines/*` (existing) + `quote_engine.py` (this module) |
| Risk models | `inventory.py` (position risk), `hedger.py` (delta-band hedging), `analysis/vega_bucket.py` (existing vega risk) |
| Capital model | TODO — flag as v2 in talk track; current `tca.py` exposes the inputs |
| Strategy backtests | `backtest.py` — fill-level, queue-aware, hedge-aware |
| Central Risk Book | `crb.py` — internalisation engine + bps savings |
| One Delta | `quote_engine.py` works on any symbol; `synthetic.py` defaults to SPY |
| Flow Vol | `quote_engine.py` accepts a `fair_value` override → plug in the existing QuantLib option pricer for listed-option MM |
| Automate front-to-back | `api/esmm_router.py` — quote / backtest / CRB callable end-to-end via REST |

## Talk track (90 seconds, interview)

> *"I built ArgoPilot as a multi-asset derivatives pricing platform — QuantLib
> engines, a calibrated implied-vol surface, Greeks, agent-driven structuring
> co-pilot. For your eSMM team I added a market-making research layer on top:
> a quote engine that takes the fair value from the existing pricers and
> applies an Avellaneda-Stoikov-style inventory skew with a symmetric
> half-spread; an inventory book with full VWAP accounting and position-flip
> realised P&L; a band-based delta auto-hedger; a Central Risk Book simulator
> that internalises overlapping firm flow and reports the bps saved versus
> hedging on the street; and a fill-level backtester with a deliberately
> adversarial fill model — we only get filled when the touch crosses our
> price, which is the worst-case adverse-selection scenario, so the backtest
> is a stress test not a fantasy. The whole pipeline produces a TCA breakdown:
> spread capture, inventory P&L, hedge cost, adverse-selection markout, fees.
> 47 unit tests + 5 API integration tests. The fair-value layer is decoupled,
> so listed-option market-making just plugs in QuantLib's Black-Scholes engine
> as the `fair_value` arg to the same quote engine."*

## What's intentionally not built (be honest in interview)

- **No real tick data integration** — synthetic GBM + Poisson L2 only.
  Production would plug in databento / polygon via a `DataAdapter`
  conforming to `OrderBookSnapshot`. ~1 week of work.
- **Naive queue model** — fills require touch crossing; no probabilistic
  match, no queue position. A real fill model needs L2 cancel/replace data.
- **Single-symbol CRB** — no multi-asset netting, no priority tiers, no
  capital-aware netting. The math generalises trivially; the engineering is
  larger.
- **No capital / RWA model** yet. The TCA output exposes the inputs (spread
  capture, inventory P&L, fees) needed to plug one in.
- **Delta hedging only** — no gamma / vega hedging triggers. The framework
  supports it; the rules layer is one file extension away.
- **No live paper trading.** Backtest replay only.

## Development workflow

```bash
# Run just the eSMM tests
python -m pytest tests/esmm/ -v

# Run a single backtest from the REPL
python -c "
from src.esmm.backtest import run_backtest
from src.esmm.synthetic import generate_order_book_path
from src.esmm.schemas import MarketMakingConfig
snaps = generate_order_book_path(n_snaps=500, seed=42)
result = run_backtest(snaps, MarketMakingConfig(symbol='SPY'))
print(result.tca)
"

# Start the API and hit the endpoint
python -m uvicorn src.api.main:app --reload --port 8002
curl -X POST http://localhost:8002/api/esmm/backtest \
  -H 'Content-Type: application/json' \
  -d '{"config":{"symbol":"SPY"},"n_snaps":200,"seed":7}'
```
