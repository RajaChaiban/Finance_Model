"""NarratorAgent — synthesises the 3-way comparison memo.

Composes a MemoArtifact from the priced candidates, scenarios, validator
findings, and objective. The Markdown comparison table and term-sheet snippets
are computed deterministically (so the demo always has *something* to show).
The recommendation paragraph and per-candidate prose can be polished by the
LLM if available.

Phase 4 layers an HTML/Jinja template on top for the polished memo.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from src.config.agent_config import get_agent_config

from .base import AgentError, BaseAgent
from .llm_client import LLMUnavailableError, get_llm_client
from .prompts import load_prompt
from .state import (
    MemoArtifact,
    PricedCandidate,
    ScenarioReport,
    Severity,
    StructureKind,
    StructuringSession,
    TermSheetSnippet,
    ValidatorReport,
)

logger = logging.getLogger(__name__)


_NARRATOR_SYSTEM = load_prompt("narrator/system.md")


class NarratorAgent(BaseAgent):
    name = "NarratorAgent"

    def __init__(self, mi: Optional[Any] = None) -> None:
        # Narrator never queries MI — it stitches in upstream citations
        # already captured on session.market_context. The kwarg exists for
        # API parity with the other agents.
        self.mi = mi

    def _run(self, session: StructuringSession) -> StructuringSession:
        if not session.priced:
            raise AgentError("NarratorAgent requires priced candidates.")
        if session.objective is None:
            raise AgentError("NarratorAgent requires a ClientObjective.")

        memo = self._compose_deterministic(session)
        # Snapshot deterministic must-keep fields BEFORE the LLM polish.
        # The polish step rewrites prose but the verdict line, the comparison
        # table, and the structural caveats are load-bearing — re-apply them
        # after polish so the structurer always sees the pick at the top.
        scenarios_by_id = {s.candidate_id: s for s in session.scenarios}
        chosen = next(
            (
                p
                for p in session.priced
                if p.candidate.candidate_id == memo.recommended_candidate_id
            ),
            None,
        )
        verdict_line = self._verdict_line(chosen, scenarios_by_id, session.objective)
        deterministic_table = memo.comparison_table_md
        deterministic_caveats = list(memo.caveats)

        self._polish_with_llm(memo, session)

        # Force-restore the deterministic skeleton's invariants. The LLM may
        # have rewritten prose, but the table and verdict are non-negotiable.
        memo.comparison_table_md = deterministic_table
        memo.title = self._ensure_verdict_prefix(memo.title, verdict_line, on_title=True)
        memo.objective_restatement = self._ensure_verdict_prefix(
            memo.objective_restatement, verdict_line, on_title=False
        )
        memo.caveats = self._merge_caveats(memo.caveats, deterministic_caveats)

        # Stitch citations from upstream MI calls into the memo (no extra LLM).
        self._append_market_context_citations(memo, session)
        session.memo = memo
        return session

    @staticmethod
    def _ensure_verdict_prefix(text: str, verdict: str, *, on_title: bool) -> str:
        """Make sure `text` begins with the verdict line (in title or memo
        body form). If the LLM already inserted an equivalent prefix, leave
        it; otherwise prepend ours."""
        if not text:
            text = ""
        head = text.lstrip().split("\n", 1)[0].strip().lower()
        if "verdict" in head or "recommend" in head:
            return text
        if on_title:
            return f"VERDICT: {verdict}\n{text}".strip()
        return f"**RECOMMENDATION:** {verdict}\n\n{text}".strip()

    @staticmethod
    def _merge_caveats(llm_caveats: list[str], deterministic: list[str]) -> list[str]:
        """Combine LLM-polished caveats with the deterministic structural
        ones, dedup, cap at 6. Deterministic items first because they are
        action-oriented and load-bearing."""
        seen: set[str] = set()
        out: list[str] = []
        for c in list(deterministic) + list(llm_caveats):
            key = c.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(c.strip())
        return out[:6]

    # ------------------------------------------------------------------
    # Deterministic skeleton
    # ------------------------------------------------------------------

    def _compose_deterministic(self, session: StructuringSession) -> MemoArtifact:
        obj = session.objective
        priced = session.priced
        scenarios_by_id = {s.candidate_id: s for s in session.scenarios}
        validator = session.validator

        recommended_id = self._heuristic_pick(priced, scenarios_by_id)
        chosen = next(
            (p for p in priced if p.candidate.candidate_id == recommended_id),
            None,
        )

        verdict = self._verdict_line(chosen, scenarios_by_id, obj)

        # Title: prepend the verdict so a structurer reading the first line of
        # the memo sees the pick before the metadata.
        base_title = (
            f"3-Way Structuring Memo — {obj.underlying} "
            f"${obj.notional_usd:,.0f} {obj.horizon_days}d {obj.view}"
        )
        title = f"VERDICT: {verdict}\n{base_title}"

        # Objective_restatement leads with the verdict line so the front of
        # the memo answers "which one?" before any commentary.
        objective_text = (
            f"**RECOMMENDATION:** {verdict}\n\n"
            + self._restate_objective(obj)
        )

        comparison_md = self._comparison_table(
            priced, scenarios_by_id, validator, recommended_id, obj
        )
        per_cand_md = [
            self._candidate_section(pc, scenarios_by_id.get(pc.candidate.candidate_id))
            for pc in priced
        ]
        recommendation_md = self._default_recommendation(
            recommended_id, priced, scenarios_by_id, obj, session.market_context
        )
        caveats = self._caveats_for_memo(priced, scenarios_by_id, validator, obj)
        term_sheets = [
            TermSheetSnippet(
                candidate_id=pc.candidate.candidate_id,
                text=self._term_sheet_text(pc),
            )
            for pc in priced
        ]

        return MemoArtifact(
            title=title,
            objective_restatement=objective_text,
            comparison_table_md=comparison_md,
            per_candidate_sections_md=per_cand_md,
            recommendation_md=recommendation_md,
            recommended_candidate_id=recommended_id,
            term_sheets=term_sheets,
            caveats=caveats,
        )

    @staticmethod
    def _restate_objective(obj) -> str:
        parts = [
            f"Client holds ${obj.notional_usd:,.0f} in {obj.underlying}",
            f"view: {obj.view.replace('_', ' ')}",
            f"horizon: {obj.horizon_days} days",
            (
                "zero-cost only"
                if obj.premium_tolerance == "zero_cost_only"
                else f"premium budget {obj.budget_bps_notional:.0f}bps of notional"
            ),
        ]
        if obj.capped_upside_ok:
            parts.append("OK with capped upside")
        if obj.barrier_appetite:
            parts.append("OK with barrier risk")
        if obj.hedge_target_loss_pct is not None:
            parts.append(f"target max loss {obj.hedge_target_loss_pct:.0%}")
        return ". ".join(parts) + "."

    @staticmethod
    def _verdict_line(
        chosen: Optional[PricedCandidate],
        scenarios_by_id: dict[str, ScenarioReport],
        obj,
    ) -> str:
        """A 1-sentence justification: why this candidate, in plain English."""
        if chosen is None:
            return "No candidate could be selected — see comparison table."

        cid = chosen.candidate.candidate_id
        sr = scenarios_by_id.get(cid)
        worst_pct = NarratorAgent._worst_scenario_pct(sr)
        prem_bps = chosen.net_premium_bps
        budget_bps = float(obj.budget_bps_notional or 0.0)

        # Premium descriptor
        if prem_bps <= 0:
            prem_phrase = (
                f"zero-cost ({prem_bps:+.1f}bps)"
                if abs(prem_bps) < 1
                else f"net credit of {prem_bps:+.1f}bps"
            )
        else:
            within = ""
            if budget_bps > 0:
                within = (
                    " within budget" if prem_bps <= budget_bps else " ABOVE budget"
                )
            prem_phrase = f"{prem_bps:+.1f}bps{within}"

        # Worst-case descriptor
        worst_phrase = (
            f"worst-case {worst_pct:+.2%} of notional"
            if worst_pct is not None
            else "scenario P&L within tolerance"
        )

        return (
            f"**{chosen.candidate.name}** [{cid}] — best fit for "
            f"{obj.view.replace('_', ' ')} view at {prem_phrase}, "
            f"{worst_phrase}."
        )

    @staticmethod
    def _worst_scenario_pct(sr: Optional[ScenarioReport]) -> Optional[float]:
        """Worst (most-negative) P&L pct across all scenario rows."""
        if sr is None or not sr.scenarios:
            return None
        return min(r.pnl_pct_notional for r in sr.scenarios)

    @staticmethod
    def _worst_scenario_row(sr: Optional[ScenarioReport]):
        """Return the scenario row with the most-negative P&L (or None)."""
        if sr is None or not sr.scenarios:
            return None
        return min(sr.scenarios, key=lambda r: r.pnl_pct_notional)

    @staticmethod
    def _validator_emoji(
        validator: Optional[ValidatorReport], candidate_id: str
    ) -> str:
        """Highest-severity emoji for a given candidate id (or all-IDs scope)."""
        if validator is None or not validator.findings:
            return "OK"
        relevant = [
            f
            for f in validator.findings
            if f.candidate_id == candidate_id or f.candidate_id is None
        ]
        if not relevant:
            return "OK"
        if any(f.severity == Severity.BLOCK for f in relevant):
            return "BLOCK"
        if any(f.severity == Severity.WARN for f in relevant):
            return "WARN"
        return "OK"

    @staticmethod
    def _why_pick_phrase(pc: PricedCandidate, obj) -> str:
        """A 6–10 word phrase summarising fit-to-objective for this candidate."""
        kind = pc.candidate.kind
        prem = pc.net_premium_bps
        zero_cost = abs(prem) < 5  # treat near-zero as zero-cost
        is_credit = prem < -5

        if kind == StructureKind.ZERO_COST_COLLAR or (
            kind == StructureKind.COLLAR and zero_cost
        ):
            return "Zero-cost downside; trades upside cap"
        if kind == StructureKind.COLLAR:
            base = "Cheap protection; caps upside above call strike"
            return base if obj.capped_upside_ok else "Caps upside — confirm acceptable"
        if kind == StructureKind.PUT_SPREAD:
            return "Cheaper than long put; tail uncovered below short K"
        if kind == StructureKind.LONG_PUT:
            return "Full downside cover; highest premium of the three"
        if kind == StructureKind.LONG_CALL:
            return "Pure upside; capped loss = premium paid"
        if kind == StructureKind.CALL_SPREAD:
            return "Cheap directional upside; capped above short K"
        if kind in (StructureKind.KO_PUT, StructureKind.KI_PUT):
            return "Cheaper protection; barrier risk on knock event"
        if kind in (StructureKind.KO_CALL, StructureKind.KI_CALL):
            return "Cheap upside; barrier risk on knock event"
        if kind == StructureKind.COVERED_CALL:
            return "Yield enhancement; upside capped at short K"
        if kind == StructureKind.RISK_REVERSAL:
            return "Levered directional view; symmetric tail risk"
        if kind == StructureKind.PUT_SPREAD_COLLAR:
            return "Defined-risk hedge with funded collar leg"
        if is_credit:
            return "Credit structure; upside capped"
        if zero_cost:
            return "Zero-cost; upside or barrier trade-off"
        return "Defined-risk hedge matching client view"

    @staticmethod
    def _comparison_table(
        priced: list[PricedCandidate],
        scenarios_by_id: dict[str, ScenarioReport],
        validator: Optional[ValidatorReport],
        recommended_id: str,
        obj,
    ) -> str:
        """Decision table: 10 columns in the spec'd order, recommended row marked."""
        header = (
            "| Candidate | Strategy | Premium ($/bps) | Max Loss ($) | "
            "Max Gain ($) | Δ | Vega | Worst Scenario P&L | Validator | Why Pick |\n"
            "|---|---|---:|---:|---:|---:|---:|---:|:---:|---|\n"
        )
        rows = []
        for pc in priced:
            cid = pc.candidate.candidate_id
            sr = scenarios_by_id.get(cid)
            worst_row = NarratorAgent._worst_scenario_row(sr)
            if worst_row is not None:
                worst_cell = (
                    f"{worst_row.pnl_usd:+,.0f} "
                    f"({worst_row.pnl_pct_notional:+.2%}) [{worst_row.name}]"
                )
            else:
                worst_cell = "n/a"

            # Max loss is stored as a positive magnitude on PricedCandidate;
            # render it as a signed dollar loss so a structurer reads "-$X".
            if pc.max_loss_usd is not None:
                max_loss_cell = f"-{pc.max_loss_usd:,.0f}"
            else:
                max_loss_cell = "n/a"
            if pc.max_gain_usd is not None:
                if pc.max_gain_usd >= 1e10:  # uncapped (e.g. long put inheriting upside)
                    max_gain_cell = "uncapped"
                else:
                    max_gain_cell = f"+{pc.max_gain_usd:,.0f}"
            else:
                max_gain_cell = "n/a"

            premium_cell = f"${pc.net_premium:+,.0f} ({pc.net_premium_bps:+.1f}bps)"
            v_status = NarratorAgent._validator_emoji(validator, cid)
            why = NarratorAgent._why_pick_phrase(pc, obj)
            marker = "**>>** " if cid == recommended_id else ""
            name_cell = f"{marker}{pc.candidate.name} [{cid}]"

            rows.append(
                f"| {name_cell} | {pc.candidate.kind.value} | {premium_cell} | "
                f"{max_loss_cell} | {max_gain_cell} | "
                f"{pc.greeks.delta:+.3f} | {pc.greeks.vega:+.2f} | "
                f"{worst_cell} | {v_status} | {why} |"
            )
        legend = (
            "\n\n_Legend: **>>** = recommended pick. "
            "Validator: OK = clean / WARN = warning / BLOCK = blocker. "
            "Max Loss is the worst payoff at expiry (premium-at-risk for debit "
            "structures, strike gap for collars). Max Gain 'uncapped' = no "
            "structural ceiling._"
        )
        return header + "\n".join(rows) + legend

    @staticmethod
    def _candidate_section(pc: PricedCandidate, sr: Optional[ScenarioReport]) -> str:
        lines = [
            f"### {pc.candidate.name}",
            "",
            pc.candidate.rationale.strip(),
            "",
            "**Legs:**",
        ]
        for leg in pc.candidate.legs:
            extra = ""
            if leg.barrier_level is not None:
                extra = f" B={leg.barrier_level} ({leg.barrier_monitoring})"
            lines.append(
                f"- {leg.option_type} K={leg.strike} qty={leg.quantity:+.0f}"
                f" exp={leg.expiry_days}d{extra}"
            )
        lines.append("")
        lines.append(
            f"**Net premium:** ${pc.net_premium:+,.0f} ({pc.net_premium_bps:+.1f} bps of notional)"
        )
        if pc.max_loss_usd is not None:
            lines.append(f"**Max loss (USD):** {pc.max_loss_usd:+,.0f}")
        if pc.max_gain_usd is not None:
            lines.append(f"**Max gain (USD):** {pc.max_gain_usd:+,.0f}")

        if sr:
            lines.append("")
            lines.append("**Scenarios:**")
            lines.append("| Scenario | Spot Shock | Vol Shock | Total P&L ($) | P&L (% notional) |")
            lines.append("|---|---:|---:|---:|---:|")
            for r in sr.scenarios:
                lines.append(
                    f"| {r.name} | {r.spot_shock_pct:+.0%} | {r.vol_shock_pct:+.0%} | "
                    f"{r.pnl_usd:+,.0f} | {r.pnl_pct_notional:+.2%} |"
                )
            lines.append("")
            lines.append(
                f"**Hedgeable:** {'yes' if sr.hedgeability_ok else 'NO'} — {sr.hedgeability_reason}"
            )
        return "\n".join(lines)

    @staticmethod
    def _term_sheet_text(pc: PricedCandidate) -> str:
        """Stable, parseable term-sheet block — `KEY: value` lines with a LEGS
        sub-block. Designed to be machine-readable: every field is on its own
        line and the LEGS block is bounded by `LEGS:` / `END LEGS`.
        """
        cid = pc.candidate.candidate_id
        max_loss_str = (
            f"-${pc.max_loss_usd:,.0f}"
            if pc.max_loss_usd is not None
            else "n/a"
        )
        if pc.max_gain_usd is None:
            max_gain_str = "n/a"
        elif pc.max_gain_usd >= 1e10:
            max_gain_str = "uncapped"
        else:
            max_gain_str = f"+${pc.max_gain_usd:,.0f}"
        breakeven_str = (
            ", ".join(f"{b:.2f}" for b in pc.breakeven)
            if pc.breakeven
            else "n/a"
        )

        lines = [
            f"CANDIDATE_ID: {cid}",
            f"STRUCTURE: {pc.candidate.name}",
            f"KIND: {pc.candidate.kind.value}",
            f"NOTIONAL: ${pc.candidate.notional_usd:,.0f}",
            f"NET_PREMIUM: ${pc.net_premium:+,.0f} ({pc.net_premium_bps:+.1f} bps)",
            f"MAX_LOSS: {max_loss_str}",
            f"MAX_GAIN: {max_gain_str}",
            f"BREAKEVEN: {breakeven_str}",
            "LEGS:",
        ]
        for i, leg in enumerate(pc.candidate.legs, start=1):
            barrier_kv = (
                f" BARRIER={leg.barrier_level} MONITORING={leg.barrier_monitoring}"
                if leg.barrier_level is not None
                else ""
            )
            lines.append(
                f"  LEG_{i}: TYPE={leg.option_type} STRIKE={leg.strike:.2f} "
                f"QTY={leg.quantity:+.0f} EXPIRY_DAYS={leg.expiry_days}"
                f"{barrier_kv}"
            )
        lines.append("END LEGS")
        lines.append(
            f"GREEKS: D={pc.greeks.delta:+.3f} G={pc.greeks.gamma:.4f} "
            f"V={pc.greeks.vega:+.2f} T={pc.greeks.theta:+.3f} "
            f"DV01={pc.greeks.dv01:+.4f}"
        )
        lines.append("INDICATIVE — not a binding quote. Subject to dealer confirmation.")
        return "\n".join(lines)

    @staticmethod
    def _heuristic_pick(
        priced: list[PricedCandidate],
        scenarios_by_id: dict[str, ScenarioReport],
    ) -> str:
        """Score each candidate; pick the highest. Crash P&L weighted positive,
        |premium| weighted negative, hedgeability flag is gating.
        """

        def score(pc: PricedCandidate) -> float:
            sr = scenarios_by_id.get(pc.candidate.candidate_id)
            if sr and not sr.hedgeability_ok:
                return -1e9
            crash_pnl_pct = 0.0
            if sr:
                for r in sr.scenarios:
                    if "Crash" in r.name:
                        crash_pnl_pct = r.pnl_pct_notional
                        break
            premium_penalty = abs(pc.net_premium_bps) / 100.0  # bps → %
            return crash_pnl_pct * 100.0 - premium_penalty

        ranked = sorted(priced, key=score, reverse=True)
        return ranked[0].candidate.candidate_id

    @staticmethod
    def _default_recommendation(
        rec_id: str,
        priced: list[PricedCandidate],
        scenarios_by_id: dict[str, ScenarioReport],
        obj,
        market_context: Optional[list[dict[str, Any]]],
    ) -> str:
        chosen = next((p for p in priced if p.candidate.candidate_id == rec_id), None)
        if chosen is None:
            return "Recommendation: see comparison table."

        sr = scenarios_by_id.get(rec_id)
        worst_row = NarratorAgent._worst_scenario_row(sr)
        worst_clause = (
            f"Worst-case scenario tested ({worst_row.name}) is "
            f"{worst_row.pnl_usd:+,.0f} ({worst_row.pnl_pct_notional:+.2%} of notional)."
            if worst_row is not None
            else "Scenario stress tests show no material breach of risk tolerance."
        )

        # Compare against alternatives — what does the client give up?
        alternatives = [p for p in priced if p.candidate.candidate_id != rec_id]
        cheapest = min(priced, key=lambda p: p.net_premium_bps)
        most_protective = min(
            priced,
            key=lambda p: (
                NarratorAgent._worst_scenario_pct(scenarios_by_id.get(p.candidate.candidate_id))
                or 0.0
            ),
        )
        tradeoff_bits: list[str] = []
        if cheapest.candidate.candidate_id != rec_id:
            tradeoff_bits.append(
                f"cheaper alternative is **{cheapest.candidate.name}** "
                f"at {cheapest.net_premium_bps:+.1f}bps"
            )
        if (
            most_protective.candidate.candidate_id != rec_id
            and most_protective.candidate.candidate_id != cheapest.candidate.candidate_id
        ):
            tradeoff_bits.append(
                f"most-protective alternative is **{most_protective.candidate.name}**"
            )
        tradeoff_clause = (
            "Trade-off vs. siblings: " + "; ".join(tradeoff_bits) + "."
            if tradeoff_bits
            else ""
        )

        # Market intelligence reference (or explicit absence).
        mi_clause = ""
        entries = list(market_context or [])
        if entries:
            # Pick the most-confident entry from a non-Narrator agent.
            ranked = sorted(
                entries,
                key=lambda e: (
                    {"high": 3, "medium": 2, "low": 1}.get(str(e.get("confidence", "")).lower(), 0)
                ),
                reverse=True,
            )
            top = ranked[0]
            top_agent = top.get("agent", "MI")
            top_intent = top.get("intent", "context")
            top_sources = top.get("sources") or []
            cite_id = ""
            for s in top_sources:
                if isinstance(s, dict) and s.get("id"):
                    cite_id = str(s["id"])
                    break
            cite_suffix = f" [source: {cite_id}]" if cite_id else ""
            mi_clause = (
                f" Market context (via {top_agent}, {top_intent}) "
                f"supports this view{cite_suffix}."
            )
        else:
            mi_clause = " Note: no MI context available for this session."

        para1 = (
            f"**RECOMMENDED: {chosen.candidate.name}** [{chosen.candidate.candidate_id}]. "
            f"Net premium {chosen.net_premium_bps:+.1f}bps "
            f"(${chosen.net_premium:+,.0f}), net delta {chosen.greeks.delta:+.3f}, "
            f"vega {chosen.greeks.vega:+.2f}. "
            f"Best-balanced fit for {obj.view.replace('_', ' ')} view "
            f"with the client's premium tolerance ({obj.premium_tolerance})."
        )
        para2 = (
            f"{worst_clause} "
            f"Validator cleared this candidate for hedgeability."
            f"{mi_clause}"
        )
        if tradeoff_clause:
            return f"{para1}\n\n{para2}\n\n{tradeoff_clause}"
        return f"{para1}\n\n{para2}"

    @staticmethod
    def _caveats_from_validator(report: Optional[ValidatorReport]) -> list[str]:
        """Backwards-compatible alias kept for callers that only have the
        validator report. Prefer `_caveats_for_memo` which adds structural
        caveats."""
        if report is None:
            return []
        warnings = [
            f"{f.candidate_id or '*'}: {f.message}"
            for f in report.findings
            if f.severity == Severity.WARN
        ]
        return warnings[:4]

    @staticmethod
    def _caveats_for_memo(
        priced: list[PricedCandidate],
        scenarios_by_id: dict[str, ScenarioReport],
        validator: Optional[ValidatorReport],
        obj,
    ) -> list[str]:
        """Action-oriented caveats: validator warnings + structural caveats
        derived from the candidate set. Capped at 5 items, deduplicated, and
        phrased as direct instructions to the salesperson."""
        out: list[str] = []

        # 1. Validator warnings (already factual)
        if validator is not None:
            for f in validator.findings:
                if f.severity == Severity.WARN:
                    cid = f.candidate_id or "*"
                    out.append(f"[{cid}] {f.message}")
                elif f.severity == Severity.BLOCK:
                    cid = f.candidate_id or "*"
                    out.append(f"[BLOCKER {cid}] {f.message}")

        # 2. Structural caveats from candidate kinds
        seen_kinds: set[StructureKind] = set()
        for pc in priced:
            kind = pc.candidate.kind
            if kind in seen_kinds:
                continue
            seen_kinds.add(kind)
            cid = pc.candidate.candidate_id
            short_calls = [leg for leg in pc.candidate.legs if leg.quantity < 0 and "call" in leg.option_type]
            if kind in (StructureKind.COLLAR, StructureKind.ZERO_COST_COLLAR, StructureKind.COVERED_CALL, StructureKind.PUT_SPREAD_COLLAR) and short_calls:
                cap_strike = max(leg.strike for leg in short_calls)
                drawdown_above = (cap_strike / max(1.0, _safe_spot(obj, pc)) - 1.0)
                out.append(
                    f"[{cid}] Client must accept upside cap at strike "
                    f"{cap_strike:.2f} (~{drawdown_above:+.1%} from spot) "
                    f"— confirm in writing before pricing."
                )
            if kind in (
                StructureKind.KO_PUT,
                StructureKind.KI_PUT,
                StructureKind.KO_CALL,
                StructureKind.KI_CALL,
            ):
                out.append(
                    f"[{cid}] Barrier structure: confirm client accepts "
                    f"discontinuous payoff at the barrier and monitoring frequency."
                )
            if kind == StructureKind.PUT_SPREAD:
                short_puts = [leg for leg in pc.candidate.legs if leg.quantity < 0 and "put" in leg.option_type]
                if short_puts:
                    short_k = min(leg.strike for leg in short_puts)
                    out.append(
                        f"[{cid}] Tail uncovered below {short_k:.2f} — flag "
                        f"this as a pure budget play, not crash protection."
                    )

        # 3. Worst-case scenario callouts
        for pc in priced:
            sr = scenarios_by_id.get(pc.candidate.candidate_id)
            worst_row = NarratorAgent._worst_scenario_row(sr)
            if worst_row is None:
                continue
            if worst_row.pnl_pct_notional <= -0.10:
                out.append(
                    f"[{pc.candidate.candidate_id}] {worst_row.name} stress "
                    f"shows {worst_row.pnl_pct_notional:+.1%} of notional "
                    f"({worst_row.pnl_usd:+,.0f}) — verify drawdown tolerance."
                )

        # Dedup, preserve order, cap.
        seen: set[str] = set()
        uniq: list[str] = []
        for c in out:
            if c not in seen:
                seen.add(c)
                uniq.append(c)
        return uniq[:5]

    # ------------------------------------------------------------------
    # Market-intelligence citations
    # ------------------------------------------------------------------

    @staticmethod
    def _append_market_context_citations(
        memo: MemoArtifact, session: StructuringSession
    ) -> None:
        """Append a 'Market Intelligence Citations' section to the memo.

        Uses upstream `session.market_context` entries (populated by Intake /
        Strategist / Pricing / Scenario / Validator). Renders both into a
        Markdown section appended to the recommendation, and (when available)
        into the rendered HTML.
        """
        entries = list(session.market_context or [])
        if not entries:
            return

        lines = ["", "### Market Intelligence Citations", ""]
        for i, e in enumerate(entries, start=1):
            agent = e.get("agent", "?")
            intent = e.get("intent", "?")
            confidence = e.get("confidence", "?")
            answer = (e.get("answer") or "").strip()
            # Keep each citation tight — first 280 chars is enough to give the
            # salesperson a defensible quote.
            snippet = (answer[:280] + "…") if len(answer) > 280 else answer
            sources = e.get("sources") or []
            source_ids = ", ".join(
                str(s.get("id"))
                for s in sources
                if isinstance(s, dict) and s.get("id")
            )
            lines.append(
                f"{i}. **{agent}** ({intent}, confidence: {confidence})"
                + (f" — sources: {source_ids}" if source_ids else "")
            )
            if snippet:
                lines.append(f"   {snippet}")
            lines.append("")

        section_md = "\n".join(lines)
        # Append to the recommendation paragraph so it lands at the bottom of
        # the memo without disturbing the comparison table or per-candidate
        # sections that the LLM polished.
        memo.recommendation_md = (
            (memo.recommendation_md or "").rstrip() + "\n\n" + section_md.strip() + "\n"
        )

        # If a rendered HTML is present, mirror the section there too.
        if memo.rendered_html:
            html_lines = [
                "<section class='market-intel-citations'>",
                "<h3>Market Intelligence Citations</h3>",
                "<ol>",
            ]
            for e in entries:
                agent = e.get("agent", "?")
                intent = e.get("intent", "?")
                confidence = e.get("confidence", "?")
                answer = (e.get("answer") or "").strip()
                snippet = (answer[:280] + "…") if len(answer) > 280 else answer
                sources = e.get("sources") or []
                source_ids = ", ".join(
                    str(s.get("id"))
                    for s in sources
                    if isinstance(s, dict) and s.get("id")
                )
                html_lines.append(
                    f"<li><strong>{agent}</strong> "
                    f"<em>({intent}, confidence: {confidence})</em>"
                    + (f" — sources: {source_ids}" if source_ids else "")
                    + (f"<br>{snippet}" if snippet else "")
                    + "</li>"
                )
            html_lines += ["</ol>", "</section>"]
            memo.rendered_html = memo.rendered_html.rstrip() + "\n" + "\n".join(html_lines)

    # ------------------------------------------------------------------
    # Optional LLM polish
    # ------------------------------------------------------------------

    def _polish_with_llm(self, memo: MemoArtifact, session: StructuringSession) -> None:
        cfg = get_agent_config()
        if not (cfg.has_gemini or cfg.demo_replay):
            return

        client = get_llm_client()
        prompt = _build_narrator_prompt(memo, session)
        try:
            res = client.complete(
                agent_name=self.name,
                model=cfg.model_narrator,
                system=_NARRATOR_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                json_mode=True,
                replay_key="NarratorAgent:memo",
            )
        except LLMUnavailableError:
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Narrator polish failed: %s", exc)
            return

        parsed = res.parsed_json
        if not isinstance(parsed, dict):
            return

        if title := parsed.get("title"):
            memo.title = str(title).strip()
        if obj := parsed.get("objective_restatement"):
            memo.objective_restatement = str(obj).strip()

        # Apply per-candidate prose if matching ids.
        prose_by_id = {
            entry.get("candidate_id"): entry.get("summary")
            for entry in parsed.get("per_candidate_prose", []) or []
            if entry.get("candidate_id") and entry.get("summary")
        }
        if prose_by_id:
            new_sections = []
            for pc in session.priced:
                cid = pc.candidate.candidate_id
                if cid in prose_by_id:
                    sr = next(
                        (s for s in session.scenarios if s.candidate_id == cid), None
                    )
                    section = self._candidate_section(pc, sr)
                    section = section.replace(
                        pc.candidate.rationale.strip(),
                        str(prose_by_id[cid]).strip(),
                    )
                    new_sections.append(section)
                else:
                    new_sections.append(
                        next(
                            (s for s in memo.per_candidate_sections_md if pc.candidate.name in s),
                            "",
                        )
                    )
            if all(new_sections):
                memo.per_candidate_sections_md = new_sections

        rec = parsed.get("recommendation") or {}
        rec_id = rec.get("candidate_id")
        rec_para = rec.get("paragraph")
        valid_ids = {p.candidate.candidate_id for p in session.priced}
        if rec_id in valid_ids and rec_para:
            memo.recommended_candidate_id = rec_id
            memo.recommendation_md = str(rec_para).strip()

        if caveats := parsed.get("caveats"):
            memo.caveats = [str(c) for c in caveats][:4]


def _safe_spot(obj, pc: PricedCandidate) -> float:
    """Best-effort spot reference for caveats (no MarketRegime in scope here).
    Falls back to the structure's notional/quantity ratio implied by the
    first long put strike if no other signal is available — purely for
    formatting the cap-vs-spot delta in caveats; correctness is not
    load-bearing."""
    long_puts = [leg for leg in pc.candidate.legs if leg.quantity > 0 and "put" in leg.option_type]
    if long_puts:
        return float(long_puts[0].strike) / 0.95  # roughly assume put is 5% OTM
    long_calls = [leg for leg in pc.candidate.legs if leg.quantity > 0 and "call" in leg.option_type]
    if long_calls:
        return float(long_calls[0].strike) / 1.05
    if pc.candidate.legs:
        return float(pc.candidate.legs[0].strike)
    return 1.0


def _build_narrator_prompt(memo: MemoArtifact, session: StructuringSession) -> str:
    obj = session.objective
    cand_blocks: list[str] = []
    scenarios_by_id = {s.candidate_id: s for s in session.scenarios}
    for pc in session.priced:
        sr = scenarios_by_id.get(pc.candidate.candidate_id)
        sc_lines = []
        if sr:
            for r in sr.scenarios:
                sc_lines.append(
                    f"  {r.name}: spot {r.spot_shock_pct:+.0%}, vol {r.vol_shock_pct:+.0%} "
                    f"-> P&L ${r.pnl_usd:+,.0f} ({r.pnl_pct_notional:+.2%})"
                )
        cand_blocks.append(
            f"- candidate_id={pc.candidate.candidate_id} kind={pc.candidate.kind.value}\n"
            f"  name={pc.candidate.name}\n"
            f"  rationale={pc.candidate.rationale}\n"
            f"  premium={pc.net_premium_bps:+.1f}bps (${pc.net_premium:+,.0f})\n"
            f"  greeks: Δ={pc.greeks.delta:+.3f} Γ={pc.greeks.gamma:.4f} V={pc.greeks.vega:+.2f} "
            f"Θ={pc.greeks.theta:+.3f} DV01={pc.greeks.dv01:+.4f}\n"
            + ("  scenarios:\n" + "\n".join(sc_lines) if sc_lines else "")
        )

    objective_block = (
        f"underlying={obj.underlying} notional={obj.notional_usd:,.0f} "
        f"view={obj.view} horizon_days={obj.horizon_days} "
        f"budget_bps={obj.budget_bps_notional} premium_tol={obj.premium_tolerance} "
        f"capped_upside_ok={obj.capped_upside_ok} barrier_appetite={obj.barrier_appetite}"
    )

    return (
        "OBJECTIVE:\n" + objective_block + "\n\n"
        "CANDIDATES:\n" + "\n".join(cand_blocks)
    )
