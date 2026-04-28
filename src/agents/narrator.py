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
from typing import Optional

from src.config.agent_config import get_agent_config

from .base import AgentError, BaseAgent
from .llm_client import LLMUnavailableError, get_llm_client
from .state import (
    MemoArtifact,
    PricedCandidate,
    ScenarioReport,
    Severity,
    StructuringSession,
    TermSheetSnippet,
    ValidatorReport,
)

logger = logging.getLogger(__name__)


_NARRATOR_SYSTEM = """You are a senior derivatives structurer writing the comparison memo a junior will hand to a client-facing salesperson.

You are given:
  * The client objective (one paragraph).
  * Three priced candidates with Greeks and scenario P&L tables.
  * The Validator's findings (warnings to surface, blockers should already be filtered).

You will return a single JSON object:

{
  "title": "<one line>",
  "objective_restatement": "<one paragraph in plain English>",
  "per_candidate_prose": [
    {"candidate_id": "<id>", "summary": "<2-3 sentences, why this works, why not, what trades off>"},
    ...
  ],
  "recommendation": {
    "candidate_id": "<id of the recommended one>",
    "paragraph": "<3 sentences. Why this one. What the client gives up. What the structurer would tell the salesperson.>"
  },
  "caveats": [<short bullet strings, max 4>]
}

Constraints:
  * Do not invent numbers that are not in the input.
  * Tone: senior desk professional. Terse, confident, no marketing fluff.
  * No legal/regulatory advice phrases.
  * The recommended candidate_id must be one of the supplied ids.
"""


class NarratorAgent(BaseAgent):
    name = "NarratorAgent"

    def _run(self, session: StructuringSession) -> StructuringSession:
        if not session.priced:
            raise AgentError("NarratorAgent requires priced candidates.")
        if session.objective is None:
            raise AgentError("NarratorAgent requires a ClientObjective.")

        memo = self._compose_deterministic(session)
        # Optional LLM polish + recommendation. Falls back to the heuristic pick.
        self._polish_with_llm(memo, session)
        session.memo = memo
        return session

    # ------------------------------------------------------------------
    # Deterministic skeleton
    # ------------------------------------------------------------------

    def _compose_deterministic(self, session: StructuringSession) -> MemoArtifact:
        obj = session.objective
        priced = session.priced
        scenarios_by_id = {s.candidate_id: s for s in session.scenarios}

        title = (
            f"3-Way Structuring Memo — {obj.underlying} "
            f"${obj.notional_usd:,.0f} {obj.horizon_days}d {obj.view}"
        )
        objective_text = self._restate_objective(obj)
        comparison_md = self._comparison_table(priced, scenarios_by_id)
        per_cand_md = [
            self._candidate_section(pc, scenarios_by_id.get(pc.candidate.candidate_id))
            for pc in priced
        ]
        recommended_id = self._heuristic_pick(priced, scenarios_by_id)
        recommendation_md = self._default_recommendation(recommended_id, priced)
        caveats = self._caveats_from_validator(session.validator)
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
    def _comparison_table(
        priced: list[PricedCandidate],
        scenarios_by_id: dict[str, ScenarioReport],
    ) -> str:
        # Markdown table — Phase 4 will turn this into HTML.
        header = (
            "| Candidate | Net Premium (bps) | Net Premium ($) | "
            "Δ | Γ | V | DV01 | Hedgeable | Crash -20% PnL ($) |\n"
            "|---|---:|---:|---:|---:|---:|---:|:---:|---:|\n"
        )
        rows = []
        for pc in priced:
            sr = scenarios_by_id.get(pc.candidate.candidate_id)
            crash_pnl = ""
            if sr:
                for row in sr.scenarios:
                    if "Crash" in row.name:
                        crash_pnl = f"{row.pnl_usd:+,.0f}"
                        break
            ok = "✓" if (sr is None or sr.hedgeability_ok) else "✗"
            rows.append(
                f"| {pc.candidate.name} | {pc.net_premium_bps:+.1f} | "
                f"{pc.net_premium:+,.0f} | "
                f"{pc.greeks.delta:+.3f} | {pc.greeks.gamma:.4f} | "
                f"{pc.greeks.vega:+.2f} | {pc.greeks.dv01:+.4f} | "
                f"{ok} | {crash_pnl} |"
            )
        return header + "\n".join(rows)

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
        lines = [
            f"STRUCTURE: {pc.candidate.name}",
            f"NOTIONAL: ${pc.candidate.notional_usd:,.0f}",
            f"NET PREMIUM: ${pc.net_premium:+,.0f} ({pc.net_premium_bps:+.1f} bps)",
            "LEGS:",
        ]
        for leg in pc.candidate.legs:
            extra = ""
            if leg.barrier_level is not None:
                extra = f" / Barrier {leg.barrier_level} ({leg.barrier_monitoring})"
            lines.append(
                f"  - {leg.option_type:>16s}  K={leg.strike:>10.2f}  qty={leg.quantity:+.0f}"
                f"  exp={leg.expiry_days}d{extra}"
            )
        lines.append(
            f"GREEKS: Δ={pc.greeks.delta:+.3f}  Γ={pc.greeks.gamma:.4f}  "
            f"V={pc.greeks.vega:+.2f}  Θ={pc.greeks.theta:+.3f}  "
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
    def _default_recommendation(rec_id: str, priced: list[PricedCandidate]) -> str:
        chosen = next((p for p in priced if p.candidate.candidate_id == rec_id), None)
        if chosen is None:
            return "Recommendation: see comparison table."
        return (
            f"Recommend **{chosen.candidate.name}**. Premium of "
            f"{chosen.net_premium_bps:+.1f}bps with "
            f"net delta {chosen.greeks.delta:+.3f}. Strongest crash P&L of the three "
            "with hedgeability cleared by Validator."
        )

    @staticmethod
    def _caveats_from_validator(report: Optional[ValidatorReport]) -> list[str]:
        if report is None:
            return []
        warnings = [
            f"{f.candidate_id or '*'}: {f.message}"
            for f in report.findings
            if f.severity == Severity.WARN
        ]
        return warnings[:4]

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
