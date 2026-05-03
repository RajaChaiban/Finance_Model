---
component: MarketIntelligence (RAG layer)
role: user_template
source: src/agents/market_intelligence.py:401 (PromptManager.TEMPLATE_DEAL_INTELLIGENCE)
filled_by: market_intelligence.py:748 (query_deal_analysis)
paired_system: prompts/market_intelligence/system_deal_analysis.md
calling_agent: ValidatorAgent.validator.py:107
placeholders:
  - "{asset_class}"   — ticker
  - "{deal_summary}"  — single deal dict formatted by format_deal_data (line 415)
  - "{comparables}"   — list of corpus deals formatted by format_comparables (line 420)
note: |
  Output is parsed lightly to detect "no precedent" / "outlier" language and
  emit a WARN ValidatorFinding. The literal corpus-comparison answer is also
  appended to session.market_context for the Narrator's citations section.
---

```text
Analyse the following {asset_class} option structure and provide market-context intelligence:

Deal / Structure Summary:
{deal_summary}

Corpus Comparables:
{comparables}

Provide:
1. Where this structure sits in the corpus — is it typical, an outlier, or with no comparable / no precedent?
2. Hedging and execution risks (pin, gap, vega, correlation)
3. Likely investor / counterparty type
4. Pricing posture vs. comparables
5. Key risks and mitigants
```
