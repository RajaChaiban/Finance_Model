---
agent: StrategistAgent
role: user_template
source: src/agents/strategist.py:173-203 (_build_polish_prompt)
filled_by: Python f-strings inside _build_polish_prompt
inputs:
  - obj: ClientObjective (Intake output)
  - regime: MarketRegime (built by orchestrator after Gate A)
  - candidates: list[Candidate] (rules-table output, before polish)
---

# User-prompt template

The Strategist sends the system prompt above plus the user message below.
The user message is **assembled in Python** by `_build_polish_prompt`
(strategist.py:173). Curly-brace tokens below are f-string substitutions —
not literal text the LLM sees.

```text
OBJECTIVE:
underlying={obj.underlying} notional_usd={obj.notional_usd:,.0f} view={obj.view} horizon_days={obj.horizon_days} budget_bps={obj.budget_bps_notional} premium_tol={obj.premium_tolerance} capped_upside_ok={obj.capped_upside_ok} barrier_ok={obj.barrier_appetite}

REGIME:
spot={regime.spot} q={regime.dividend_yield} r={regime.risk_free_rate} realised_vol_30d={regime.realised_vol_30d} vol_regime={regime.vol_regime} earnings_proximity={regime.earnings_proximity}

CANDIDATES:
- candidate_id={c.candidate_id} kind={c.kind.value} name="{c.name}" legs=({legs_str}) draft_rationale="{c.rationale}"
- candidate_id=...
- candidate_id=...
```

## Per-leg formatting (`legs_str`)

Each candidate's legs are joined with `; ` separators. Per leg:

```text
{l.option_type} K={l.strike}[ B={l.barrier_level}] qty={l.quantity:+.0f}
```

The `B={...}` segment is included only when `l.barrier_level is not None`.
`qty` is signed (e.g. `+1` long, `-1` short).

## Worked example (illustration only, not a fixture)

```text
OBJECTIVE:
underlying=SPY notional_usd=10,000,000 view=protect_gains horizon_days=90 budget_bps=50 premium_tol=low capped_upside_ok=True barrier_ok=False

REGIME:
spot=605.12 q=0.014 r=0.043 realised_vol_30d=0.142 vol_regime=normal earnings_proximity=None

CANDIDATES:
- candidate_id=c1 kind=collar name="Zero-cost collar" legs=(european_put K=580.0 qty=+1; european_call K=625.0 qty=-1) draft_rationale="Funds put with capped upside; common for protect_gains."
- candidate_id=c2 kind=put_spread name="50d put spread" legs=(european_put K=580.0 qty=+1; european_put K=550.0 qty=-1) draft_rationale="Cheaper than outright put; tail risk uncapped below lower strike."
- candidate_id=c3 kind=knockout name="Down-and-out put" legs=(knockout_put K=580.0 B=540.0 qty=+1) draft_rationale="Cheapest hedge but knocks out at 540 — not for crash protection."
```
