# Architecture — Derivatives Pricing Pipeline

A FastAPI + React derivatives pricer with QuantLib as the primary numerical engine. Supports European, American, and barrier (knock-out / knock-in) options with optional live IV-surface calibration.

## High-level flow

```
React UI (Vite, port 5173+)
    │
    │  POST /api/price  { option_type, S, K, T, σ, r, q, ... }
    ▼
FastAPI app (src/api/main.py, port 8003)
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
    └── /api/market/*   spot, dividend, rate, hist-vol, dividend-info
            (Yahoo Finance via market_data.py)
```

## Repository layout

```
src/
├── api/                  FastAPI layer
│   ├── main.py           App + CORS + market data endpoints
│   ├── handlers.py       price_option orchestrator
│   ├── models.py         Pydantic request/response schemas
│   └── market_data.py    Yahoo Finance adapters (with retry/cache)
│
├── engines/              Pricing engines
│   ├── router.py         Dispatch: option_type → (pricer, greeks, label)
│   ├── quantlib_engine.py   PRIMARY engine (binomial, FDM, barrier)
│   ├── black_scholes.py  Analytic European fallback
│   ├── knockout.py       Reiner-Rubinstein KO + BGK discrete-monitor shift
│   └── monte_carlo_lsm.py   Wraps ql.MCAmericanEngine (Longstaff-Schwartz)
│
├── data/                 Vol-surface construction
│   ├── iv_grid.py        Invert option-chain quotes → IV grid
│   ├── vol_surface.py    Build ql.BlackVarianceSurface from grid
│   └── rate_conventions.py
│
├── config/loader.py      YAML config + validation (PricingConfig)
├── report/generator.py   HTML report rendering
└── analysis/             Structurer agent (LLM commentary)

frontend/src/
├── api/client.ts         Typed APIClient (fetch wrapper)
├── components/
│   ├── ConfigForm.tsx    Input form (collapsible sections)
│   ├── Dashboard.tsx     Page shell + step indicator
│   └── ReportDisplay.tsx Renders backend HTML report + downloads
└── types.ts              Shared TS types + OPTION_TYPES dict

tests/                    pytest, 524 tests, ~25 s
```

## Engine routing

`src/engines/router.py` is the single point of dispatch. Each `option_type` string maps to a `(pricer_fn, greeks_fn, method_label)` tuple. QuantLib is preferred; if `import QuantLib` fails at module load, the router falls back to pure-Python implementations.

| `option_type`     | Primary engine                           | Fallback                                  |
|-------------------|------------------------------------------|-------------------------------------------|
| `european_call/put` | `ql.AnalyticEuropeanEngine`            | `black_scholes.price_european`            |
| `american_call/put` | `ql.BinomialVanillaEngine` (LR tree)   | `monte_carlo_lsm.price_american` (QL MC)  |
| `knockout_call/put` | `ql.AnalyticBarrierEngine` or FDM-LV    | `knockout.price_knockout` (Reiner-Rubinstein) |
| `knockin_call/put`  | `ql.AnalyticBarrierEngine` (DnIn/UpIn) | KO + parity: `KI = Vanilla − KO`          |

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

## Frontend wiring

`frontend/src/api/client.ts` defaults to `${protocol}//${hostname}:8003` (override with `VITE_API_URL`). The form auto-fetches market data on mount:

```
ConfigForm.tsx mount
  → GET /api/market/spot-price
  → GET /api/market/dividend-yield
  → GET /api/market/historical-volatility
  → GET /api/market/risk-free-rate
```

On submit:
```
POST /api/price → render PricingResult
  → ReportDisplay renders the inline HTML report
  → user can download HTML or PDF
```

The `option_type` dropdown lists all 8 product flavours: 2× exercise (American/European) × 2× side (call/put) + 4× barrier (KO/KI × call/put). Barrier options surface a `barrier_level` field; direction is inferred from B vs S server-side.

## Configuration

Two equivalent input formats:

- **YAML config** (`configs/*.yaml`) for batch runs via `python main.py --config <path>` — validated by `src/config/loader.py:PricingConfig`.
- **REST request** (`PricingRequest` Pydantic model) for the UI — converted to `PricingConfig` in `handlers._request_to_config`.

Both paths converge at `router.route()`.

## Testing

`pytest tests/` — 524 tests, ~25s. Coverage layers:

- **Engine correctness**: `test_quantlib_correctness.py`, `test_smile_pricing.py`, `test_knockin.py` (KO+KI=Vanilla parity), `test_combinations.py` (all 8 option types via router).
- **Conventions**: `test_mc_theta.py` (per-day, sign), `test_rate_conventions.py`, `test_dividend_normaliser.py`, `test_evaluation_date.py`.
- **Numerical methods**: `test_fdm_greeks.py`, `test_mc_antithetic.py`, `test_step_count_policy.py`, `test_bgk_shift.py`, `test_discrete_monitoring.py`, `test_discrete_dividend.py`.
- **Solver / IV**: `test_solver_iv_default.py`, `test_iv_grid.py`, `test_vol_surface.py`, `test_option_chain.py`.
- **Engine consistency**: `test_engine_consistency.py`, `test_engines.py`.

Engine priorities: parity / no-arb identities first (cross-checks engines against themselves), then closed-form references where available (Black-Scholes for European, Reiner-Rubinstein for KO).

## Known caveats

1. **MC is not user-selectable**. The frontend MC controls are inert in production. Fix: add `engine` to `PricingRequest`.
2. **American + barrier is not supported**. The router has no `american_knockout_*`. The QL `AnalyticBarrierEngine` is European-exercise only; American barriers would need a tree or PDE engine wired in.
3. **Theta beyond 1 day** is not exposed — only the standard per-day decay.
4. **Greeks for barriers near the barrier** use a barrier-distance-aware bump step (`greeks_knockout_ql:604-609`) to avoid pin-risk noise; this is intentional but means delta/gamma reported very close to the barrier are smoothed.
5. **MC fallback module requires QuantLib** (post-swap), so the `if not QUANTLIB_AVAILABLE` branch in `router.py` is logically unreachable in MC paths. Cleaning this up is a follow-up.
