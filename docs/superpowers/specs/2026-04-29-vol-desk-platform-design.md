# Vol Desk — Platform UI Design

**Date:** 2026-04-29
**Branch:** `feat/vol-desk-platform`
**Status:** Approved (Approach A)

## Goal

Lift the existing single-page Derivatives Pricing Dashboard into a trading-desk-style platform: a market-intelligence layer sits on top of the page (live indices, top movers), and clicking a mover prefills the existing pricer. The change is additive — the existing pricer and agentic co-pilot are untouched and continue to pass all 1190 backend tests.

## Architecture

```
┌─ Header                  Vol Desk wordmark • market-status badge • clock
├─ Index ticker strip      SPY  QQQ  IWM  VIX  + sparkline + %Δ, polled 60s
├─ Hero (condensed)
├─ Movers grid             Gainers │ Losers │ Most Volatile (HV30) — click row → pre-fill pricer
├─ Mode switcher           Pricer │ Co-pilot
├─ ConfigForm OR CopilotPanel
└─ ReportDisplay (enhanced) + PayoffChart, + GreeksBar
```

## New Backend Endpoint

**`GET /api/market/movers?universe=default`**

Response shape:

```json
{
  "as_of": "2026-04-29T15:30:00Z",
  "indices": [
    {"ticker": "SPY", "price": 711.69, "change_pct": 0.42, "spark": [705.2, 706.1, ...]}
  ],
  "gainers":  [{"ticker": "NVDA", "price": ..., "change_pct": ..., "hv30": ...}, ...],
  "losers":   [...],
  "volatile": [...]
}
```

- **Universe (default, ~28 tickers):**
  Indices: `SPY QQQ IWM DIA VIX`
  Mag 7: `AAPL MSFT GOOGL AMZN META NVDA TSLA`
  Sector SPDRs: `XLK XLF XLE XLV XLI XLY XLP XLU XLRE XLB XLC`
  High-beta: `AMD COIN PLTR SHOP NFLX`
- **Implementation:** batch fetch via `yfinance.Tickers(...)`, compute %change from last two daily closes, HV30 from log returns of last 30 closes (same convention as `get_historical_volatility`). Sparklines = last 30 daily closes.
- **Cache:** 60s server-side TTL via the existing `MarketDataCache` pattern. Stale data is served if a refresh fails.
- **Top-K:** 10 per category. Indices section returns all 5 always.

## New Frontend Components

| File | Purpose |
|---|---|
| `frontend/src/components/Header.tsx` | Wordmark, market-status pill (uses NYSE 9:30–16:00 ET local-time check), live clock |
| `frontend/src/components/IndexTickerStrip.tsx` | Five `IndexCard`s with recharts mini-sparkline (Area, no axes), price, %Δ |
| `frontend/src/components/MoversGrid.tsx` | Three `MoverColumn`s; row click dispatches `onPickTicker(symbol)` |
| `frontend/src/components/PayoffChart.tsx` | recharts AreaChart of P&L vs S_T, computed client-side from (optionType, K, premium) |
| `frontend/src/components/GreeksBar.tsx` | recharts BarChart of Δ Γ ν Θ ρ |
| `frontend/src/api/marketDataClient.ts` | `fetchMovers()` with typed response |
| `frontend/src/hooks/useMarketMovers.ts` | 60s polling hook, returns `{data, isLoading, error, lastFetched}` |

## Modified Files

- `src/api/main.py` — register new `/api/market/movers` route
- `src/api/market_data.py` (or new `src/api/movers.py`) — handler + ranking logic
- `src/data/market_data.py` — add `fetch_movers_batch(tickers)` helper
- `frontend/src/components/Dashboard.tsx` — mount Header, IndexTickerStrip, MoversGrid; wire `onPickTicker` to prefill ConfigForm and scroll
- `frontend/src/components/ConfigForm.tsx` — accept controlled prefill from Dashboard (already lifted state)
- `frontend/src/components/ReportDisplay.tsx` — embed `PayoffChart` and `GreeksBar` cards
- `frontend/src/App.css` — add header / ticker strip / movers grid styles, reuse design tokens
- `frontend/package.json` — add `recharts` (^2.15)

## Data Flow

```
Browser ─60s poll→ /api/market/movers ─ yfinance batch ─→ cached 60s
   │
   └─ user clicks NVDA row
        → Dashboard.setFormData({...prev, underlying:"NVDA", spotPrice:fetched_price})
        → scrollIntoView(ConfigForm)
        → user adjusts → submits → /api/price (unchanged)
        → result rendered + PayoffChart from (optionType, K, premium=result.price)
        → GreeksBar reads result.greeks
```

## Error Handling

- **Movers endpoint failure:** ticker strip shows last cached snapshot + "stale" badge; movers grid shows skeleton placeholders + retry button.
- **Single-ticker yfinance miss:** that row drops out of the response; other tickers still render.
- **Empty/zero HV30:** ticker excluded from the "volatile" column rather than rendering noisy data.
- **recharts + React 19:** verified compatible (recharts 2.13+).
- **Existing pricer / co-pilot path:** untouched — zero regression risk on the 1190 passing tests.

## Testing

- **Backend:** `tests/test_movers_endpoint.py`
  - mock `yfinance` to return deterministic OHLC for ~5 tickers
  - assert response structure (keys, sort order)
  - assert top-10 ranking by `change_pct` desc for gainers, asc for losers, by `hv30` desc for volatile
  - assert second call within 60s hits cache (mock should only be called once)
- **Frontend:** Playwright smoke
  - load `/`, assert `[data-testid="vd-header"]` exists
  - assert `[data-testid="vd-movers-grid"]` renders ≥3 rows total
  - click first gainer row, assert `ConfigForm` ticker input value updates
- **Existing tests:** `python -m pytest tests/` must still report 1190 passed.

## Out of Scope

- Real-time websocket streaming (60s polling is enough for v1)
- Watchlist / portfolio pages (Approach C scope)
- Custom universe input from the UI (default universe only for v1)
- Authentication / multi-user state

## Acceptance Criteria

1. `python -m pytest tests/` → 1190+ passed (the new movers test adds to total).
2. Vite dev server boots on 5173, FastAPI on 8002.
3. Page loads with Header + IndexTickerStrip + MoversGrid visible above the existing pricer.
4. Clicking a mover row prefills the ConfigForm ticker and spot price.
5. Submitting the prefilled form returns a price (live yfinance data permitting).
6. ReportDisplay shows a payoff curve and a Greeks bar chart.
7. No console errors. No regressions on the existing co-pilot flow.
