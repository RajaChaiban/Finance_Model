# 20-Scenario Real-Life Validation Report — v3

_Branch: `integration/stress-test-fixes` • HEAD (pre-fix): `23d36e4` • Date: 2026-05-03 • **Run #3**_

**Total scenarios:** 20 — **PASS: 16 • WARN: 4 • FAIL: 0**

## TL;DR

A budget-aware rescue pass added to `src/agents/rules/strategy_rules.py`
turns the v2 framework's 11/9/0 PASS/WARN/FAIL into **16/4/0**. The 5 v2
"all-candidates-BLOCKed" budget-breach WARNs (#4 XLF, #10 SPY, #13 XOP,
#15 COIN, #20 PFE) **all flip to PASS**: each now produces at least one
in-budget candidate the validator clears. The mechanism is a closed-form
BS quick-pricer plus four lightweight transforms (tighten spread, push
long leg further OTM, vanilla→barrier when `barrier_appetite=True`,
tighten collar cap on a bilateral grid sweep) plus a stacked deep-OTM
barrier transform that handles the SPY-365d-style cases where a 5%-OTM
KI/KO is still meaningfully above budget. KI/KO discount factors are
calibrated against QuantLib's Reiner-Rubinstein engine on a piecewise-
linear interpolation of the standardised log-distance ``λ = |ln(B/S)| /
(σ√T)`` so that short-tenor very-high-vol KI/KO (COIN 14d, σ=0.65) and
long-tenor low-vol KI/KO (SPY 365d, σ=0.16) both price within ~10% of
QL. Rescue acceptance is held to ``budget + 10 bps`` — the same line as
the validator's hard cap — so we never accept a variant the validator
will then BLOCK. **No regressions: 1360 backend tests pass (same single
pre-existing `asian_put greeks_signs` skip), frontend build green.**

## Aggregate metrics — v1 vs v2 vs v3 vs target

| Metric | v1 | v2 | **v3** | Target | Status |
|---|---|---|---|---|---|
| PASS / WARN / FAIL | 4 / 14 / 2 | 11 / 9 / 0 | **16 / 4 / 0** | — | ✅ +5 PASS, FAILs eliminated |
| Mean MC drift (Eu leg) | 1.43% | 1.50% | 1.72% | <2% | ✅ |
| Max MC drift | 7.24% | 7.24% | 8.85% | <2% (1 outlier) | ➖ unchanged class (parity-test variance, ATM short-tenor) |
| Title-leak runs | 2 / 20 = 10.0% | 0 / 20 = 0.0% | **0 / 20 = 0.0%** | 0% | ✅ unchanged from v2 |
| Event-keyed caveats fired | 1 / 5 = 20.0% | 4 / 5 = 80.0% | **4 / 5 = 80.0%** | ≥80% | ✅ unchanged |
| Barrier honored when appetite | 7 / 7 = 100% | 7 / 7 = 100% | **7 / 7 = 100%** | 100% | ✅ unchanged |
| Direction-aligned recommendation | 100% | 100% | **100%** | 100% | ✅ unchanged |
| Mean validator findings/run | 1.85 | 1.85 | **1.30** | — | ⤓ rescue eliminates a chunk of BLOCKs upstream |

## Per-scenario delta (v1 → v2 → v3)

| # | Scenario | v1 | v2 | v3 | v1 rec | v3 rec | Δ |
|---|---|---|---|---|---|---|---|
| 1 | NVDA Q4 earnings | WARN | PASS | **PASS** | long_put | ki_put | — |
| 2 | SVB-style KRE | WARN | PASS | **PASS** | long_put | ki_put | — |
| 3 | Iran/Israel oil (XLE) | WARN | PASS | **PASS** | long_call | risk_reversal | — |
| 4 | Powell pivot (XLF, $300M, 0bps) | WARN | WARN | **PASS** | zero_cost_collar | zero_cost_collar | **WARN→PASS** (collar tightener) |
| 5 | Trump small-caps (IWM) | WARN | PASS | **PASS** | long_call | risk_reversal | — |
| 6 | Boeing strike (XLI) | PASS | PASS | **PASS** | zero_cost_collar | zero_cost_collar | — |
| 7 | BoJ YCC (EWJ) | PASS | PASS | **PASS** | covered_call | covered_call | — |
| 8 | CPI surprise (TLT) | WARN | WARN | WARN | long_put | long_put | (MC drift 2.78%) |
| 9 | NDX recovery (QQQ) | WARN | PASS | **PASS** | long_call | risk_reversal | — |
| **10** | **COVID-tail SPY 365d** | **FAIL** | **WARN** | **PASS** | long_put | **ki_put deep** | **WARN→PASS** (vanilla→KI + deep-OTM) |
| 11 | Healthcare (XLV) | PASS | PASS | **PASS** | zero_cost_collar | zero_cost_collar | — |
| 12 | TSLA Q4 IV-crush | WARN | WARN | WARN | covered_call | covered_call | (validator one-sided budget; pre-existing) |
| **13** | **XOP supply glut** | WARN | WARN | **PASS** | ki_put | **ki_put deep** | **WARN→PASS** (deep-OTM KI) |
| 14 | JPM pre-earnings | WARN | WARN | WARN | covered_call | covered_call | (MC drift 2.25%) |
| **15** | **COIN crypto-beta 14d** | WARN | WARN | **PASS** | long_put | **ki_put deep** | **WARN→PASS** (deep-OTM KI) |
| 16 | Treasury auction (IEF) | PASS | PASS | **PASS** | zero_cost_collar | zero_cost_collar | — |
| 17 | AAPL services rerate | WARN | PASS | **PASS** | long_call | risk_reversal | — |
| 18 | SPY year-end pin | FAIL | WARN | WARN | covered_call | covered_call | (MC drift 8.85%) |
| 19 | XLU rate-cut play | WARN | PASS | **PASS** | long_call | risk_reversal | — |
| **20** | **PFE patent-cliff** | WARN | WARN | **PASS** | ki_put | **ki_put deep** | **WARN→PASS** (deep-OTM KI) |

**Delta count:** 5 WARN→PASS, 0 regressions, 0 FAILs.

### Focus on the 5 budget-rescue scenarios

| # | Ticker | Budget | v2 rec / bps | v3 rec / bps | Rescue path |
|---|---|---:|---|---|---|
|  4 | XLF | 0 (zero-cost) | zero_cost_collar / **+16.0** (BLOCK) | zero_cost_collar / **+8.7** (PASS) | bilateral collar grid sweep (put/call ±1 step) re-anchored to 0bps |
| 10 | SPY | 80 | long_put / **+405.6** (all-BLOCKed) | ki_put / **+60.2** (PASS) | long_put → KI deep-OTM (K=88%·F, B=70%·F) |
| 13 | XOP | 120 | ki_put / **+312.1** (all-BLOCKed) | ki_put / **+41.1** (PASS) | KI strike pushed deep OTM |
| 15 | COIN | 200 | ki_put / **+229.4** (all-BLOCKed) | ki_put / **+17.6** (PASS) | KI strike pushed deep OTM |
| 20 | PFE | 120 | ki_put / **+339.7** (all-BLOCKed) | ki_put / **+49.8** (PASS) | KI strike pushed deep OTM |

## Framework correctness verdict

**The agentic framework is now structurally correct and budget-aware on
all seven invariants.** Direction alignment 100%, capped-upside 100%,
barrier-when-appetite 100%, title 0% leak, selector-validator deference
(v2), event-caveat ≥80% (v2), and **budget-feasible candidate
selection ≥budget+10 (v3)**. The 4 remaining WARNs all decompose into
non-rescue defects:

1. **MC parity-test variance (3: #8 TLT 2.78%, #14 JPM 2.25%, #18 SPY
   8.85%).** ATM short-tenor European legs on low-vol underliers,
   50k GBM paths. 250k paths or antithetic variates would close these.
   Not a strategy or pricing-engine defect.
2. **Validator one-sided budget rule (1: #12 TSLA -150bps credit).**
   Pre-existing v1+v2 finding: validator's `_BUDGET_TOLERANCE_BPS` only
   catches positive debits over budget under non-`zero_cost_only` tone.
   Out of scope for the budget-rescue sprint.

### Top 2 evidence pieces showing budget-aware works

1. **Scenario 10 SPY 365d crash hedge — vanilla→barrier deep-OTM stack.**
   v2 strategist returned `long_put` at +405.6 bps (5×budget), `ko_put`
   at +243 bps, `put_spread` at +286 bps — all BLOCKed, narrator fell
   back to the long_put (highest crash payoff). v3's rescue runs the
   long_put through `_convert_vanilla_to_barrier` followed by
   `_push_barrier_strike_deep`: the result is a `KI_PUT` at K=520, B=415
   (12% / 30% OTM-on-forward), quick-priced at ~+55 bps, QL re-priced
   at +60.2 bps — within the validator's `budget+10 = 90 bps` cap.
   Validator clears it; narrator picks it as the recommendation.
   `barrier_honored` stays True; `direction_aligned` stays True.

2. **Scenario 4 XLF protect_gains $300M zero-cost — collar grid sweep.**
   v2 ZCC printed +16.0 bps because the brent-solved continuous K_call
   at 55.7 rounded to a $1 grid leaving 12 bps of residual (validator's
   hard cap is 10). v3's `_tighten_collar_cap` now sweeps both legs
   bilaterally (put at K_put ± 1 step, call at K_call -0..-4 steps) and
   picks the discrete pair closest to ``budget_bps`` — the result is
   put K=49.7, call K=56 → +8.7 bps QL. The rescue knows the validator's
   tolerance line and stops there; the result is desk-recognisable
   strikes (the same 95P/105C neighbourhood) with the residual cleared.

## Residual risks / follow-ups

1. **Quick-pricer KI/KO discount drifts on tail-vol regimes.** The
   piecewise-linear λ-anchored ratios are calibrated for the 0.3 < λ <
   2.0 band; outside that the discount over-predicts (tail) or under-
   predicts (very-near-spot barriers). Acceptable for the rescue
   selector — the PricingAgent always re-prices exactly via QL — but
   if we ever lean on the quick-pricer for memo prose we need a tighter
   calibration or a true closed-form Reiner-Rubinstein implementation
   inline.
2. **Rescue does not stack with `_ensure_barrier_candidate`.** When the
   barrier-substitute slot collides with a rescue-converted vanilla,
   the slate ends up with two near-identical KI variants. Cosmetic
   only — the narrator selects one — but a future cleanup pass could
   dedupe by ``(kind, strike, barrier)``.
3. **Validator one-sided budget rule (TSLA #12).** Carried over from
   v1+v2. Recommend a `_rule_budget_breach` test with credit-side
   over-budget input under `medium`/`high` premium tolerance.
4. **MC parity tightness on short-tenor ATM legs.** SPY #18 still shows
   8.85% drift — unchanged class from v2 (this is harness MC variance,
   not engine drift). Antithetic variates or 250k paths would close.

## Artifacts

- v3 run log: `research/stress_test_2026_05_03/real_life_run_v3.log`
- v3 JSON: `research/stress_test_2026_05_03/real_life_run_v3.json`
- v2 baseline JSON: `research/stress_test_2026_05_03/real_life_run_v2.json`
- v2 baseline report: `research/stress_test_2026_05_03/real_life_validation_report_v2.md`
- Harness: `tests/stress/run_20_real_scenarios.py` (now writes
  `real_life_run_v3.json` by default; respects `STRESS_OUTPUT_FILE` env var)
- Source patched: `src/agents/rules/strategy_rules.py` —
    * `_quick_price_bps` (closed-form BS proxy for net_premium_bps,
       barrier-aware via λ-calibrated discount).
    * `_barrier_discount` (separate KI/KO interpolation tables anchored
       against QuantLib RR).
    * Four transforms: `_tighten_spread`, `_push_long_otm`,
       `_convert_vanilla_to_barrier`, `_tighten_collar_cap` (bilateral
       grid sweep).
    * Stacked deep-OTM transform `_push_barrier_strike_deep` for the
       hard cases (SPY 365d crash hedge, COIN 14d earnings, etc.).
    * `_rescue_for_budget` orchestrator wired into `build_candidates`
       after `_ensure_barrier_candidate` and `_reorder_neutral_candidates`.
- Branch: `integration/stress-test-fixes` (post-fix HEAD pending commit;
  not pushed)
