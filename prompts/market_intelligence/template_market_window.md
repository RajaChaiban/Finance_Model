---
component: MarketIntelligence (RAG layer)
role: user_template
source: src/agents/market_intelligence.py PromptManager.TEMPLATE_MARKET_WINDOW
filled_by: market_intelligence.py:675 (query_market_window) via str.format(...)
paired_system: prompts/market_intelligence/system_market_intelligence.md
calling_agent: StrategistAgent.strategist.py:113
placeholders:
  - "{asset_class}"  — ticker (e.g. SPY)
  - "{recent_deals}"  — formatted comparables block, see PromptManager.format_comparables
  - "{market_data}"   — formatted market-data block, see PromptManager.format_deal_data
note: |
  The Strategist scans the LLM answer for the literal token "CLOSED"
  (case-insensitive, see strategist.py:40 _CLOSED_PATTERN). When matched,
  every candidate's rationale is prefixed with _CLOSED_WINDOW_WARNING — the
  Strategist does NOT refuse to produce candidates.
---
Based on the following recent prints and listed reference data, assess the current market window for {asset_class} option-issuance and structuring activity:

Recent Trades / Prints:
{recent_deals}

Market Data (vol surface, skew, term structure):
{market_data}

Provide:
1. Whether the market window is OPEN / CAUTIOUS / CLOSED for vanilla and barrier structures
2. Liquidity and bid-ask by structure type (vanilla, KO/KI, Asian, lookback)
3. Key tailwinds or headwinds (earnings, macro, skew regime)
4. Conditions that would shift the window
