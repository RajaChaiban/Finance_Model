---
component: MarketIntelligence (RAG layer)
role: user_template
source: src/agents/market_intelligence.py PromptManager.TEMPLATE_PRICING_BENCHMARK
filled_by: market_intelligence.py:716 (query_pricing) via str.format(...)
paired_system: prompts/market_intelligence/system_pricing.md
calling_agent: PricingAgent.pricing.py:75
placeholders:
  - "{asset_class}"        — ticker
  - "{tranche_type}"       — structure_kind (e.g. collar, put_spread, knockout_put)
  - "{comparable_deals}"   — formatted comparables block (format_comparables)
  - "{market_conditions}"  — formatted market-data block (format_deal_data)
note: |
  Called AFTER the QuantLib pricing run, not before. Output decorates the
  model price in the memo — it does not change the price itself.
---
Based on recent comparable prints and listed reference levels, provide pricing benchmarks for a {tranche_type} structure on {asset_class}:

Comparable Trades:
{comparable_deals}

Current Market Conditions:
{market_conditions}

Provide:
1. Current pricing range in bps of spot (or premium-of-notional)
2. Historical context where the corpus supports it
3. Key pricing drivers — skew, term structure, barrier proximity, hedging-cost premium
4. Range of plausible outcomes
5. Confidence level (high/medium/low) and what would tighten it
