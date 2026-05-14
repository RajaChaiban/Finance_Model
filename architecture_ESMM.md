# architecture_ESMM — Equity Single-name Market-Making Lab

## Goal

A research-grade market-maker that:
1. Runs against a typed `OrderBookSnapshot` stream (synthetic *or* real).
2. Quotes via an Avellaneda-Stoikov-style engine with inventory skew.
3. Manages risk via a band-rule auto-hedger (delta now, gamma optional).
4. Internalises overlapping firm flow via a Central Risk Book.
5. Is wrapped by a deterministic 3-agent decision loop that proposes,
   backtests, and grades configurations.

Each layer is a pure-function over its inputs, so the same engine drives
**unit tests, synthetic backtests, and (planned) live data** without code
changes — only the *source* of `OrderBookSnapshot`s changes.

## Layered architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  FRONTEND PANEL (frontend/src/components/EsmmPanel.tsx)              │
│  - synthetic-book preview, backtest trigger, TCA chart, history      │
└──────────────────────────────────────────────────────────────────────┘
                              │  REST + JSON
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│  FastAPI ROUTER (src/api/esmm_router.py)                             │
│  POST /api/esmm/backtest  /quote  /crb/internalise  /synthetic-book  │
│  GET  /api/esmm/backtests  /backtests/{id}                           │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│  AGENT LAYER (src/agents/esmm/)                                      │
│   RegimeObserver → ConfigStrategist → run_backtest → TCACritic       │
│   ▲                                                       │          │
│   └────────── recommendations (next-iter fine-tunes) ◀────┘          │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│  ENGINE (src/esmm/)                                                  │
│                                                                      │
│  ┌──────────────┐   ┌──────────────┐   ┌────────────┐               │
│  │ FeatureEngine│──▶│ QuoteEngine  │──▶│ Backtester │──▶ Fills      │
│  └──────────────┘   └──────────────┘   └────────────┘               │
│         ▲                  ▲                 │                       │
│         │                  │                 ▼                       │
│   OrderBookSnapshot   InventoryBook    AutoHedger ◀── net_delta      │
│         ▲                                                            │
└─────────┼────────────────────────────────────────────────────────────┘
          │
          ▼
┌──────────────────────────────────────────────────────────────────────┐
│  DATA SOURCE — exactly ONE of:                                       │
│  - src/esmm/synthetic.py (today)                                     │
│  - DataAdapter implementing OrderBookSnapshot iterable (planned)     │
└──────────────────────────────────────────────────────────────────────┘
```

## Data contract

Everything between modules is a **Pydantic** model in `src/esmm/schemas.py` or
`src/agents/esmm/schemas.py`. Two contracts dominate:

### `OrderBookSnapshot`

```python
class OrderBookSnapshot(BaseModel):
    ts: float           # Unix epoch seconds (float ⇒ sub-second OK)
    symbol: str
    bids: list[OrderBookLevel]   # MUST be sorted descending by price
    asks: list[OrderBookLevel]   # MUST be sorted ascending  by price
```

This is the *one* type a `DataAdapter` must yield. Once an adapter produces
these, every downstream module — features, quote engine, backtester, TCA, agents —
works unchanged.

> **Invariant gap to close before going live:** the schema does not currently
> validate sort order. Synthetic data is well-formed by construction; real feeds
> can produce out-of-order books on rare race conditions. Add a Pydantic
> `model_validator` before connecting any external source.

### `MarketMakingConfig`

The single struct that captures every quoting + risk knob. The agentic
strategist mutates this; the engine reads it. See README for field-by-field
semantics.

## Data flow per snapshot (live or replay)

```
              ┌────────────────────────────────────┐
              │  new OrderBookSnapshot arrives     │
              └────────────────┬───────────────────┘
                               ▼
   1.  Did the previous quote get hit?
       → check_fills(prev_quote, snap, prev_mid)
       → apply Fill(s) to InventoryBook
                               ▼
   2.  Auto-hedger evaluates net_delta (and net_gamma_dollar)
       → emits 0/1/2 hedge fills
       → applied to inventory
                               ▼
   3.  QuoteEngine re-quotes off post-fill state
       → fair_value = micro_price(snap)
       → skew = inventory_skew_bps(position, max_inv, skew_per_unit)
       → bid = (fair − skew_amt) − half_spread,  ask = symmetric
       → if |position| ≥ max_inventory, pull the offending side
                               ▼
   4.  Append (quote, mid, inventory) to result; loop
                               ▼
   5.  End of path → attribute_pnl(fills, snaps) → TCABreakdown
```

## Agent decision loop (`src/agents/esmm/orchestrator.py`)

```
                      ┌──────────────────────┐
   snapshots ────────▶│   RegimeObserver     │
                      │  classify_regime()   │
                      └──────────┬───────────┘
                                 │ RegimeObservation
                                 ▼
                      ┌──────────────────────┐
   prior_score ──────▶│   ConfigStrategist   │
                      │ propose(observation, │
                      │    prior_score)      │
                      └──────────┬───────────┘
                                 │ ConfigProposal
                                 ▼
                      ┌──────────────────────┐
                      │     run_backtest     │
                      └──────────┬───────────┘
                                 │ TCABreakdown
                                 ▼
                      ┌──────────────────────┐
                      │      TCACritic       │
                      │     score(tca)       │
                      └──────────┬───────────┘
                                 │ TCAScore (0-100)
                                 ▼
                ┌────────────────────────────┐
                │ score ≥ acceptance_score?  │
                └─────┬──────────────────┬───┘
                      │ yes              │ no
                      ▼                  ▼
              ACCEPTED               increment iter
              return result          feed score back
                                     to strategist
```

* **Convergence:** when `score ≥ 70` (configurable).
* **Bail-out:** `max_iterations` (default 5); return best-scoring decision.
* **Single regime per run:** Today the observer runs once on the full path; the
  strategist iterates within that regime context. A future live variant should
  re-observe each step.

## Engine modules — one-line summary each

| Module | Role |
|---|---|
| `schemas.py` | Pydantic types shared by everything. |
| `orderbook.py` | `mid_price`, `micro_price`, `spread_bps`, `order_book_imbalance`, `weighted_mid_price`. |
| `features.py` | `RollingStats` + `FeatureEngine` (rv_fast/slow, momentum, signed_flow, micro−mid bps). |
| `inventory.py` | `InventoryBook` with full P&L accounting (open/close/flip slices). |
| `quote_engine.py` | Stoikov-style quote: `bid = (fv − skew) − half_spread`, ask symmetric. |
| `hedger.py` | `AutoHedger.evaluate` (delta-band) + `evaluate_with_gamma`. |
| `crb.py` | `CentralRiskBook.internalise` + `.internalise_book` (multi-symbol). |
| `backtest.py` | `run_backtest` with the adversarial fill rule (only filled when touch crosses through). |
| `tca.py` | `attribute_pnl` → `TCABreakdown` with adverse-selection markouts. |
| `persistence.py` | SQLite, opt-in via `ESMM_PERSIST=1`. |
| `synthetic.py` | GBM-mid + Poisson-depth book generator (the only source today). |

## Agent modules — one-line summary each

| Module | Role |
|---|---|
| `schemas.py` | `Regime`, `RegimeObservation`, `ConfigProposal`, `TCAScore`, `AgenticDecision`, `AgenticRunResult`. |
| `regime_observer.py` | Threshold-based classifier (calm / trending / volatile / stress). |
| `config_strategist.py` | Maps `(regime, prior_score) → MarketMakingConfig`. |
| `tca_critic.py` | `TCABreakdown → TCAScore (0-100)` with textual recs. |
| `orchestrator.py` | Drives the observe→propose→backtest→score loop until accepted or max-iter. |

## Conventions & gotchas

* **Direction sign convention.** Quote skew is positive when long inventory →
  shift the *whole* (fv, bid, ask) triplet *down*. Same logic, symmetric, for
  shorts. This matches Stoikov.
* **Inventory = delta for cash equity only.** `backtest.py:91-94` makes the
  one-to-one assumption explicit. For listed-options MM the assumption breaks;
  see the roadmap section.
* **CRB savings.** `saved_bps = street_spread_bps` represents the *full* spread
  saved per fully-matched pair (each leg saves a half-spread). The variable
  reports per matched pair, not per share — callers multiplying by quantity
  should multiply by pair count.
* **Markout horizon.** TCA's adverse-selection bucket reads
  `snapshots[i + 10]` after each fill. If the path ends within 10 snaps of a
  fill, that fill contributes 0 to adverse selection — a small bias on short
  paths.
* **`fee_bps` sign.** Negative = maker rebate (credited to P&L). Positive =
  taker fee.
* **`signed_flow` is dead today.** `FeatureEngine` accepts `recent_fills`, but
  neither the backtester nor `RegimeObserver` passes them. Wire this through
  before relying on the signed-flow regime feature.

## Plugging in real data — the `DataAdapter` contract

The engine is data-source-agnostic, but the entry point isn't formalised yet.
The intended contract is:

```python
class DataAdapter(Protocol):
    """Yields OrderBookSnapshots one at a time.

    Implementations MUST:
      - emit snapshots in non-decreasing `ts` order
      - emit bids descending, asks ascending
      - never emit a crossed book (best_bid >= best_ask); on the wire,
        drop or repair the snapshot upstream
    """
    def stream(self, symbol: str) -> Iterable[OrderBookSnapshot]: ...

    def replay(self, symbol: str, start: datetime, end: datetime
              ) -> Iterable[OrderBookSnapshot]: ...
```

Once an adapter exists, the API router gains a parallel endpoint:

```
POST /api/esmm/backtest/live      body: { symbol, start, end, config }
                                  reads via DataAdapter.replay()

POST /api/esmm/backtest/snapshots body: { snapshots: [...], config }
                                  reads from the request body directly
```

The existing `POST /api/esmm/backtest` (synthetic) stays unchanged.

## Candidate data sources (free → paid)

> See **README_ESMM.md** for the matching adapter status. The detailed
> evaluation lives in the API-research notes generated alongside this
> document.

| Source | Cost | Granularity | License-OK for backtest research? |
|---|---|---|---|
| **yfinance** (Yahoo) | free, no key | EOD + delayed top-of-book | yes (personal use) |
| **Alpha Vantage** | free key, 25/day | EOD + intraday 1-min | yes (terms allow research) |
| **Polygon.io free tier** | free key | 15-min delayed quotes, full options chain | research only |
| **Finnhub** | free key | quasi-real-time top-of-book US equities | research only |
| **IEX Cloud / IEXG** | free historical | TOPS top-of-book free, DEEP L2 free historical | yes |
| **CBOE DataShop (free historical)** | free | EOD + some L1 options | yes |
| **Databento** | paid | L1, L2, full MBP-10, TAQ — institutional | with subscription |
| **Polygon paid** | paid | L2 + full options chain in real time | with subscription |
| **IBKR TWS API** | $1.50/mo data | L1 quotes, options chain, paper trading | with brokerage account |

The synthetic generator stays as the offline default — even after real adapters
land, you want a deterministic seed for tests + CI.

## Roadmap (in priority order)

1. **`OrderBookSnapshot` sortedness validator** (5 min, blocks safe real-data plug-in).
2. **`DataAdapter` Protocol + a `YFinanceAdapter` reference impl** (free, L1 only).
3. **`/api/esmm/backtest/snapshots`** endpoint that accepts a raw snapshot
   array — lets the frontend or any external system stream from any source.
4. **Wire `recent_fills` into the live `FeatureEngine`** so `signed_flow` becomes a
   live feature, not dead weight.
5. **`IBKRAdapter` paper-trading path** for the first real-data integration test.
6. **Per-leg option Greeks** in `AutoHedger` (multiply inventory by leg delta /
   gamma rather than treating 1 share = 1 delta).
7. **Re-observe each loop iteration** in the orchestrator's live mode.
8. **`DatabentoAdapter`** when a paid subscription becomes viable — proper L2,
   the only feed that lets you genuinely measure the queue-position assumption
   in the backtester.
