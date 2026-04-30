"""StrategistAgent — picks 3 candidate structures and articulates the why.

Phase 1 design: deterministic *selection* via the rules table, optional LLM
*polish* of the rationale text. This keeps the demo robust (the rules table is
the IP — it always returns 3 valid candidates) while letting the LLM make the
prose feel like a senior structurer wrote it.

If GEMINI_API_KEY is unset OR DEMO_REPLAY=1, we use the templated rationale
verbatim and skip the LLM call.

Phase 2 will replace selection with an LLM that consumes the rules table as a
cached system prompt and adapts strikes/barriers off the regime; the rules
table stays as the schema-of-thought and as a fallback.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from src.config.agent_config import get_agent_config

from .base import AgentError, BaseAgent
from .llm_client import LLMUnavailableError, get_llm_client
from .rules import build_candidates, match_rules
from .state import Candidate, ClientObjective, MarketRegime, StructuringSession

logger = logging.getLogger(__name__)


# A senior-structurer prefix appended to candidate rationales when the
# market-window query returns a CLOSED verdict for the underlying.
_CLOSED_WINDOW_WARNING = (
    "Market-window check: dealer corpus flags issuance window as CLOSED for this "
    "underlier and tenor. Treat the indicative price as a soft level — a live "
    "axe is unlikely. Recommend deferring or re-shaping. "
)

_CLOSED_PATTERN = re.compile(r"\bCLOSED\b")


_STRATEGIST_POLISH_SYSTEM = """You are a senior derivatives structurer at an institutional bank.

You will be given a client objective, a market regime snapshot, and three pre-selected candidate structures (each with a draft rationale). Your job is to polish each rationale into 2–3 crisp sentences that a senior structurer would say to a junior. Keep the financial substance unchanged.

Return a single JSON object:

{
  "candidates": [
    {"candidate_id": "<id>", "polished_rationale": "<2-3 sentences, plain English, no markdown>"},
    ...
  ]
}

Constraints:
  * Do not invent numbers that are not in the input.
  * Do not change which structures are recommended.
  * Tone: confident, terse, desk-floor cadence.
  * No outright sales claims. No "guaranteed". No advice. Statements of structural fact only.
"""


class StrategistAgent(BaseAgent):
    name = "StrategistAgent"

    def __init__(self, mi: Optional[Any] = None) -> None:
        self.mi = mi

    def _run(self, session: StructuringSession) -> StructuringSession:
        if session.objective is None:
            raise AgentError("StrategistAgent requires a ClientObjective.")
        if session.regime is None:
            raise AgentError("StrategistAgent requires a MarketRegime.")

        # Market-window check happens BEFORE candidate construction so the
        # rationale text can reflect the verdict ("CLOSED" → softened tone).
        window_closed = self._check_market_window(session)

        rule = match_rules(session.objective, session.regime)
        candidates = build_candidates(rule, session.objective, session.regime)

        if not candidates:
            raise AgentError("No candidates produced from rules table.")

        # Optional polish via LLM. Failures are silently absorbed — we always
        # have the templated rationale.
        polished = self._polish_rationales(candidates, session.objective, session.regime)
        for cand in candidates:
            replacement = polished.get(cand.candidate_id)
            if replacement:
                cand.rationale = replacement

        if window_closed:
            for cand in candidates:
                cand.rationale = _CLOSED_WINDOW_WARNING + cand.rationale

        session.candidates = candidates
        return session

    # ------------------------------------------------------------------
    # Market-window check (RAG)
    # ------------------------------------------------------------------

    def _check_market_window(self, session: StructuringSession) -> bool:
        """Returns True iff the corpus flags the market as CLOSED for this
        underlier. Always safe to call — returns False on any failure or
        when MI is disabled.
        """
        if self.mi is None or session.objective is None:
            return False
        try:
            qr = self.mi.query_market_window(
                asset_class=session.objective.underlying,
                context=(
                    f"horizon {session.objective.horizon_days}d, view "
                    f"{session.objective.view}, premium budget "
                    f"{session.objective.budget_bps_notional}bps"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Strategist MI query_market_window failed: %s", exc)
            return False

        self._record_market_context(session, intent="market_window", qr=qr)

        answer = (getattr(qr, "answer", "") or "").upper()
        return bool(_CLOSED_PATTERN.search(answer))

    # ------------------------------------------------------------------
    # Optional LLM polish
    # ------------------------------------------------------------------

    def _polish_rationales(
        self,
        candidates: list[Candidate],
        objective: ClientObjective,
        regime: MarketRegime,
    ) -> dict[str, str]:
        cfg = get_agent_config()
        if not (cfg.has_gemini or cfg.demo_replay):
            return {}

        client = get_llm_client()
        prompt = _build_polish_prompt(objective, regime, candidates)
        try:
            res = client.complete(
                agent_name=self.name,
                model=cfg.model_strategist,
                system=_STRATEGIST_POLISH_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                json_mode=True,
                replay_key="StrategistAgent:polish",
            )
        except LLMUnavailableError:
            return {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Strategist polish failed: %s", exc)
            return {}

        parsed = res.parsed_json
        if not isinstance(parsed, dict):
            return {}
        out: dict[str, str] = {}
        for entry in parsed.get("candidates", []) or []:
            cid = entry.get("candidate_id")
            text = entry.get("polished_rationale")
            if cid and text:
                out[cid] = str(text).strip()
        return out


def _build_polish_prompt(
    obj: ClientObjective, regime: MarketRegime, candidates: list[Candidate]
) -> str:
    cand_lines = []
    for c in candidates:
        legs_str = "; ".join(
            f"{l.option_type} K={l.strike}"
            + (f" B={l.barrier_level}" if l.barrier_level is not None else "")
            + f" qty={l.quantity:+.0f}"
            for l in c.legs
        )
        cand_lines.append(
            f"- candidate_id={c.candidate_id} kind={c.kind.value} name=\"{c.name}\" "
            f"legs=({legs_str}) draft_rationale=\"{c.rationale}\""
        )
    objective_block = (
        f"underlying={obj.underlying} notional_usd={obj.notional_usd:,.0f} "
        f"view={obj.view} horizon_days={obj.horizon_days} "
        f"budget_bps={obj.budget_bps_notional} premium_tol={obj.premium_tolerance} "
        f"capped_upside_ok={obj.capped_upside_ok} barrier_ok={obj.barrier_appetite}"
    )
    regime_block = (
        f"spot={regime.spot} q={regime.dividend_yield} r={regime.risk_free_rate} "
        f"realised_vol_30d={regime.realised_vol_30d} vol_regime={regime.vol_regime} "
        f"earnings_proximity={regime.earnings_proximity}"
    )
    return (
        "OBJECTIVE:\n" + objective_block + "\n\n"
        "REGIME:\n" + regime_block + "\n\n"
        "CANDIDATES:\n" + "\n".join(cand_lines)
    )
