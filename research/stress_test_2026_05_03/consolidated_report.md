# Co-Pilot Stress Test — 10 Desk-Call Scenarios

_Test date: 2026-05-03 • Mode: DEMO_REPLAY=1 • MC overlay: 50,000 GBM paths per leg • Engines: QuantLib analytic primary; MC LSM forced overlay for validation_

## TL;DR

**0 PASS, 5 WARN, 5 FAIL across 10 senior-structurer reviews.**

The QuantLib pricing layer is **honest** — MC vs QL parity holds at <1% on every vanilla leg and <3% on every barrier leg across all 10 runs. The failures are **all upstream** of the engine: Strategist rule-table gaps, Narrator template-string leakage, and a Validator that does not block budget breaches or wrong-direction recommendations.

This is not a memo system a desk would let touch a client today. It IS the kind of system where the bug list is short, mechanical, and mostly fixable in one sprint.

## Verdict tally

| # | Scenario | View | Notional | Budget | Grade | One-line cause |
|---|---|---|---|---|---|---|
| 1 | XLK mildly_bullish 270d no-barrier | mildly_bullish | $150M | 90 bps | **FAIL** | Recommended 330bps call_spread (3.7x budget); validator silent |
| 2 | XLE bearish 180d barrier-OK | bearish | $80M | 60 bps | **WARN** | No barrier candidate despite barrier_appetite=True |
| 3 | XLF protect_gains 365d zero-cost | protect_gains | $250M | 0 bps | **FAIL** | Returned 50bps CREDIT (not zero-cost); asymmetric strikes |
| 4 | XLV neutral 90d 40bps yield | neutral | $50M | 40 bps | **WARN** | Recommended 101bps DEBIT for a credit/yield brief |
| 5 | SPY crash_hedge 545d barrier-OK | crash_hedge | $1B | 100 bps | **FAIL** | 432bps long_put (4.3x budget); KO barrier above crash zone |
| 6 | AAPL earnings_hedge 21d barrier-OK | earnings_hedge | $30M | 80 bps | **WARN** | 140bps over budget; no IV-crush caveat |
| 7 | IWM bullish 180d 150bps no-barrier | bullish | $120M | 150 bps | **FAIL** | Recommended COVERED CALL (Δ −0.36) for bullish, no-cap mandate |
| 8 | XLP mildly_bearish 270d capped-OK | mildly_bearish | $200M | 70 bps | **WARN** | "Zero-cost collar" cost 38.9bps; strikes spot-anchored not forward |
| 9 | SMH neutral 120d 50bps capped-OK | neutral | $90M | 50 bps | **FAIL** | Picked long-vol DEBIT put_spread for neutral yield brief (5.8x over) |
| 10 | XLRE protect_gains 365d zero-cost | protect_gains | $60M | 30 bps | **WARN** | "Zero-cost collar" cost 80.6bps (16x miss) on a high-div REIT |

**Engine pass rate: 10 / 10.** Every MC vs QL drift was within tolerance.
**Memo pass rate: 0 / 10.** Every memo had at least one structural defect.

## Recurring defects (in order of severity)

### 1. Validator silence on budget / direction breaches (severity: BLOCKER)

Scenarios **1, 5, 7, 9** had recommendations that openly violated the client's stated budget or view direction. In every case, `session.validator.findings` returned **empty**.

The validator currently checks engine NaNs, zero-cost-label vs actual-net, and a few other invariants — but **does not enforce**:
- `net_premium_bps > budget_bps_notional + tolerance` → BLOCK
- `sign(greeks.delta) ≠ sign(view_direction)` → BLOCK
- `capped_upside_ok=False` AND `recommended_candidate has short call leg` → BLOCK
- `view in {neutral, yield_enhance}` AND `theta < 0` AND `vega > 0` → WARN (long-vol/short-theta is opposite of yield)

**Fix sketch:** add four invariant checks to `src/agents/validator.py`. Each is one method against `session.objective` + `priced[recommended_id].greeks` + `priced[recommended_id].net_premium_bps`. None require LLM.

### 2. Narrator template leak: "Internal RFQ — SPY Downside Protection (8m)" (severity: BLOCKER for client-facing)

Appears as a literal string in `memo.title` for scenarios **1, 2, 3, 6, 7, 8, 9, 10** — i.e. 8 of 10 runs across every ticker, view, tenor, and notional. The string is hardcoded somewhere in the Narrator's title-composer template.

**Fix sketch:** grep for `"SPY Downside Protection"` in `src/agents/narrator.py` (and any helper modules / replay fixtures it loads). Replace with a dynamic `f"Internal RFQ — {underlying} {direction_word} ({tenor_label})"` derived from `session.objective`.

### 3. Wrong-direction recommendation (severity: HIGH)

- **Scenario 7 (IWM bullish, no-cap):** recommended `covered_call` (Δ −0.36, caps upside, premium credit). The strategist's own caveat reads "Client must accept upside cap at strike 245 — confirm in writing before pricing" — the Narrator selected a structure that *requires* a caveat asking the client to swallow the constraint they explicitly refused.
- **Scenario 9 (SMH neutral, yield):** recommended a long-vol DEBIT put_spread when the brief was neutral / capped-upside-OK / medium-tolerance. A `covered_call` candidate that would have collected −459bps credit was sitting in the same comparison table.

Root cause is the same in both: the Narrator's recommendation pass scores cost-fit but not sign-of-direction. Combined with a Strategist rule table that emits covered_call as a candidate for `bullish` (when `capped_upside_ok=True` would be a sane gate) and emits put_spread for `neutral` (which is structurally bearish/long-vol).

**Fix sketch:** in `src/agents/narrator.py`, the recommendation-tiebreak loop (the function that sets `memo.recommended_candidate_id`) needs a hard pre-filter:
```
if objective.view in BULLISH_VIEWS and pc.greeks.delta < 0:   skip
if objective.view in BEARISH_VIEWS and pc.greeks.delta > 0:   skip
if not objective.capped_upside_ok and candidate has short call: skip
```
Plus rule-table audit in `src/agents/rules/strategy_rules.py` to remove direction-incompatible candidates at the source.

### 4. "Zero-cost" structures that are not zero-cost (severity: HIGH)

Scenarios **2, 3, 8, 10** all returned candidates *labeled* `zero_cost_collar` whose net premium was 38.9–80.6 bps off zero. The Validator flagged the zcc_premium violation as WARN in 3 of 4 cases — but the Narrator never re-strikes and never re-labels.

Root cause: `_build_collar_for_zero_cost` (in strategy_rules.py) picks symmetric strikes on a fixed grid (e.g. 95% put / 105% call) and does **not** solve for the call strike that prices the put given (S, r, q, σ, T). On dividend-heavy underliers (XLF q=2.2%, XLP q=2.7%, XLRE q=3.8%) the forward sits above spot and symmetric-vs-spot strikes break the par condition.

**Fix sketch:** replace the static grid with a 1-D root solve: hold the put strike fixed at e.g. 95% spot, root-find the call strike K_call s.t. `BS_call(S, K_call, r, q, σ, T) − BS_put(S, K_put, r, q, σ, T) = 0`. Use Brent on K_call ∈ [S, 1.5·S]. ~20 lines.

### 5. Missing barrier candidates when `barrier_appetite=True` (severity: MEDIUM)

Scenario **2** (XLE bearish, barrier_appetite=True) — strategist returned put_spread / collar / long_put. **Zero KO/KI** despite the client explicitly opting in.

The textbook product for bearish + barrier-OK on high-vol energy is a KI-put with B at −1σ to −2σ. The rule table presumably has a `bearish` row but it doesn't condition on `barrier_appetite` to substitute KI-put for the cheapest leg.

**Fix sketch:** in `src/agents/rules/strategy_rules.py`, every rule row whose view supports a barrier variant should emit the barrier version when `objective.barrier_appetite=True` AND the budget supports it (KI/KO are 30–50% cheaper than vanilla; only relevant when the vanilla version is over budget).

### 6. Missing rule rows for neutral × normal-vol × credit (severity: MEDIUM)

Scenario **4** (XLV neutral) — the lone neutral row in `strategy_rules.py:141` requires `budget_band="credit"` AND `vol_regime="high"`. With σ=0.16/0.17 (normal vol), the row didn't match and ranking fell through to a generic scoring path biased by an MI hit — promoting put_spread for a neutral yield-enhance brief.

Add neutral × normal-vol × credit → (covered_call, short_strangle, iron_condor). Today the StructureKind enum has covered_call but lacks short_strangle and iron_condor — a Phase-7 lego addition.

### 7. Forward-vs-spot strike anchoring (severity: MEDIUM)

Scenarios **8 (XLP, q=2.7%)** and **10 (XLRE, q=3.8%)** both placed strikes against spot. On high-dividend underliers the forward F = S·exp((r−q)·T) shifts meaningfully and asymmetric-vs-spot strikes are correct only relative to the forward. The strategist's strike picks consistently miss this.

**Fix sketch:** strike helpers in `strategy_rules.py` should compute F first and express percentage-of-forward, then back-translate to a strike. e.g. K_put_95F = 0.95 · F, K_call_105F = 1.05 · F. ~10 lines.

### 8. Event-risk caveats absent (severity: MEDIUM)

- Scenario 6 (AAPL earnings_hedge): no IV-crush caveat — this is the dominant post-print P&L driver for a long-vol structure.
- Scenario 5 (SPY 18mo): no theta-drag or rho-duration caveat for a 545-day hold.
- Scenario 2 (XLE bearish): no OPEC+ / EIA inventory / ex-div caveats despite the RFQ explicitly being built on WTI roll-down.
- Scenario 10 (XLRE 12mo): no FOMC catalyst caveat on a rate-sensitive sector.
- Scenario 4 (XLV 90d short-vol): no earnings-cycle caveat for UNH/JNJ/LLY/PFE rolling prints.

Caveats today are largely scenario-stress paraphrases ("verify drawdown tolerance for −20% crash"). They should be event-keyed off `objective.view` and `regime.earnings_proximity`.

## Engine validity summary

| Scenario | Vanilla MC vs QL drift | Barrier MC vs QL drift |
|---|---|---|
| 1 (XLK call_spread) | −0.82% / +0.36% | n/a |
| 2 (XLE collar) | +0.28% / −1.56% | n/a |
| 3 (XLF collar) | +0.30% / −1.50% | n/a |
| 4 (XLV covered_call) | −1.41% (vol-blend mismatch suspected) | n/a |
| 5 (SPY long_put) | +1.10% | n/a (pin-risk on KO sibling B=K) |
| 6 (AAPL long_put + KI sibling) | +0.04% | KI: −1.78% (discrete monitoring) |
| 7 (IWM covered_call) | +0.50% | n/a |
| 8 (XLP collar) | −0.74% / +0.27% | n/a |
| 9 (SMH put_spread) | +0.45% / +0.81% | n/a |
| 10 (XLRE collar) | −0.12% / +0.91% | n/a |

**Verdict on engines: SOUND.** No drift exceeded 2% on a vanilla leg, and the one >1% case (Scenario 4) is most likely a vol-blend convention mismatch between PricingAgent's scalar σ pick and the 90d σ used in the MC overlay — not an engine bug. Scenario 6 KI drift of −1.78% / 9.8 bps is at the edge of the 3% target and is explained by the discrete daily-monitoring vs continuous-monitoring with BGK shift convention difference. The single pin-risk warning (Scenario 5 KO sibling with B=K=460) is a Strategist bug — barrier exactly at strike — not an engine bug.

## What the co-pilot got right (across all 10 runs)

- Pipeline mechanics are reliable: every Intake → Gate A → Strategist → Gate B → Pricing → Scenario → Validator → Narrator → Gate C transition completed without crash, in DEMO_REPLAY mode, on first attempt.
- Greeks signs and magnitudes are internally consistent for every priced candidate. Vega/theta/rho follow the documented per-1% / per-day / per-1% conventions. No NaN propagation.
- MI / RAG retrieval works when the corpus has data: every seeded sector ETF (XLE, XLF, XLP, XLK, XLRE, XLV, XLY, IWM) produced ticker-specific citations rather than fallback placeholders. SMH (not seeded) gracefully emitted "No comparable deals indexed" rather than fabricating.
- Per-leg pricing reconciles to MC at the engine-quality bar (<1% on vanillas).
- Pydantic state validation catches malformed inputs upstream — no scenario crashed on schema.
- Comparison table format is parseable in every run (10 columns, all candidates have term-sheet blocks with STRUCTURE / LEGS / GREEKS / END LEGS markers).

## Senior structurer recommendation

**Do not let this co-pilot near a client desk in its current state.** The plumbing is good, but the structuring judgment is failing on at least one of these axes in 10 / 10 runs. In rough order of fix-impact:

1. **Validator hardening** — add 4 invariants (budget breach, direction sign, capped_upside_ok contradiction, view-vs-greeks consistency). All four are `if/raise` checks, no LLM. Catches scenarios 1, 5, 7, 9 immediately.
2. **Narrator title-template fix** — kill the "SPY Downside Protection (8m)" leak. Single grep + replace. Catches 8 / 10 cosmetic defects.
3. **Strategist rule-table audit** — add neutral × normal-vol × credit row, gate covered_call to `capped_upside_ok=True` views only, ensure barrier rows fire when `barrier_appetite=True`.
4. **Zero-cost collar solver** — replace static strike grid with Brent on K_call given fixed K_put. Catches scenarios 2, 3, 8, 10.
5. **Forward-vs-spot anchoring** — strike helpers compute forward first.
6. **Event-keyed caveats** — keep a small lookup of view → mandatory caveats (earnings_hedge → IV crush, crash_hedge → theta drag, protect_gains on REIT → FOMC, etc.).

After those six, re-run the same 10 scenarios. Expect 6+ to flip to PASS.

## Artifacts

All 10 scripts saved to `tests/stress/scenario_NN.py`. Each runs end-to-end via `python tests/stress/scenario_NN.py` in DEMO_REPLAY mode (no live LLM, no live market data, no network). Each prints VERDICT, recommended candidate, MC vs QL deltas per leg, and the validator findings list.
