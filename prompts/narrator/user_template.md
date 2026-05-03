---
agent: NarratorAgent
role: user_template
source: src/agents/narrator.py:449-482 (_build_narrator_prompt)
filled_by: Python f-strings inside _build_narrator_prompt
inputs:
  - memo: MemoArtifact (deterministic skeleton)
  - session: StructuringSession with priced candidates + scenarios
---

# User-prompt template

The Narrator sends the system prompt above plus the user message below.
The user message is **assembled in Python** by `_build_narrator_prompt`
(narrator.py:449). Curly-brace tokens are f-string substitutions, not
literal text.

```text
OBJECTIVE:
underlying={obj.underlying} notional={obj.notional_usd:,.0f} view={obj.view} horizon_days={obj.horizon_days} budget_bps={obj.budget_bps_notional} premium_tol={obj.premium_tolerance} capped_upside_ok={obj.capped_upside_ok} barrier_appetite={obj.barrier_appetite}

CANDIDATES:
- candidate_id={pc.candidate.candidate_id} kind={pc.candidate.kind.value}
  name={pc.candidate.name}
  rationale={pc.candidate.rationale}
  premium={pc.net_premium_bps:+.1f}bps (${pc.net_premium:+,.0f})
  greeks: Δ={pc.greeks.delta:+.3f} Γ={pc.greeks.gamma:.4f} V={pc.greeks.vega:+.2f} Θ={pc.greeks.theta:+.3f} DV01={pc.greeks.dv01:+.4f}
  scenarios:
    {r.name}: spot {r.spot_shock_pct:+.0%}, vol {r.vol_shock_pct:+.0%} -> P&L ${r.pnl_usd:+,.0f} ({r.pnl_pct_notional:+.2%})
    ...
- candidate_id=...
- candidate_id=...
```

## Notes on assembly

- One block per `PricedCandidate` in `session.priced` (always 3).
- The `scenarios:` subsection is included only if a `ScenarioReport` exists
  for that candidate_id (looked up via `scenarios_by_id`). If empty, the
  whole `scenarios:\n...` line is omitted from that block.
- Greek labels use Unicode (Δ Γ V Θ) and the price/Greek conventions from
  `architecture.md` (vega per 1% σ, theta per calendar day).
- `pc.net_premium_bps` and `pc.net_premium` are signed: positive = client
  pays, negative = client receives premium (credit structure).

## Worked example (illustration only)

```text
OBJECTIVE:
underlying=SPY notional=10,000,000 view=protect_gains horizon_days=90 budget_bps=50 premium_tol=low capped_upside_ok=True barrier_appetite=False

CANDIDATES:
- candidate_id=c1 kind=collar
  name=Zero-cost collar
  rationale=Funds put with capped upside; common for protect_gains.
  premium=+0.0bps ($+0)
  greeks: Δ=-0.054 Γ=0.0021 V=+18.42 Θ=-0.012 DV01=+0.0034
  scenarios:
    -10% spot: spot -10%, vol +20% -> P&L $-145,000 (-1.45%)
    flat: spot +0%, vol +0% -> P&L $0 (+0.00%)
    +10% spot: spot +10%, vol -10% -> P&L $+250,000 (+2.50%)
- candidate_id=c2 ...
- candidate_id=c3 ...
```
