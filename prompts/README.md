# Agent prompts

Read-only mirror of every LLM prompt used by the multi-agent structuring co-pilot.
Prompts live in Python source as string constants and template builders — these
`.md` files document them verbatim for human review and editing reference.

> **Source of truth is the code**, not these files. If you change a prompt,
> edit it in `src/agents/...` and resync the matching `.md` here in the same
> commit so the two stay aligned.

## Layout

```
prompts/
├── intake/
│   └── system.md                       Intake — NL RFQ → JSON ClientObjective
├── strategist/
│   ├── system.md                       Strategist — polish 3 candidate rationales
│   └── user_template.md                  ↳ user prompt assembled per session
├── narrator/
│   ├── system.md                       Narrator — 3-way comparison memo
│   └── user_template.md                  ↳ user prompt assembled per session
└── market_intelligence/                RAG layer (class PromptManager)
    ├── system_market_intelligence.md   General market Q&A + market-window
    ├── system_pricing.md               Model price vs. comparable trades
    ├── system_deal_analysis.md         Deal analysis vs. corpus precedents
    ├── template_market_window.md         ↳ filled by query_market_window
    ├── template_pricing_benchmark.md     ↳ filled by query_pricing
    └── template_deal_intelligence.md     ↳ filled by query_deal_analysis
```

## Which agents use prompts?

| Agent | LLM? | Prompts |
|---|---|---|
| Orchestrator | no | — (state machine only) |
| **Intake** | yes (NL path) | `intake/system.md` (raw RFQ is the user message — no template) |
| **Strategist** | yes (rationale polish) | `strategist/system.md` + `strategist/user_template.md` |
| Pricing | **no** | — calls QuantLib engines directly |
| Scenario | **no** | — deterministic P&L grid |
| Validator | **no** | — parity / no-arb rules + `rules/strategy_rules.py` |
| **Narrator** | yes (memo polish) | `narrator/system.md` + `narrator/user_template.md` |
| RAG layer | yes (Gemini via existing client) | 3 system prompts + 3 user templates in `market_intelligence/` |

Of the 7 agents, only **3 specialists** call an LLM directly (Intake, Strategist,
Narrator). The RAG layer (`src/agents/market_intelligence.py`) is a separate
component that 5 agents consult — it has its own prompt set.

## Frontmatter convention

Each `.md` begins with a YAML block recording:

- `agent` — which agent (or RAG method) issues the call
- `role` — `system` or `user_template`
- `source` — exact file:line in the codebase
- `model_tier` — fast vs. smart, with the env-var override name
- `mode` — `json` or `text`
- `called_at` — where in the source the prompt is sent to the LLM

User-template files document the **f-string placeholders** the Python code
fills in at runtime — e.g. `{underlying}`, `{notional_usd}`, `{spot}`. The
exact assembly logic lives in `_build_polish_prompt` /
`_build_narrator_prompt` / the RAG `query_*` methods.

## Editing checklist

When you change a prompt, update both sides:

1. Edit the constant or template in `src/agents/<file>.py`.
2. Edit the matching `.md` here so the two read identically.
3. Run `pytest tests/test_agents_smoke.py` to confirm the replay fixture
   still parses (replays in `tests/fixtures/demo_replay.json` are matched
   on `replay_key`, not on prompt text — but a wording change can still
   shift LLM JSON output enough to fail downstream pydantic parsing).
4. If you renamed a prompt constant, also grep for stale references in
   `tests/`.
