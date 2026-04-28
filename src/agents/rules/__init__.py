"""Strategist rules: the IP of the platform.

In Phase 1 the StrategistAgent picks candidates by deterministic lookup against
the rules table. In Phase 2 the LLM consumes the rules table as a cached system
prompt and adapts strikes/barriers, but the *selection* logic stays here as a
fallback the LLM cannot ignore.
"""

from .strategy_rules import RuleRow, RULES, match_rules, build_candidates

__all__ = ["RuleRow", "RULES", "match_rules", "build_candidates"]
