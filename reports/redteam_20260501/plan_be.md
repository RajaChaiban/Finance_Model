# Phase 1 — Backend Probe Plan (approved)

**Liveness:** `GET /health` (NOT `/api/health`). `/` returns metadata.

**Endpoints (src/api/main.py):**
`/health`, `/`, `GET /api/market/{spot-price, dividend-yield, risk-free-rate, historical-volatility, dividend-info, movers}`, `POST /api/price`, agent router `POST /api/agent/sessions`, `GET /api/agent/sessions/{id}`, `POST /api/agent/sessions/{id}/gate/{a|b|c}`, `GET /api/agent/sessions/{id}/events` (SSE).

**PricingRequest required (gt=0): `option_type, underlying, spot_price, strike_price, days_to_expiration, volatility (lt=1)`. Defaults: `risk_free_rate=0.045, dividend_yield=0.015`.**

## 1. Schema fuzz of POST /api/price
- 1.a Missing one-at-a-time → expect 422 with field path. e.g. `{"option_type":"european_call","underlying":"SPY"}`.
- 1.b Wrong types: `"spot_price":"oops"`, `"days_to_expiration":[1]`, `"volatility":null` → 422.
- 1.c Out-of-range: S=0, S=-1, K=0, K=-5, days=0, days=-3, sigma=0, sigma=1.0, sigma=10.5, NaN, +Inf, -Inf via `1e400` & raw NaN token → expect 422 / 400; never 500.
- 1.d Oversized: `underlying = "A"*1_000_000` → expect 413/422.
- 1.e Unknown `option_type:"european_strangle"` → router ValueError → 400.
- 1.f Smile-aware barrier (handlers.py:103-104): `option_type=knockout_call, use_vol_surface=true, barrier_level=120, spot=100` → response `method` MUST contain "FD with Local Vol", NOT "Analytical".
- 1.g `engine="gs"` with `option_type=knockout_call` → 400 ValueError.
- 1.h Asian missing `averaging_method` → defaults to geometric (no 500).
- 1.i Lookback floating w/ K!=S → returns price; sanity record.

## 2. KO+KI = Vanilla parity (assert |KO+KI-EU| < 1e-6)
- 2.a S=100, K=100, B=120, T=180d, sigma=0.20, r=0.05, q=0.02 (UpO call ATM).
- 2.b S=100, K=110, B=90,  T=180d, sigma=0.20, r=0.05, q=0.02 (DnO).
- 2.c S=100, K=95,  B=115, T=365d, sigma=0.30, r=0.05, q=0.02 (ITM).
- Repeat each for puts. Repeat 2.a with `use_vol_surface=true` (tolerance ~1e-3 under PDE).

## 3. /api/market/* fuzz + concurrency
- 3.a `?ticker=ZZZZZZ` on spot-price → expect 400 with detail.
- 3.b Empty, unicode, %00 injection, SQLi, 8KB ticker → 400/422, never 500.
- 3.c yfinance fallback path: hit `ZZZZZZ` and assert response shape stays valid; check `source: "cache"|"api"|"fallback"`.
- 3.d `/api/market/risk-free-rate?days_to_expiration=-1`, `=0`, `=999999` → expect 400; result must not be NaN.
- 3.e **CONCURRENCY (run LAST after ui-breaker is done)**: 50 concurrent GETs to `/api/market/movers`. Measure p50/p95. Expect 0 5xx.

## 4. /api/agent/sessions lifecycle
- 4.a Start: `POST /api/agent/sessions` body `{"intake_form":{"underlying":"SPY","horizon_days":90,"objective":"yield"}}` → expect `status=AWAITING_GATE_A`.
- 4.b Empty start: `{}` → 400 "Provide intake_form, intake_nl, or both."
- 4.c Stale id: `GET /api/agent/sessions/00000000-0000-0000-0000-000000000000` → 404.
- 4.d Approve A: `POST .../gate/a` body `{"approved":true}` → AWAITING_GATE_B.
- 4.e Edit at A: `{"approved":true,"payload":{"edits":{...}}}` → fields updated.
- 4.f Reject B: `{"approved":false}` → CANCELLED.
- 4.g Illegal: approve gate B before A reached → expect orchestrator no-op or 400.
- 4.h Unknown gate letter `.../gate/z` → 400.
- 4.i Extra fields: `{"approved":true,"foo":"bar"}` → 422 (extra=forbid).
- 4.j SSE reconnect: open `GET .../events`, drop, re-subscribe — state must persist.
- 4.k SSE on non-existent id → 404.

## 5. Greeks unit conventions (S=100,K=100,sigma=0.20,T=90d,r=0.05,q=0.02)
- 5.a Price european_call → record vega; re-price sigma=0.21; assert `vega ≈ ΔP/1` (per 1% absolute), NOT ΔP/0.01.
- 5.b Theta: re-price days=89; FD-theta ≈ -ΔP/1d; compare reported theta.
- 5.c Rho: re-price r=0.06; FD-rho ≈ ΔP/1; compare.
- 5.d Repeat 5.a-c for american_put and knockout_call.

## 6. Auth/CORS/error leakage
- 6.a Capture headers on 200 and forced 500 of /api/price; scan body for stack traces / file paths.
- 6.b CORS preflight `OPTIONS /api/price` with `Origin: http://evil.example.com` — middleware echoes Origin (permissive). FLAG.
- 6.c No auth header required anywhere — DOCUMENT.
- 6.d Rate limit: 200 rapid `POST /api/price` small payloads — no middleware exists. FLAG.

## 7. Observability (passive — no backend bounce)
- Per parent reconciliation: SKIP the bounce. Tail any existing log file or rely on the running uvicorn terminal. While probes run, watch for `Traceback|ERROR|5\d\d|exc_info`.

## A. Phase 3 needs backend UP
All items.

## B. Phase 3 needs backend DOWN
None — no bounce per parent reconciliation.

## C. Artifacts (under reports/redteam_20260501/)
- `curl_log.jsonl`
- `parity_table.csv`
- `greeks_fd_table.csv`
- `concurrency_movers.csv`

## Sequencing rule from parent
Step 3.e (50× concurrent burst) runs AFTER ui-breaker reports done. Coordinate via shared findings file.
