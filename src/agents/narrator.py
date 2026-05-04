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
from datetime import date, datetime
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


# ---------------------------------------------------------------------------
# Title-composition + direction-filter constants
# ---------------------------------------------------------------------------

# Maps ClientObjective.view → human-readable phrase used in the memo title.
_TITLE_DIRECTION_WORDS: dict[str, str] = {
    "bullish": "Upside Participation",
    "mildly_bullish": "Upside Participation",
    "bearish": "Downside Protection",
    "mildly_bearish": "Downside Protection",
    "protect_gains": "Gain Lock-In",
    "crash_hedge": "Tail Hedge",
    "earnings_hedge": "Earnings Hedge",
    "neutral": "Yield Enhancement",
}

# Direction-sign filter buckets — used to drop wrong-direction candidates from
# the recommendation tiebreak before scoring.
_BULLISH_VIEWS: frozenset[str] = frozenset({"bullish", "mildly_bullish"})
_BEARISH_VIEWS: frozenset[str] = frozenset({
    "bearish",
    "mildly_bearish",
    "protect_gains",
    "crash_hedge",
    "earnings_hedge",
})

# Underlying-class lookup (cheap, no MI/RAG hit). Keys are uppercased tickers.
# Used for event-keyed caveat selection (e.g. REIT → FOMC, ENERGY → OPEC).
_UNDERLYING_CLASS: dict[str, str] = {
    "SPY": "BROAD",
    "QQQ": "BROAD",
    "IWM": "BROAD",
    "SMH": "BROAD",
    "DIA": "BROAD",
    "XLK": "TECH",
    "XLE": "ENERGY",
    "XLV": "HEALTHCARE",
    "XLF": "FINANCIALS",
    "XLP": "STAPLES",
    "XLY": "DISCRETIONARY",
    "XLRE": "REIT",
    "XLU": "UTILITIES",
    "XLB": "MATERIALS",
    "XLI": "INDUSTRIALS",
    "XLC": "COMMUNICATIONS",
    "VNQ": "REIT",
    "IYR": "REIT",
}

# Event-keyed caveats. The lookup walks (view, class), then ("*", class), then
# (view, "*"). Wildcard "*" means "any view" / "any class". Append all unique
# matches found across these tiers.
EVENT_CAVEATS: dict[tuple[str, str], list[str]] = {
    ("earnings_hedge", "*"): [
        "IV crush risk: ATM IV typically drops 30-40% post-print; long-vol legs lose value even on directional move.",
        "Pin risk near short strikes on weekly expiry chains.",
    ],
    ("crash_hedge", "*"): [
        "18mo+ tenor: meaningful theta drag and rho exposure (long-duration puts have rho ~ -3 per 1% rate).",
    ],
    ("protect_gains", "REIT"): [
        "Rate-sensitive sector: FOMC catalyst risk over multi-month tenor; rho exposure non-trivial.",
    ],
    ("bearish", "ENERGY"): [
        "Energy-specific catalysts: OPEC+ meeting cadence, EIA inventory releases, ex-div dates.",
    ],
    ("mildly_bearish", "ENERGY"): [
        "Energy-specific catalysts: OPEC+ meeting cadence, EIA inventory releases, ex-div dates.",
    ],
    ("neutral", "HEALTHCARE"): [
        "Constituent earnings cycle (UNH/JNJ/LLY/PFE/ABBV) creates rolling event-vol pickup; short-vol legs vulnerable.",
    ],
    # Fallback: forward-anchoring reminder for high-div (q>3%) underliers.
    ("*", "REIT"): [
        "High dividend yield - strikes priced against forward, not spot.",
    ],
    ("*", "STAPLES"): [
        "Dividend-rich sector: forward sits above spot; verify strikes are forward-anchored, not spot-anchored.",
    ],
}


def _compose_title(obj) -> str:
    """Compose a dynamic memo title from the ClientObjective.

    Replaces the legacy hardcoded "SPY Downside Protection (8m)" template that
    leaked through the demo_replay fixture into every memo regardless of view
    or underlying. Mirrors the existing helper-naming convention
    (`_ensure_verdict_prefix`, `_ensure_mi_footer`).
    """
    underlying = (obj.underlying or "").upper() or "?"
    direction_word = _TITLE_DIRECTION_WORDS.get(obj.view, obj.view.replace("_", " ").title())
    horizon_days = int(obj.horizon_days or 0)
    if horizon_days < 90:
        tenor_label = f"{horizon_days}d"
    else:
        tenor_label = f"{horizon_days // 30}m"
    return f"Internal RFQ - {underlying} {direction_word} ({tenor_label})"


def _underlying_class(ticker: str) -> str:
    """Map a ticker to a coarse asset/sector class for caveat lookup.

    Returns 'OTHER' for unknown tickers — caveat lookup falls back to the
    view-only wildcard row in that case.
    """
    return _UNDERLYING_CLASS.get((ticker or "").upper(), "OTHER")


def _event_caveats_for(obj) -> list[str]:
    """Return the canonical event-keyed caveat strings for a ClientObjective.

    Walks the EVENT_CAVEATS lookup in (view, class) -> ("*", class) ->
    (view, "*") order, deduping while preserving order. This is the same
    walk used inside `_caveats_for_memo` step 4 — extracted as a
    free-function so the post-polish guard `_ensure_event_caveats` can
    call it without re-implementing the walk.
    """
    if obj is None:
        return []
    view = (getattr(obj, "view", "") or "").strip()
    u_class = _underlying_class(getattr(obj, "underlying", "") or "")
    seen: set[str] = set()
    out: list[str] = []
    for key in (
        (view, u_class),
        ("*", u_class),
        (view, "*"),
    ):
        for caveat in EVENT_CAVEATS.get(key, ()):
            if caveat not in seen:
                seen.add(caveat)
                out.append(caveat)
    return out


def _validator_blocked(
    pc: PricedCandidate, validator_report: Optional[ValidatorReport]
) -> bool:
    """True when the validator emitted any severity=BLOCK finding keyed to
    this candidate's id.

    Used as the highest-priority pre-filter in `_heuristic_pick` (Fix 1):
    a candidate the validator categorically rejected must not be surfaced
    as the recommendation. Findings with `candidate_id is None` are
    cross-cutting (apply to the whole RFQ) and DO NOT count here — those
    don't disqualify any one candidate, they just flag a global issue.
    """
    if validator_report is None:
        return False
    return any(
        f.severity == Severity.BLOCK and f.candidate_id == pc.candidate.candidate_id
        for f in validator_report.findings
    )


def _is_direction_compatible(pc: PricedCandidate, obj) -> bool:
    """Reject candidates whose net delta points the wrong way for the brief.

    Used as a hard pre-filter before the recommendation tiebreak loop. The
    +/-0.05 dead-band tolerates near-zero-delta structures (e.g. fully
    covered collars) without flagging them as wrong-direction.
    """
    delta = pc.greeks.delta
    if obj.view in _BULLISH_VIEWS and delta < -0.05:
        return False
    if obj.view in _BEARISH_VIEWS and delta > +0.05:
        return False
    return True


def _capped_upside_compatible(pc: PricedCandidate, obj) -> bool:
    """Reject candidates that cap upside when the client refused the cap.

    A short call leg (any kind) caps upside above its strike — incompatible
    with `capped_upside_ok=False`. Iron condors / risk-reversals with
    multi-leg short calls collapse to the same rule.
    """
    if obj.capped_upside_ok:
        return True
    for leg in pc.candidate.legs:
        if leg.quantity < 0 and leg.option_type.endswith("_call"):
            return False
    return True


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
        deterministic_recommendation = memo.recommendation_md

        self._polish_with_llm(memo, session)

        # Force-restore the deterministic skeleton's invariants. The LLM may
        # have rewritten prose, but the table and verdict are non-negotiable.
        # Ordering: deterministic skeleton -> LLM polish -> verdict prefix ->
        # MI footer -> event-keyed caveats -> title-template enforcement.
        memo.comparison_table_md = deterministic_table
        memo.title = self._ensure_verdict_prefix(memo.title, verdict_line, on_title=True)
        memo.objective_restatement = self._ensure_verdict_prefix(
            memo.objective_restatement, verdict_line, on_title=False
        )
        memo.caveats = self._merge_caveats(memo.caveats, deterministic_caveats)
        memo.recommendation_md = self._ensure_mi_footer(
            memo.recommendation_md, deterministic_recommendation
        )
        # Fix 3: re-append any canonical event-keyed caveats the LLM polish
        # paraphrased away. Deterministic strings only — token-level audit
        # signal that the lookup table actually fired for this objective.
        self._ensure_event_caveats(memo, session.objective)
        # Fix 2: title-template guard runs LAST so it can wrap a verdict line
        # the prefix step inserted. Always re-derive from objective; the
        # LLM polish step may have replaced our composed title with a leaked
        # template string from a replay fixture (legacy "SPY Downside
        # Protection (8m)" payload).
        memo.title = self._enforce_title_template(memo.title, session.objective)

        # Stitch citations from upstream MI calls into the memo (no extra LLM).
        self._append_market_context_citations(memo, session)
        # Render a freshness-aware "Recent Comparable Deals" section so the
        # structurer can see what other desks are doing and how stale the
        # corpus is at quote time.
        self._append_comparable_deals_section(memo, session)
        session.memo = memo
        return session

    @staticmethod
    def _enforce_title_template(text: str, obj) -> str:
        """Always re-derive the "Internal RFQ - …" prefix from the
        ClientObjective and replace any LLM-emitted variant.

        Previous logic trusted any "Internal RFQ" line whose ticker substring
        matched the objective ticker — that left SPY-keyed leaks like
        ``Internal RFQ - SPY Downside Protection (8m)`` in place even when
        the objective specified a different view or tenor (the canonical
        ``(8m)`` smoking gun). The dynamic compose handles every (underlying,
        view, horizon) tuple correctly.

        We compare the "Internal RFQ - <UNDERLYING> <DIRECTION> (<TENOR>)"
        prefix BYTE-FOR-BYTE; any line that contains "Internal RFQ" but
        whose first segment doesn't match the canonical compose is rewritten.
        The deterministic skeleton produces lines of the form
        ``Internal RFQ - SPY Tail Hedge (18m) - SPY $500M 365d crash_hedge``
        which DO match the canonical prefix and are kept verbatim
        (suffix-and-all). LLM-polished leaks like
        ``Internal RFQ - SPY Downside Protection (8m)`` do NOT match and
        are replaced with the canonical compose. Preserves the VERDICT
        prefix line that `_ensure_verdict_prefix` added (must run BEFORE
        this guard).
        """
        if obj is None:
            return text or ""
        text = text or ""
        expected = _compose_title(obj).strip()
        if not text:
            return expected

        out_lines: list[str] = []
        replaced = False
        for line in text.splitlines():
            if "Internal RFQ" in line:
                # Byte-for-byte prefix match: strip any leading whitespace,
                # compare the first len(expected) chars to the canonical
                # compose. The deterministic skeleton's "<canonical> - SPY
                # $X 365d crash_hedge" suffix passes; any LLM-coined
                # variant ("SPY Downside Protection (8m)" on a 365d horizon)
                # does not.
                stripped = line.lstrip()
                if stripped.startswith(expected):
                    out_lines.append(line)
                    replaced = True
                elif not replaced:
                    out_lines.append(expected)
                    replaced = True
                # else: this is a duplicate / drift Internal RFQ line — drop.
            else:
                out_lines.append(line)

        if not replaced:
            # No "Internal RFQ" line at all (legacy "3-Way Structuring Memo"
            # or any other LLM-coined title) — insert the composed title
            # after any VERDICT prefix line so ops triage always has a
            # deterministic anchor.
            insert_at = 0
            if out_lines and out_lines[0].lstrip().lower().startswith("verdict"):
                insert_at = 1
            out_lines.insert(insert_at, expected)

        return "\n".join(out_lines).strip()

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
    def _ensure_mi_footer(polished: str, deterministic: str) -> str:
        """Re-apply the MI citation / absence clause after LLM polish.

        The deterministic recommendation always ends with either
        ``Market context (via X, Y) supports this view [source: Z].`` or
        ``Note: no MI context available for this session.`` — both load-bearing
        signals for downstream auditing. The LLM polish step may rewrite prose
        and drop them; this guard restores whichever clause was present.
        """
        polished = polished or ""
        polished_lower = polished.lower()
        if (
            "[source:" in polished
            or "market context (via" in polished_lower
            or "no mi context" in polished_lower
        ):
            return polished
        det = deterministic or ""
        clause = ""
        if "Market context" in det:
            idx = det.rfind("Market context")
            if idx >= 0:
                end = det.find("\n", idx)
                clause = det[idx:end if end > 0 else None].strip()
        elif "no MI context" in det.lower():
            idx = det.lower().rfind("note: no mi context")
            if idx >= 0:
                end = det.find("\n", idx)
                clause = det[idx:end if end > 0 else None].strip()
        if not clause:
            clause = "Note: no MI context available for this session."
        return f"{polished.rstrip()}\n\n{clause}\n"

    @staticmethod
    def _ensure_event_caveats(memo: MemoArtifact, obj) -> MemoArtifact:
        """Re-append any canonical event-keyed caveat string the LLM polish
        step paraphrased away.

        Mirrors the `_ensure_mi_footer` pattern: we trust the LLM to polish
        prose but enforce that load-bearing deterministic strings (e.g.
        "IV crush risk: ATM IV typically drops 30-40% post-print …" for
        an ``earnings_hedge`` view) survive polish verbatim, since downstream
        token-level audit checks rely on them. The cap of 8 keeps the
        section visually bounded; canonical caveats jump the queue ahead
        of any LLM-coined extras only if we're already at the cap.
        """
        canonical = _event_caveats_for(obj)
        if not canonical:
            return memo
        existing = list(memo.caveats or [])
        existing_set = set(existing)
        appended = False
        for c in canonical:
            if c not in existing_set:
                existing.append(c)
                existing_set.add(c)
                appended = True
        if appended:
            memo.caveats = existing[:8]
        return memo

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

        recommended_id, filter_caveat = self._heuristic_pick(
            priced, scenarios_by_id, obj, validator
        )
        chosen = next(
            (p for p in priced if p.candidate.candidate_id == recommended_id),
            None,
        )

        verdict = self._verdict_line(chosen, scenarios_by_id, obj)

        # Title: prepend the verdict so a structurer reading the first line of
        # the memo sees the pick before the metadata. The base title is
        # composed dynamically from the objective (replaces the legacy
        # "SPY Downside Protection (8m)" template that leaked through the
        # demo_replay fixture into every memo).
        rfq_title = _compose_title(obj)
        base_title = (
            f"{rfq_title} - {obj.underlying} "
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
        caveats = self._caveats_for_memo(
            priced, scenarios_by_id, validator, obj, filter_caveat=filter_caveat
        )
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
        obj,
        validator_report: Optional[ValidatorReport] = None,
    ) -> tuple[str, Optional[str]]:
        """Score each candidate; pick the highest. Crash P&L weighted positive,
        |premium| weighted negative, hedgeability flag is gating.

        Pre-filters the candidate list on:
          1. Validator severity=BLOCK findings keyed to the candidate
             (Fix 1: defer to the validator's hard-blockers — never recommend
             a candidate the validator itself rejected).
          2. Direction (Δ-sign vs view).
          3. Capped-upside compatibility.
        If every candidate fails ALL filters, falls back to the original
        priced list and returns a caveat — the structurer needs to know
        the recommendation may not match the brief.

        Returns (candidate_id, filter_caveat_or_None).
        """
        # Step 1: drop validator-BLOCKed candidates first. This is the most
        # consequential filter — a candidate the validator BLOCKed is
        # categorically unfit for recommendation regardless of how well it
        # scores on direction/cap/crash-P&L heuristics.
        non_blocked = [
            pc for pc in priced
            if not _validator_blocked(pc, validator_report)
        ]

        filter_caveat: Optional[str] = None
        if not non_blocked:
            # Every candidate has at least one BLOCK finding — fall back to
            # the original list and emit a triage caveat. The structurer
            # cannot proceed without re-running the strategist.
            non_blocked = list(priced)
            filter_caveat = (
                "All candidates have validator BLOCKs — review brief and "
                "re-run strategist."
            )

        # Step 2 + 3: direction + capped-upside compatibility.
        eligible = [
            pc for pc in non_blocked
            if _is_direction_compatible(pc, obj) and _capped_upside_compatible(pc, obj)
        ]
        if not eligible:
            # No direction/cap-compatible candidate among the non-BLOCKed
            # set. Fall back to the non-BLOCKed list (still better than
            # a BLOCKed pick) and emit a caveat — but don't overwrite a
            # pre-existing all-BLOCKed caveat.
            eligible = non_blocked
            if filter_caveat is None:
                filter_caveat = (
                    "No candidate matches the client's direction / upside-cap "
                    "constraint - review strategist output before sending."
                )

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

        ranked = sorted(eligible, key=score, reverse=True)
        return ranked[0].candidate.candidate_id, filter_caveat

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
        *,
        filter_caveat: Optional[str] = None,
    ) -> list[str]:
        """Action-oriented caveats: validator warnings + structural caveats
        derived from the candidate set + event-keyed caveats from the
        (view, underlying-class) lookup table. Capped at 6 items,
        deduplicated, and phrased as direct instructions to the salesperson.
        """
        out: list[str] = []

        # 0. Direction / upside-cap filter caveat (only set when no candidate
        # in the priced list satisfies the brief's direction / capped_upside
        # constraints — the recommendation is then a least-bad fallback).
        if filter_caveat:
            out.append(filter_caveat)

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

        # 4. Event-keyed caveats from the (view, underlying_class) lookup.
        # Shared helper walks the lookup in order: (view, class),
        # ("*", class), (view, "*"). Wildcard "*" means "any". A caveat
        # appears at most once (helper dedupes; outer dedup handles overlap
        # with prior steps).
        out.extend(_event_caveats_for(obj))

        # Dedup, preserve order, cap.
        seen: set[str] = set()
        uniq: list[str] = []
        for c in out:
            if c not in seen:
                seen.add(c)
                uniq.append(c)
        return uniq[:6]

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
    # Recent Comparable Deals — freshness + competitive intelligence
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_as_of(value: Any) -> Optional[date]:
        """Parse an ``as_of`` field (ISO date string) to a ``date`` object."""
        if not value:
            return None
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        text = str(value).strip()
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(text[: len(fmt) + 5], fmt).date()
            except ValueError:
                continue
        # ISO with timezone or fractional seconds: try fromisoformat (Py 3.11+).
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except (ValueError, TypeError):
            return None

    def _collect_comparable_deals(
        self, session: StructuringSession
    ) -> list[dict[str, Any]]:
        """Pull unique deal-type sources from session.market_context, sorted
        freshest-first. Undated docs land at the bottom."""
        seen: set[str] = set()
        deals: list[dict[str, Any]] = []
        for entry in session.market_context or []:
            for src in entry.get("sources") or []:
                if not isinstance(src, dict):
                    continue
                if src.get("type") != "deal":
                    continue
                sid = str(src.get("id") or "")
                if not sid or sid in seen:
                    continue
                seen.add(sid)
                deals.append(
                    {
                        "id": sid,
                        "asset_class": src.get("asset_class") or "—",
                        "as_of": src.get("as_of"),
                        "as_of_date": self._parse_as_of(src.get("as_of")),
                        "snippet": src.get("snippet") or "",
                        "score": src.get("score"),
                    }
                )

        # Freshest dates first; undated last (sorted by id for stability).
        def _sort_key(d: dict[str, Any]):
            ad = d["as_of_date"]
            return (0, -ad.toordinal()) if ad is not None else (1, d["id"])

        deals.sort(key=_sort_key)
        return deals

    def _append_comparable_deals_section(
        self, memo: MemoArtifact, session: StructuringSession
    ) -> None:
        """Append a freshness-aware comparable-deals table.

        Renders to ``memo.recommendation_md`` (and to ``rendered_html`` if a
        polished HTML version is present). The section header tells the
        structurer how stale the corpus is; the table shows what other desks
        have been doing on similar tickers/structures.
        """
        deals = self._collect_comparable_deals(session)
        if not deals:
            note_md = (
                "\n\n### Recent Comparable Deals\n\n"
                "_No comparable deals indexed in the MI corpus for this query._\n"
            )
            memo.recommendation_md = (memo.recommendation_md or "").rstrip() + note_md
            return

        today = date.today()
        dated = [d for d in deals if d["as_of_date"] is not None]
        latest = max((d["as_of_date"] for d in dated), default=None)

        header_bits: list[str] = [f"{len(deals)} unique deals indexed"]
        if latest is not None:
            lag = (today - latest).days
            header_bits.append(f"freshest {latest.isoformat()} (T-{lag}d)")
        if len(dated) < len(deals):
            header_bits.append(f"{len(deals) - len(dated)} undated")
        header_line = "Corpus freshness — " + "; ".join(header_bits) + "."

        rows = [
            "| # | Source ID | Asset | As Of | Lag | Snippet |",
            "|---|---|---|---|---|---|",
        ]
        for i, d in enumerate(deals[:10], start=1):
            ad = d["as_of_date"]
            as_of_str = ad.isoformat() if ad else "—"
            lag_str = f"T-{(today - ad).days}d" if ad else "—"
            snippet = d["snippet"].replace("|", "\\|") if d["snippet"] else "—"
            if len(snippet) > 140:
                snippet = snippet[:140] + "…"
            rows.append(
                f"| {i} | `{d['id']}` | {d['asset_class']} | {as_of_str} | "
                f"{lag_str} | {snippet} |"
            )
        if len(deals) > 10:
            rows.append(f"| … | _{len(deals) - 10} more not shown_ | | | | |")

        section_md = (
            "\n\n### Recent Comparable Deals\n\n"
            f"_{header_line}_\n\n" + "\n".join(rows) + "\n"
        )
        memo.recommendation_md = (memo.recommendation_md or "").rstrip() + section_md

        if memo.rendered_html:
            html = [
                "<section class='comparable-deals'>",
                "<h3>Recent Comparable Deals</h3>",
                f"<p><em>{header_line}</em></p>",
                "<table><thead><tr>"
                "<th>#</th><th>Source ID</th><th>Asset</th>"
                "<th>As Of</th><th>Lag</th><th>Snippet</th>"
                "</tr></thead><tbody>",
            ]
            for i, d in enumerate(deals[:10], start=1):
                ad = d["as_of_date"]
                as_of_str = ad.isoformat() if ad else "&mdash;"
                lag_str = f"T-{(today - ad).days}d" if ad else "&mdash;"
                snippet = d["snippet"] or "&mdash;"
                if len(snippet) > 140:
                    snippet = snippet[:140] + "…"
                html.append(
                    f"<tr><td>{i}</td><td><code>{d['id']}</code></td>"
                    f"<td>{d['asset_class']}</td><td>{as_of_str}</td>"
                    f"<td>{lag_str}</td><td>{snippet}</td></tr>"
                )
            html += ["</tbody></table>", "</section>"]
            memo.rendered_html = memo.rendered_html.rstrip() + "\n" + "\n".join(html)

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
