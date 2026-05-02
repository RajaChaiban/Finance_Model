# Vol Desk red-team findings — 2026-05-01

Append-only. Each agent owns its own section header — DO NOT edit the other agent's section. Severity legend: **P0** = crash / data corruption / security; **P1** = wrong number / broken core UX; **P2** = polish / a11y / cosmetics.

Entry template:
```
### [SEVERITY] short title
- repro: ...
- evidence: path-to-screenshot OR curl + response snippet OR console excerpt
- suspected: file:line
```

---

## UI findings (owner: ui-breaker)

### [P1] Run Pricing button does not deduplicate rapid clicks → multiple POSTs
- repro: Sit in pricer, ensure params valid. From DevTools console: `for (let i=0;i<3;i++) document.querySelector('button.submit-button').click();` Two synchronous clicks fire 2 POST /api/price requests; three clicks fire 3.
- evidence: `performance.getEntriesByType('resource').filter(r => r.name.includes('/api/price')).length` jumps by N where N = synchronous click count. The `disabled={isLoading}` guard is enforced only after React commits the next render, so multiple clicks within one task all bypass it.
- suspected: frontend/src/components/ConfigForm.tsx:141 (handleSubmit lacks an in-flight ref guard) and frontend/src/components/Dashboard.tsx:39 (handlePricingSubmit could also short-circuit if isLoading is true)

### [P1] Risk-free rate has no client/server upper bound → priced at 1000% silently
- repro: spot=100, strike=100, days=30, vol=20, dividend=0, set Risk-Free Rate (%) field to `1000`, click Run. Pricing succeeds; results show "Risk-Free Rate 1000.00%" inline.
- evidence: Pricing report rendered with `Risk-Free Rate 1000.00%`, `Option Price $0.0263` (because americanput in deep ITM regime); see network reqid=2531 POST /api/price [200].
- suspected: frontend/src/components/ConfigForm.tsx:121 (validateForm only checks negative bounds for risk-free, no upper). Backend at src/config/loader.py also does not reject.

### [P1] Dividend yield accepts arbitrary positive/negative values silently
- repro: Set Dividend Yield (%) to `99999` or `-100` and submit. No client error; backend returns 200.
- evidence: with Dividend Yield = 99999 the pricing call returned 200 OK; UI shows the value inline in report.
- suspected: frontend/src/components/ConfigForm.tsx ~line 100-140 (handleChange and validateForm have no validation for dividendYield)

### [P1] Days to Expiration accepts 99999 client-side; backend errors leak QuantLib-internal date message
- repro: spot=100, strike=100, vol=20, dividend=0, rate=4, set Days to Expiration to `99999`, submit.
- evidence: error-box renders `Pricing failed: QuantLib American pricing failed: Date's serial number (146142) outside allowed range [367-109574], i.e. [January 1st, 1901-December 31st, 2199]` — exposes internal QL identifiers to the user.
- suspected: frontend/src/components/ConfigForm.tsx:130-140 (no upper bound on daysToExpiration). Backend should reject before QuantLib.

### [P1] Ticker input not trimmed; whitespace round-trips to backend, returns 400 to user
- repro: Type `   spy   ` (with spaces) into Ticker. Component fetches `GET /api/market/spot-price?ticker=%20%20%20SPY` → 400. User sees `"Could not fetch spot price for ticker    SPY. Please enter manually."`
- evidence: console error `Error fetching ticker market data: Error: Could not fetch spot price for ticker    SPY. Please enter manually.` Network reqid=2509 [400]. Ticker should trim before fetch.
- suspected: frontend/src/components/ConfigForm.tsx (ticker handleChange or onBlur) — uppercase only, no trim

### [P1] Lighthouse a11y score 79/100 (4 hard failures)
- repro: Run lighthouse_a11y.json (snapshot mode, desktop) on http://localhost:5173/.
- evidence: lighthouse_a11y.json shows: `color-contrast` (score 0), `label` (form elements without labels, score 0), `landmark-one-main` (no <main> landmark, score 0), `select-name` (selects without label association, score 0). Console also reports "No label associated with a form field (count: 7)" and "A form field element should have an id or name attribute (count: 7)".
- suspected: Many components — `frontend/src/components/ConfigForm.tsx` (labels are present as <label> siblings but not via for/id), `frontend/src/components/Dashboard.tsx` (no <main> wrapper)

### [P1] No :focus-visible styling — keyboard focus is barely perceptible
- repro: Tab through the page. Buttons/links get default browser outline only; CSS does not define :focus-visible rules.
- evidence: `Array.from(document.styleSheets).flatMap(...)` finds 0 rules with `:focus-visible` selector and only 3 `:focus` rules. App.css design tokens define `--shadow-focus` but the rule is only applied inside specific selectors (.form-group input:focus etc), not for buttons / nav / mover rows.
- suspected: frontend/src/App.css — global :focus-visible rule missing

### [P1] Form inputs lack name and id attributes
- repro: View any number input in DevTools.
- evidence: `document.querySelectorAll('input[type="number"]')` shows every input has `name=""` and `id=""`. Console "issue" tab shows "A form field element should have an id or name attribute (count: 7)".
- suspected: frontend/src/components/ConfigForm.tsx:512-535 (inputs render `<label>X</label><input ...>` without id/htmlFor pairing)

### [P2] Ticker accepts 5000-char string and pings ticker endpoints with full payload
- repro: Programmatically set ticker to 5000-char `A`, dispatch `input`. UI fires GET /api/market/spot-price?ticker=AAA…(5000A). Spot returns 400; div-yield and historical-vol return 200 with junk values.
- evidence: network reqids 2518-2520 confirm 5000-char query string accepted by 2/3 endpoints. Browser silently truncates URL only on extremely long requests — the form will swallow whatever yfinance returns.
- suspected: frontend/src/components/ConfigForm.tsx (no maxLength on ticker input)

### [P2] Volatility=0 submission leaks unrelated QuantLib date error from prior bad state
- repro: After submitting daysToExpiration=99999, switch days back to 90 and set Volatility=0; submit.
- evidence: error-box returns the *previous* date-range error rather than a fresh "volatility must be > 0" — error_state isn't cleared between submits if pricing rejects again.
- suspected: frontend/src/components/ConfigForm.tsx:147-151 (catch sets errors.submit but doesn't clear when validateForm passes & re-attempts)

### [P2] Knockout/Knockin with B == S submits and returns a price (no warning)
- repro: knockin_put, S=100, K=100, B=100, vol=20, days=30. Submits; backend returns a price.
- evidence: `errs:[]`, prices returned. There is no UI warning that B at spot is degenerate (instant-knock).
- suspected: frontend/src/components/ConfigForm.tsx (no advisory banner for B≈S barrier)

### [P2] Cyrillic А (U+0410) accepted in ticker, sent URL-encoded to backend
- repro: Type `АAPL` (first char Cyrillic) into Ticker; backend gets `?ticker=%D0%90APL` → 400 from spot, 200 (junk) from others.
- evidence: network reqids 2512-2514. UI shows error from spot but not from div-yield/hist-vol, leading to inconsistent state.
- suspected: frontend/src/components/ConfigForm.tsx — no ASCII-only validator on ticker

### [P2] Numeric inputs lack HTML5 min/max constraints
- repro: Inspect `input[type="number"]` for spot/strike/days/rate/vol — all show empty min/max attributes.
- evidence: `{step:"0.01", min:"", max:""}` on every numeric input.
- suspected: frontend/src/components/ConfigForm.tsx — should add min="0" min="1" etc to assist browsers and screenreaders

### [P2] No <main> landmark; assistive tech can't skip to main content
- repro: `document.querySelectorAll('main, [role=main]')` returns empty.
- evidence: confirmed in lighthouse landmark-one-main audit (score 0).
- suspected: frontend/src/components/Dashboard.tsx:95 (`<div className="dashboard-onepage">` should be `<main>` or wrap children in <main>)

### [P2] Color-contrast failures (lighthouse audit)
- repro: lighthouse a11y snapshot.
- evidence: `color-contrast` audit score 0. Likely candidates: `--text-muted #94A3B8` on `--bg-base #F8FAFC` (~3.0:1, below 4.5:1 WCAG AA) and the "MARKET CLOSED" pill grey-on-grey.
- suspected: frontend/src/App.css design tokens — increase contrast for muted text on light backgrounds

### [P2] React warns "Received NaN for the `value` attribute" when number input is cleared
- repro: Clear any number field (Spot, Strike, Days, Vol, Rate). React state stores `parseFloat("") = NaN` and the controlled input prop receives NaN.
- evidence: console: `[error] Received NaN for the `%s` attribute. If this is expected, cast the value to a string. value`. Also recurring `[warn] The specified value "NaN" cannot be parsed`.
- suspected: frontend/src/components/ConfigForm.tsx (handleChange uses parseFloat without fallback to 0/empty-string)

### [P2] Cross-finding follow-up: pricing failures DO surface as `error-box`
- repro: Block POST /api/price via fetch shim returning 500; submit pricer.
- evidence: error-box renders `injected error`. The pre-flagged concern that Dashboard.handlePricingSubmit "swallows" failures is mitigated because ConfigForm.handleSubmit (line 145) wraps onSubmit in try/catch and writes `errors.submit` which is rendered. NOT a P0/P1.
- suspected: frontend/src/components/Dashboard.tsx:39 (no catch — but ConfigForm catches downstream)

---

## Backend findings (owner: backend-prober)

### [P0] CORS middleware reflects arbitrary Origin → any web page can drive the API
- repro: `curl -i -X OPTIONS -H "Origin: http://evil.example.com" -H "Access-Control-Request-Method: POST" http://localhost:8002/api/price` and `curl -i -X POST -H "Origin: http://attacker.test" -H "Content-Type: application/json" -d '{"option_type":"european_call","underlying":"AAPL","spot_price":100,"strike_price":100,"days_to_expiration":90,"volatility":0.2}' http://localhost:8002/api/price`
- evidence: preflight returns `access-control-allow-origin: http://evil.example.com`; POST returns `access-control-allow-origin: http://attacker.test`. Combined with no auth and full pricing access, any random page on the internet can drive the API from a victim's browser. CORS is the only browser barrier here and it is wide open.
- suspected: src/api/main.py:35 and src/api/main.py:44 (`origin or "*"` reflection in CustomCORSMiddleware — should be allowlist of known origins like `http://localhost:5173`)

### [P0] NaN / Infinity / 1e400 in JSON body crash with raw 500 + plain "Internal Server Error"
- repro: `curl -i -X POST -H "Content-Type: application/json" -d '{"option_type":"european_call","underlying":"SPY","spot_price":100,"strike_price":100,"days_to_expiration":90,"volatility":NaN}' http://localhost:8002/api/price` (also Infinity, -Infinity, 1e400)
- evidence: 500 response, body `Internal Server Error`. Pydantic does not reject non-finite floats — they reach engine and explode. Same pattern with `volatility:Infinity`, `volatility:1e400`, `spot_price:NaN`. ALSO: `spot_price:Infinity` returns 200 with `price:null`, `delta:nan`, and `$inf` rendered into the HTML report — i.e. a successful API response that ships NaN-laden Greeks.
- suspected: src/api/models.py:14-19 — `gt=0`/`lt=1` does not filter NaN/Inf. Add `pydantic.confloat(allow_inf_nan=False)` or explicit validators.

### [P0] use_vol_surface=true silently falls back to scalar σ → invariant violation when surface fails
- repro: `curl -X POST -H "Content-Type: application/json" -d '{"option_type":"knockout_call","underlying":"SPY","spot_price":100,"strike_price":100,"days_to_expiration":90,"volatility":0.2,"barrier_level":120,"use_vol_surface":true}' http://localhost:8002/api/price`
- evidence: response method is `"QuantLib (Barrier, Analytical)"` (NOT `"FD with Local Vol"`), and `sigma_atm`/`sigma_barrier`/`surface_quotes_inverted` all `null`. Per CLAUDE.md invariant + handlers.py:103-104, smile-aware barrier MUST force FD-with-local-vol. The surface build silently failed (likely empty SPY chain), the handler logged a warning, but the response gave NO indication to the client that the request was downgraded to a smile-blind analytic engine. Client believes they got a smile-aware price; they did not.
- suspected: src/api/handlers.py:113-121 (`falling back to scalar σ` is logger.warning only — should also set a response field like `surface_status: "fallback"` so client can refuse the price).

### [P0] use_vol_surface=true accepts garbage IV inversions (sigma=326% / 1081%) without sanitization
- repro: `curl -X POST -H "Content-Type: application/json" -d '{"option_type":"knockout_call","underlying":"AAPL","spot_price":190,"strike_price":190,"days_to_expiration":90,"volatility":0.25,"barrier_level":220,"use_vol_surface":true}' http://localhost:8002/api/price` and similar with S=100,K=100 on AAPL
- evidence: response has `sigma_used: 3.26464`, `sigma_atm: 3.2646`, `sigma_barrier: 3.0984` (i.e. 326% vol fed to FD-Local-Vol pricer). Repeating with S=100,K=100,B=120 on AAPL yields `sigma_used: 10.81` (1081% vol), `sigma_atm: 10.81`, `sigma_barrier: 10.21` and a nonsensical KO price 0.0666 vs EU 99.00 — these are scaled IVs from raw chain quotes (likely percent-vs-decimal confusion). The `surface_quotes_inverted` count (90/178 or 146/178) is high so the handler trusts the surface.
- suspected: src/data/iv_grid.py / src/data/vol_surface.py — IV inversion is yielding raw percentage values and accepting them as decimal fractions. Sanity bound (e.g. reject any inverted σ > 5.0) is missing. Downstream FD-Local-Vol pricing produces meaningless numbers but is still served as a "calibrated" price.

### [P0] 1MB ticker echoes Windows filesystem path back to client
- repro: `python -c "import json; print(json.dumps({'option_type':'european_call','underlying':'A'*1000000,'spot_price':100,'strike_price':100,'days_to_expiration':90,'volatility':0.2}))" | curl -X POST -H "Content-Type: application/json" --data-binary @- http://localhost:8002/api/price`
- evidence: 400 with body `Pricing failed: [Errno 2] No such file or directory: 'C:\\Users\\rajac\\AppData\\Local\\Temp\\tmpof72af23\\AAA…(1M chars)…'`. The temp dir reveals the runtime user, the OS, and the report-generation strategy (writing per-request temp files keyed on ticker name — concerning for path traversal if a non-alphanumeric ticker is ever accepted). Plus: the 1M-char request is echoed in full in the response body — amplification factor.
- suspected: src/report/generator.py / src/api/handlers.py — the report code uses ticker as path segment without sanitization; OSError detail is passed verbatim into HTTP detail. Also no max length on `underlying` in src/api/models.py.

### [P1] American Put theta is roughly half the FD-confirmed theta (per-day convention violated)
- repro: `POST /api/price {"option_type":"american_put","underlying":"AAPL","spot_price":100,"strike_price":100,"days_to_expiration":90,"volatility":0.2,"risk_free_rate":0.05,"dividend_yield":0.02}`. Then re-price with days_to_expiration=89 and 91; FD theta = -(P(91)-P(89))/2.
- evidence: see `reports/redteam_20260501/greeks_fd_table.csv`. American_put theta_reported=-0.01854 vs theta_fd=-0.03645 — factor of 1.97. European_call agrees (-0.02564 vs -0.02564) and KO_call agrees within 2%, so the bug is American-put-specific. CLAUDE.md asserts theta is per calendar day and "Tests enforce this." Either the test is too weak or this engine path bypasses the convention.
- suspected: src/engines/quantlib_engine.py — American put greeks_qf likely returns theta in years not days, halved by some other scaling, or evaluated at wrong t. Inspect the American put greeks computation.

### [P1] Wildcard rate-limit / queueing: 50 concurrent /api/price serialize and stall ≥96s
- repro: `python -c` 50-thread burst of POST /api/price (vanilla EU call) → all 50 return status=0 (timeout in 30s) and total wall-clock ≈ 96s before backend recovers; subsequent /health is 200 again.
- evidence: in curl_log.jsonl row `step:6.d-rate-limit-50`. /api/market/movers with same 50-thread burst completes in 2.2s p95 (cached), but /api/price has no concurrency cap, no queue limit, no 429 — so a single attacker can stall the API trivially. Confirmed no rate-limit middleware exists.
- suspected: src/api/main.py — no `slowapi`/`fastapi-limiter` mounted. Pricing path holds GIL/QuantLib for ~400ms each; combined with no admission control, this is a one-line DoS.

### [P1] /api/market/risk-free-rate accepts days_to_expiration in {-1, 0, 999999} without validation
- repro: `curl 'http://localhost:8002/api/market/risk-free-rate?days_to_expiration=-1'` (also `=0`, `=999999`)
- evidence: all return 200 with numeric rate (0.0356, 0.0356, 0.0433). Plan-required behavior: 400. Negative days are nonsensical; 999999 days is far beyond any real treasury curve and the endpoint silently extrapolates.
- suspected: src/api/main.py — the `/api/market/risk-free-rate` handler likely reads the query param without `Query(..., gt=0, le=3650)` constraint.

### [P1] POST /api/agent/sessions returns 200 with status="error" when intake_form is malformed
- repro: `curl -X POST -H "Content-Type: application/json" -d '{"intake_form":{"underlying":"SPY","horizon_days":90,"objective":"yield"}}' http://localhost:8002/api/agent/sessions`
- evidence: response is `200 OK`, body `{"session_id":"…","status":"error","message":"Started; current status error."}`. The IntakeAgent rejected the form (missing notional_usd/view/budget_bps_notional, extra "objective"), but the API returned 2xx and persisted a session in `error` status. A naive client will see "Started" + a UUID and proceed. This conflates "session created" with "intake validated" — should be 4xx with the validator errors.
- suspected: src/api/agent_router.py:74-91 (`start_session` returns 200 even when orchestrator's IntakeAgent threw a validation error). Either propagate the validation failure as 422 or return a clearer top-level status code.

### [P1] gate B before gate A is silently ignored (no-op) — orchestrator state hidden from API
- repro: start a fresh session, then immediately `POST /api/agent/sessions/{id}/gate/b {"approved":true}` (skipping A).
- evidence: 200 OK, status remains `awaiting_gate_a`, no error field set. Client cannot tell whether their decision was applied or rejected; idempotent retry is impossible. Per plan we expected either no-op OR 400; bare no-op is the more dangerous choice.
- suspected: src/api/agent_router.py:105-118 (`decide_gate` returns the session view regardless of whether the gate matched current state). Should return 409 Conflict when current state does not allow the requested gate.

### [P2] Empty / unicode / SQLi / null-byte tickers all funneled to identical 400 with raw echo
- repro: `curl 'http://localhost:8002/api/market/spot-price?ticker='`, `?ticker=%E5%A4%A9`, `?ticker=AAPL%00`, `?ticker=AAPL%27%20OR%201%3D1--`
- evidence: each returns 400 with `{"detail":"Could not fetch spot price for ticker <RAW>. Please enter manually."}`. The null byte and the SQLi string are reflected verbatim in the response detail. yfinance does not appear vulnerable, but the echo is sloppy and would be a stored-XSS vector if any caller renders the detail unsanitized into HTML.
- suspected: src/api/main.py / src/api/market_data.py (spot-price handler echoes user-supplied ticker into error detail).

### [P2] /api/agent/sessions/{id}/events stream_close fires before any state events queue
- repro: open SSE stream on a fresh awaiting_gate_a session: `curl --max-time 5 -N http://localhost:8002/api/agent/sessions/{id}/events`
- evidence: stream emits `event: session_created` then `event: stream_close {"reason":"gate"}` and disconnects within ~700ms. CLAUDE.md SSE doc says client can re-subscribe after each gate; the doc is correct, but the eager close means a client that connects right after start sees only the opening session_created event without any of the intake_agent narrative. UX/observability nit, not a crash.
- suspected: src/api/agent_router.py:171-174 (`yield {"event":"stream_close","data":...,"reason":"gate"}` triggers as soon as drain queue empties + status is awaiting_gate_*, before subsequent agent events are produced).

### [P2] No authentication on any endpoint
- repro: every endpoint above served unauthenticated.
- evidence: no `Authorization` header required, no API key, no session cookie scope. Combined with reflective CORS this is fully open to any caller.
- suspected: by design (single-user dev tool) — but should be documented + behind a flag for any non-localhost deploy.


---

## Cross-references / coordination notes

<!-- either agent may add here when a finding spans both surfaces -->
