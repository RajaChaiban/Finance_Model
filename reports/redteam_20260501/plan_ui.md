# Phase 1 — UI Red-Team Plan (approved)

## 1. ConfigForm field fuzzing
- 1.a `fill` ticker `input` with `NOTAREAL<script>alert(1)</script>` — verify uppercase transform + no XSS execution; check console for CSP errors
- 1.b `fill` ticker with `   spy   ` (whitespace) — verify trim or rejection on debounced fetch
- 1.c `fill` ticker with `АAPL` (Cyrillic A) and `AAPL$` — observe yfinance 404 surfacing
- 1.d `fill` ticker with 5000-char string — observe debounced request abort
- 1.e `evaluate_script` `input[name=spotPrice]` set to `-100`, `0`, `1e308`, `Infinity`, `NaN`, `1,234.56`, `1.234,56`, `0.000001` — submit; confirm `validateForm` blocks <=0 but check NaN path (parseFloat of "" = NaN bypass)
- 1.f `evaluate_script` strikePrice `0`, `-50`, `1e-10`, `1e20` — expect blocked
- 1.g `evaluate_script` daysToExpiration `0`, `-30`, `0.5` (parseInt → 0), `99999`, `NaN` — expect blocked at 0/negative; check huge value
- 1.h `evaluate_script` volatility raw input `-10`, `0`, `500`, `99999`, `NaN` (UI multiplies by 100) — UI rejects <=0 or >100 but allows 100 exactly; test boundary `100.0001`
- 1.i `evaluate_script` riskFreeRate `-50`, `1000`, NaN — currently NOT validated client-side; expect submit
- 1.j `evaluate_script` dividendYield `-100`, `99999` — currently NOT validated client-side
- 1.k `evaluate_script` barrierLevel (when KO/KI) empty, `0`, `-50`, equal-to-spot — submit; check backend rejection + UI display
- 1.l `evaluate_script` nPaths `99` (boundary), `-1`, `1e9` — expect 99 blocked, large value submitted (browser hang risk)
- 1.m `evaluate_script` nSteps `-1`, `0`, `1e6` — no client validation; observe
- 1.n `fill_form` paste HTML `<img src=x onerror=alert(1)>` into Copilot RFQ textarea — verify no execution

## 2. Option-type matrix (12 types, end-to-end via UI)
Per type: select via `option-type-select`, set minimal valid params, click Run, capture price + greeks; then re-run with edge param.
- 2.a european_call: minimal (S=K=100, T=30, sigma=20%); edge: deep ITM K=50 sigma=5%
- 2.b european_put: minimal; edge: K=200 sigma=80%
- 2.c american_call: minimal; edge: high div yield 8% (early-ex regime)
- 2.d american_put: minimal; edge: deep ITM K=200
- 2.e knockout_call: minimal B=120>S; edge: B=80<S (flip to down-and-out)
- 2.f knockout_put: minimal B=80<S; edge: B=120>S (flip to up-and-out)
- 2.g knockin_call: minimal B=120>S; edge: B at spot +/-0.01
- 2.h knockin_put: minimal B=80<S; edge: B exactly = S
- 2.i asian_call: minimal geometric/daily; edge: arithmetic + monthly (flips to MC + CV)
- 2.j asian_put: minimal arithmetic/weekly; edge: geometric/daily
- 2.k lookback_call: minimal fixed; edge: floating
- 2.l lookback_put: minimal floating; edge: fixed K below spot

## 3. Race conditions
- 3.a `click` Run twice within 100 ms via `evaluate_script` (button has `disabled={isLoading}` — verify no duplicate POST in `list_network_requests`)
- 3.b Submit pricer, immediately switch mode to `copilot` via `click` mode-btn — ensure no setState-on-unmounted warning
- 3.c Start Copilot session, click "New RFQ" mid-poll — verify `stopPolling` clears interval
- 3.d Start Copilot, set DevTools network to **offline** mid-poll — observe error banner, no infinite poll
- 3.e Two-tab concurrent: `new_page` second tab → submit pricer in both — confirm distinct sessions
- 3.f Approve Gate A, immediately approve Gate A again (double-click) — verify `busy` guard

## 4. Responsive / viewport
- 4.a `resize_page` 320x568 — screenshot Dashboard
- 4.b `resize_page` 768x1024 — screenshot
- 4.c `resize_page` 1024x768 — baseline
- 4.d `resize_page` 1920x1080 — baseline
- 4.e `evaluate_script` `document.body.style.zoom='2'` at 1024x768
- 4.f Hover-only affordances on MoverColumn rows at 320 width

## 5. Accessibility
- 5.a `press_key` Tab repeatedly — record focus order; flag missing focus-visible
- 5.b Tab into CopilotPanel HITL: GateButtons must be reachable
- 5.c `press_key` Enter on a MoversGrid `vd-mover-row` button — verify keyboard click prefills pricer
- 5.d `lighthouse_audit` on http://localhost:5173 with `categories: ["accessibility"]`
- 5.e `evaluate_script` audit ARIA on ticker strip, movers grid, Copilot SAMPLE_RFQ textarea
- 5.f Check checkbox label associations for deepRisk/useVolSurface

## 6. Error surfacing (BACKEND DOWN via DevTools blocking, not by killing uvicorn)
- 6.a Block `**/api/price` via DevTools network blocking; submit pricer; check UI state — expected silent failure (Dashboard.handlePricingSubmit has no setError)
- 6.b Block `**/api/market/movers`; reload — verify MoversGrid renders empty/loading state
- 6.c Block `**/api/agent/sessions`; click Run in Copilot — verify `copilot-error-banner` shows error
- 6.d Set network **offline**, attempt all flows; collect console messages
- 6.e Block ticker endpoints; type ticker — verify console.error logged but UI doesn't blank out

## 7. Visual / CSS regression
- 7.a-h `take_screenshot` for: dashboard_{1024,1920}, pricer_{1024,1920}, copilot_{1024,1920}, movers_{1024,1920}
- 7.i `evaluate_script` read computed CSS for `--success`, `--danger`, `--brand` design tokens

## A. Phase 3 steps requiring backend UP
All of 1, 2, 3, 4, 5, 7.

## B. Phase 3 steps requiring backend DOWN (via DevTools blocking only)
Item 6.

## C. Artifacts (under reports/redteam_20260501/)
- 8 PNGs: dashboard_{1024,1920}, pricer_{1024,1920}, copilot_{1024,1920}, movers_{1024,1920}
- `lighthouse_a11y.json`
- `console_errors.json`
- `network_failures.json`

## Pre-flagged cross-finding
`Dashboard.handlePricingSubmit` swallows pricing failures (no `catch` setting an error state). Confirm in step 6.a — surface in shared report.
