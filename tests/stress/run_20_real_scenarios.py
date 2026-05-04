"""20-scenario real-life validation of the structuring co-pilot.

Drives 20 desk-realistic RFQs through Intake -> Gate A -> Strategist -> Gate B
-> Pricing/Scenario/Validator/Narrator -> Gate C -> DONE in DEMO_REPLAY mode,
captures correctness signals, runs an MC overlay on one European leg of the
recommended candidate, and grades each run PASS/WARN/FAIL.

Run::

    python tests/stress/run_20_real_scenarios.py

Outputs to stdout (per-scenario one-line + aggregate footer). Pipe through
``tee`` for the run log.
"""

from __future__ import annotations

import json
import math
import os
import sys
import traceback
from typing import Any
from unittest.mock import patch

# Must set BEFORE importing src.* so DEMO_REPLAY is honored on import.
os.environ.setdefault("DEMO_REPLAY", "1")
os.environ.setdefault("GEMINI_API_KEY", "")

# Windows cp1252 cannot encode the symbols we sometimes print; flip to UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

import numpy as np

# Repo root on sys.path so `src.*` resolves when run as a script.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.agents.orchestrator import (  # noqa: E402
    OrchestratorAgent,
    SessionStore,
)
from src.agents.state import (  # noqa: E402
    Gate,
    SessionStatus,
    Severity,
    StructureKind,
)
from src.agents import llm_client  # noqa: E402
from src.config import agent_config  # noqa: E402


# ---------------------------------------------------------------------------
# Underlying-class lookup (matches src/agents/narrator.py + harness spec)
# ---------------------------------------------------------------------------
UNDERLYING_CLASS: dict[str, str] = {
    "NVDA": "TECH", "AAPL": "TECH", "TSLA": "TECH", "COIN": "TECH",
    "META": "TECH", "MSFT": "TECH",
    "SPY": "BROAD", "QQQ": "BROAD", "IWM": "BROAD",
    "XLE": "ENERGY", "XOP": "ENERGY",
    "XLV": "HEALTHCARE", "PFE": "HEALTHCARE",
    "XLF": "FINANCIALS", "JPM": "FINANCIALS", "KRE": "FINANCIALS",
    "XLP": "STAPLES",
    "XLU": "UTILITIES",
    "XLI": "INDUSTRIALS",
    "XLRE": "REIT",
    "TLT": "DURATION", "IEF": "DURATION",
    "EWJ": "FX-EQUITY",
}

# Mirrors NarratorAgent.EVENT_CAVEATS keys for the "event-keyed caveat lookup"
# applicability check. We don't reproduce caveat strings; we just record which
# (view, class) tuples have at least one entry available so we know whether
# the lookup is *expected* to fire for a given scenario.
EVENT_CAVEAT_KEYS: set[tuple[str, str]] = {
    ("earnings_hedge", "*"),
    ("crash_hedge", "*"),
    ("protect_gains", "REIT"),
    ("bearish", "ENERGY"),
    ("mildly_bearish", "ENERGY"),
    ("neutral", "HEALTHCARE"),
    ("*", "REIT"),
    ("*", "STAPLES"),
}


def has_event_caveat_key(view: str, klass: str) -> bool:
    """True if NarratorAgent.EVENT_CAVEATS has any entry for (view, class) /
    ("*", class) / (view, "*"). Mirrors the narrator's lookup logic."""
    for key in ((view, klass), ("*", klass), (view, "*")):
        if key in EVENT_CAVEAT_KEYS:
            return True
    return False


_BULLISH_VIEWS = {"bullish", "mildly_bullish"}
_BEARISH_VIEWS = {
    "bearish", "mildly_bearish", "protect_gains", "crash_hedge", "earnings_hedge",
}
_DELTA_SLOP = 0.05


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
SCENARIOS: list[dict[str, Any]] = [
    {
        "name": "NVDA Q4'25 earnings print (IV crush risk)",
        "date_anchor": "2026-02-26 print",
        "ticker": "NVDA", "view": "earnings_hedge", "notional_usd": 50_000_000,
        "horizon_days": 14, "budget_bps_notional": 120, "premium_tolerance": "low",
        "capped_upside_ok": False, "barrier_appetite": True,
        "constraints": ["expiry post-print"],
        "spot": 125.0, "vol_30d": 0.42, "vol_90d": 0.36, "dividend_yield": 0.001,
        "narrative": "Hedge ahead of NVDA Q4 print; IV crush risk on long vega.",
        "expected_kinds": {StructureKind.LONG_PUT, StructureKind.KI_PUT, StructureKind.PUT_SPREAD},
    },
    {
        "name": "SVB-style regional bank tail (KRE)",
        "date_anchor": "Q1'26 (March 2023 analogue)",
        "ticker": "KRE", "view": "crash_hedge", "notional_usd": 40_000_000,
        "horizon_days": 60, "budget_bps_notional": 150, "premium_tolerance": "medium",
        "capped_upside_ok": False, "barrier_appetite": True,
        "constraints": [],
        "spot": 58.0, "vol_30d": 0.32, "vol_90d": 0.28, "dividend_yield": 0.030,
        "narrative": "Regional-bank tail crash hedge analogue to SVB Mar 2023.",
        "expected_kinds": {StructureKind.KO_PUT, StructureKind.LONG_PUT, StructureKind.PUT_SPREAD},
    },
    {
        "name": "Iran/Israel oil upside (XLE)",
        "date_anchor": "April 2024 analogue",
        "ticker": "XLE", "view": "bullish", "notional_usd": 75_000_000,
        "horizon_days": 45, "budget_bps_notional": 180, "premium_tolerance": "medium",
        "capped_upside_ok": False, "barrier_appetite": False,
        "constraints": [],
        "spot": 98.0, "vol_30d": 0.28, "vol_90d": 0.26, "dividend_yield": 0.032,
        "narrative": "Energy upside on Mideast escalation; no caps, no barriers.",
        "expected_kinds": {StructureKind.LONG_CALL, StructureKind.CALL_SPREAD, StructureKind.RISK_REVERSAL},
    },
    {
        "name": "Powell pivot lock-in (XLF)",
        "date_anchor": "Aug 2025 Jackson Hole",
        "ticker": "XLF", "view": "protect_gains", "notional_usd": 300_000_000,
        "horizon_days": 270, "budget_bps_notional": 0, "premium_tolerance": "zero_cost_only",
        "capped_upside_ok": True, "barrier_appetite": False,
        "constraints": ["board policy: zero-cost only"],
        "spot": 52.0, "vol_30d": 0.18, "vol_90d": 0.19, "dividend_yield": 0.022,
        "narrative": "Lock financial-sector gains via zero-cost collar.",
        "expected_kinds": {StructureKind.ZERO_COST_COLLAR, StructureKind.COLLAR},
    },
    {
        "name": "Post-Trump small-caps melt-up (IWM)",
        "date_anchor": "Nov 2024 election",
        "ticker": "IWM", "view": "bullish", "notional_usd": 100_000_000,
        "horizon_days": 180, "budget_bps_notional": 200, "premium_tolerance": "medium",
        "capped_upside_ok": False, "barrier_appetite": False,
        "constraints": ["no caps"],
        "spot": 225.0, "vol_30d": 0.24, "vol_90d": 0.22, "dividend_yield": 0.011,
        "narrative": "Russell 2000 melt-up post-election; outright bullish, no caps.",
        "expected_kinds": {StructureKind.LONG_CALL, StructureKind.CALL_SPREAD, StructureKind.RISK_REVERSAL},
    },
    {
        "name": "Boeing strike industrial drag (XLI)",
        "date_anchor": "Sep 2025 analogue",
        "ticker": "XLI", "view": "mildly_bearish", "notional_usd": 80_000_000,
        "horizon_days": 90, "budget_bps_notional": 70, "premium_tolerance": "low",
        "capped_upside_ok": True, "barrier_appetite": False,
        "constraints": [],
        "spot": 145.0, "vol_30d": 0.18, "vol_90d": 0.19, "dividend_yield": 0.014,
        "narrative": "Mild industrial drawdown hedge with capped-OK budget.",
        "expected_kinds": {StructureKind.PUT_SPREAD, StructureKind.COLLAR, StructureKind.PUT_SPREAD_COLLAR},
    },
    {
        "name": "BoJ YCC adjustment yen hedge (EWJ)",
        "date_anchor": "June 2023 analogue",
        "ticker": "EWJ", "view": "neutral", "notional_usd": 60_000_000,
        "horizon_days": 60, "budget_bps_notional": 50, "premium_tolerance": "medium",
        "capped_upside_ok": True, "barrier_appetite": False,
        "constraints": [],
        "spot": 72.0, "vol_30d": 0.16, "vol_90d": 0.15, "dividend_yield": 0.018,
        "narrative": "Neutral yield-collect; BoJ regime tweak risk.",
        "expected_kinds": {StructureKind.COVERED_CALL, StructureKind.SHORT_STRANGLE, StructureKind.IRON_CONDOR},
    },
    {
        "name": "CPI surprise duration tail (TLT)",
        "date_anchor": "March 2026",
        "ticker": "TLT", "view": "bearish", "notional_usd": 50_000_000,
        "horizon_days": 30, "budget_bps_notional": 80, "premium_tolerance": "low",
        "capped_upside_ok": False, "barrier_appetite": True,
        "constraints": [],
        "spot": 92.0, "vol_30d": 0.14, "vol_90d": 0.15, "dividend_yield": 0.038,
        "narrative": "Bearish duration on CPI upside surprise.",
        "expected_kinds": {StructureKind.LONG_PUT, StructureKind.PUT_SPREAD, StructureKind.KI_PUT},
    },
    {
        "name": "NDX post-correction recovery (QQQ)",
        "date_anchor": "Oct 2022 analogue",
        "ticker": "QQQ", "view": "bullish", "notional_usd": 150_000_000,
        "horizon_days": 120, "budget_bps_notional": 180, "premium_tolerance": "medium",
        "capped_upside_ok": False, "barrier_appetite": False,
        "constraints": [],
        "spot": 485.0, "vol_30d": 0.22, "vol_90d": 0.20, "dividend_yield": 0.006,
        "narrative": "Recovery rally re-engagement on NDX post-correction.",
        "expected_kinds": {StructureKind.LONG_CALL, StructureKind.CALL_SPREAD, StructureKind.RISK_REVERSAL},
    },
    {
        "name": "COVID-style tail hedge (SPY)",
        "date_anchor": "March 2020 analogue (prophylactic)",
        "ticker": "SPY", "view": "crash_hedge", "notional_usd": 500_000_000,
        "horizon_days": 365, "budget_bps_notional": 80, "premium_tolerance": "medium",
        "capped_upside_ok": False, "barrier_appetite": True,
        "constraints": [],
        "spot": 575.0, "vol_30d": 0.16, "vol_90d": 0.18, "dividend_yield": 0.013,
        "narrative": "Year-long prophylactic crash hedge a la COVID-2020 shock.",
        "expected_kinds": {StructureKind.KO_PUT, StructureKind.LONG_PUT, StructureKind.PUT_SPREAD},
    },
    {
        "name": "Healthcare drug-pricing election noise (XLV)",
        "date_anchor": "Q3'25 analogue",
        "ticker": "XLV", "view": "mildly_bearish", "notional_usd": 120_000_000,
        "horizon_days": 150, "budget_bps_notional": 80, "premium_tolerance": "low",
        "capped_upside_ok": True, "barrier_appetite": False,
        "constraints": [],
        "spot": 155.0, "vol_30d": 0.16, "vol_90d": 0.17, "dividend_yield": 0.014,
        "narrative": "Drug-pricing election noise mildly bearish on XLV.",
        "expected_kinds": {StructureKind.PUT_SPREAD, StructureKind.COLLAR, StructureKind.PUT_SPREAD_COLLAR},
    },
    {
        "name": "TSLA Q4 print IV-crush short-vol",
        "date_anchor": "TSLA Q4 print",
        "ticker": "TSLA", "view": "neutral", "notional_usd": 25_000_000,
        "horizon_days": 14, "budget_bps_notional": 50, "premium_tolerance": "high",
        "capped_upside_ok": True, "barrier_appetite": False,
        "constraints": ["post-print only"],
        "spot": 240.0, "vol_30d": 0.55, "vol_90d": 0.45, "dividend_yield": 0.0,
        "narrative": "Sell vol post-print on TSLA, neutral view.",
        "expected_kinds": {StructureKind.SHORT_STRANGLE, StructureKind.IRON_CONDOR, StructureKind.COVERED_CALL},
    },
    {
        "name": "Oil supply-glut bearish E&P (XOP)",
        "date_anchor": "Oil supply glut",
        "ticker": "XOP", "view": "bearish", "notional_usd": 40_000_000,
        "horizon_days": 90, "budget_bps_notional": 120, "premium_tolerance": "medium",
        "capped_upside_ok": False, "barrier_appetite": True,
        "constraints": [],
        "spot": 140.0, "vol_30d": 0.30, "vol_90d": 0.28, "dividend_yield": 0.026,
        "narrative": "Bearish E&P hedge on supply glut.",
        "expected_kinds": {StructureKind.LONG_PUT, StructureKind.PUT_SPREAD, StructureKind.KI_PUT},
    },
    {
        "name": "JPM single-name pre-earnings yield",
        "date_anchor": "JPM pre-earnings",
        "ticker": "JPM", "view": "neutral", "notional_usd": 30_000_000,
        "horizon_days": 45, "budget_bps_notional": 40, "premium_tolerance": "medium",
        "capped_upside_ok": True, "barrier_appetite": False,
        "constraints": [],
        "spot": 215.0, "vol_30d": 0.20, "vol_90d": 0.22, "dividend_yield": 0.025,
        "narrative": "Single-name yield collection ahead of JPM print.",
        "expected_kinds": {StructureKind.COVERED_CALL, StructureKind.SHORT_STRANGLE, StructureKind.IRON_CONDOR},
    },
    {
        "name": "COIN crypto-beta earnings hedge",
        "date_anchor": "COIN Q-print",
        "ticker": "COIN", "view": "earnings_hedge", "notional_usd": 15_000_000,
        "horizon_days": 14, "budget_bps_notional": 200, "premium_tolerance": "medium",
        "capped_upside_ok": False, "barrier_appetite": True,
        "constraints": [],
        "spot": 185.0, "vol_30d": 0.65, "vol_90d": 0.55, "dividend_yield": 0.0,
        "narrative": "Crypto-beta single-name earnings hedge with high IV.",
        "expected_kinds": {StructureKind.LONG_PUT, StructureKind.KI_PUT, StructureKind.PUT_SPREAD},
    },
    {
        "name": "Treasury auction duration tail (IEF)",
        "date_anchor": "Treasury auction",
        "ticker": "IEF", "view": "mildly_bearish", "notional_usd": 80_000_000,
        "horizon_days": 60, "budget_bps_notional": 40, "premium_tolerance": "low",
        "capped_upside_ok": True, "barrier_appetite": False,
        "constraints": [],
        "spot": 95.0, "vol_30d": 0.08, "vol_90d": 0.09, "dividend_yield": 0.041,
        "narrative": "Belly-of-curve mild bearish position via IEF.",
        "expected_kinds": {StructureKind.PUT_SPREAD, StructureKind.COLLAR, StructureKind.COVERED_CALL},
    },
    {
        "name": "AAPL services multiple expansion",
        "date_anchor": "AAPL services theme",
        "ticker": "AAPL", "view": "mildly_bullish", "notional_usd": 50_000_000,
        "horizon_days": 120, "budget_bps_notional": 120, "premium_tolerance": "medium",
        "capped_upside_ok": False, "barrier_appetite": False,
        "constraints": [],
        "spot": 225.0, "vol_30d": 0.22, "vol_90d": 0.20, "dividend_yield": 0.005,
        "narrative": "Mildly bullish AAPL on services rerate.",
        "expected_kinds": {StructureKind.LONG_CALL, StructureKind.CALL_SPREAD, StructureKind.RISK_REVERSAL},
    },
    {
        "name": "SPY year-end pin neutral yield",
        "date_anchor": "Dec 2025 pin",
        "ticker": "SPY", "view": "neutral", "notional_usd": 200_000_000,
        "horizon_days": 30, "budget_bps_notional": 30, "premium_tolerance": "high",
        "capped_upside_ok": True, "barrier_appetite": False,
        "constraints": [],
        "spot": 575.0, "vol_30d": 0.13, "vol_90d": 0.15, "dividend_yield": 0.013,
        "narrative": "Year-end pin yield-collect on SPY.",
        "expected_kinds": {StructureKind.IRON_CONDOR, StructureKind.SHORT_STRANGLE, StructureKind.COVERED_CALL},
    },
    {
        "name": "XLU rate-cut play",
        "date_anchor": "Rate-cut theme",
        "ticker": "XLU", "view": "bullish", "notional_usd": 60_000_000,
        "horizon_days": 120, "budget_bps_notional": 140, "premium_tolerance": "medium",
        "capped_upside_ok": False, "barrier_appetite": False,
        "constraints": [],
        "spot": 78.0, "vol_30d": 0.16, "vol_90d": 0.17, "dividend_yield": 0.030,
        "narrative": "Bullish utilities on rate-cut tailwind.",
        "expected_kinds": {StructureKind.LONG_CALL, StructureKind.CALL_SPREAD, StructureKind.RISK_REVERSAL},
    },
    {
        "name": "PFE patent-cliff bearish (LOE)",
        "date_anchor": "PFE LOE 2026-2030",
        "ticker": "PFE", "view": "bearish", "notional_usd": 35_000_000,
        "horizon_days": 180, "budget_bps_notional": 120, "premium_tolerance": "medium",
        "capped_upside_ok": False, "barrier_appetite": True,
        "constraints": [],
        "spot": 27.0, "vol_30d": 0.22, "vol_90d": 0.23, "dividend_yield": 0.060,
        "narrative": "Patent-cliff bearish hedge on PFE.",
        "expected_kinds": {StructureKind.LONG_PUT, StructureKind.PUT_SPREAD, StructureKind.KI_PUT},
    },
]


# ---------------------------------------------------------------------------
# DEMO_REPLAY plumbing
# ---------------------------------------------------------------------------
def _install_intake_replay(payload: dict[str, Any]) -> None:
    client = llm_client.get_llm_client()
    if client._replay_cache is None:  # noqa: SLF001
        client._load_replay_cache()  # noqa: SLF001
    client._replay_cache["IntakeAgent:nl"] = {  # noqa: SLF001
        "text": json.dumps(payload),
        "stop_reason": "end_turn",
    }


def _fake_market(spot: float, vol_30d: float, vol_90d: float, div: float):
    return patch(
        "src.agents.orchestrator.market_data.fetch_market_params",
        return_value={
            "spot_price": spot,
            "dividend_yield": div,
            "volatility_30d": vol_30d,
            "volatility_90d": vol_90d,
            "source": "fallback",
        },
    )


# ---------------------------------------------------------------------------
# MC simulator (terminal only — sufficient for European-leg parity)
# ---------------------------------------------------------------------------
def _simulate_terminal(
    *,
    S0: float,
    r: float,
    q: float,
    sigma: float,
    T: float,
    n_paths: int,
    seed: int = 13,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    drift = (r - q - 0.5 * sigma * sigma) * T
    diff = sigma * math.sqrt(T)
    z = rng.standard_normal(n_paths)
    return S0 * np.exp(drift + diff * z)


# ---------------------------------------------------------------------------
# One-scenario driver
# ---------------------------------------------------------------------------
def _run_one(scn: dict[str, Any]) -> dict[str, Any]:
    """Execute one scenario, return result dict (never raises)."""
    result: dict[str, Any] = {
        "name": scn["name"],
        "ticker": scn["ticker"],
        "view": scn["view"],
        "grade": "FAIL",
        "note": "",
        "verdict_line": None,
        "recommended_kind": None,
        "net_premium_bps": None,
        "within_budget": False,
        "validator_findings": 0,
        "validator_severity_tally": {"info": 0, "warn": 0, "block": 0},
        "direction_aligned": False,
        "capped_upside_compliant": False,
        "caveats_event_keyed": False,
        "title_includes_underlying": False,
        "barrier_honored": None,  # None when barrier_appetite=False
        "mc_drift_pct": float("nan"),
        "exception": None,
    }

    intake_payload = {
        "underlying": scn["ticker"],
        "notional_usd": scn["notional_usd"],
        "view": scn["view"],
        "horizon_days": scn["horizon_days"],
        "budget_bps_notional": scn["budget_bps_notional"],
        "premium_tolerance": scn["premium_tolerance"],
        "capped_upside_ok": scn["capped_upside_ok"],
        "barrier_appetite": scn["barrier_appetite"],
        "constraints": list(scn["constraints"]),
        "clarifications_needed": [],
    }

    try:
        agent_config.reload()
        llm_client.reset_llm_client()
        _install_intake_replay(intake_payload)

        with _fake_market(
            spot=scn["spot"], vol_30d=scn["vol_30d"], vol_90d=scn["vol_90d"],
            div=scn["dividend_yield"],
        ):
            orch = OrchestratorAgent(store=SessionStore())
            session = orch.start_session(intake_nl=scn["narrative"])
            if session.status != SessionStatus.AWAITING_GATE_A:
                result["note"] = f"intake stalled: {session.last_error}"
                result["exception"] = "intake stall"
                return result

            session = orch.decide_gate(session.session_id, Gate.A, approved=True)
            if session.status != SessionStatus.AWAITING_GATE_B:
                result["note"] = f"gate A->B failed: {session.last_error}"
                result["exception"] = "gateA stall"
                return result

            session = orch.decide_gate(session.session_id, Gate.B, approved=True)
            if session.status != SessionStatus.AWAITING_GATE_C:
                result["note"] = f"gate B->C failed: {session.last_error}"
                result["exception"] = "gateB stall"
                return result

            memo = session.memo
            priced = session.priced
            regime = session.regime

            # ----- recommended candidate -----
            rec_id = memo.recommended_candidate_id
            rec = next((p for p in priced if p.candidate.candidate_id == rec_id), None)
            if rec is None:
                result["note"] = f"recommended_candidate_id {rec_id!r} not in priced[]"
                return result
            cand = rec.candidate

            result["verdict_line"] = memo.title
            result["recommended_kind"] = cand.kind.value
            result["net_premium_bps"] = float(rec.net_premium_bps)

            # ----- within_budget (|abs| <= budget+10) -----
            budget = float(scn["budget_bps_notional"])
            result["within_budget"] = abs(rec.net_premium_bps) <= budget + 10.0

            # ----- validator findings -----
            findings = list(session.validator.findings) if session.validator else []
            result["validator_findings"] = len(findings)
            for f in findings:
                key = f.severity.value if isinstance(f.severity, Severity) else str(f.severity)
                if key in result["validator_severity_tally"]:
                    result["validator_severity_tally"][key] += 1

            # ----- direction alignment (mirrors validator invariant) -----
            delta = rec.greeks.delta
            view = scn["view"]
            if view in _BULLISH_VIEWS:
                result["direction_aligned"] = delta >= -_DELTA_SLOP
            elif view in _BEARISH_VIEWS:
                result["direction_aligned"] = delta <= +_DELTA_SLOP
            else:
                # neutral: no Δ-sign constraint -> aligned by default.
                result["direction_aligned"] = True

            # ----- capped-upside compliance -----
            if scn["capped_upside_ok"]:
                result["capped_upside_compliant"] = True
            else:
                short_call_present = any(
                    leg.quantity < 0 and leg.option_type.endswith("_call")
                    for leg in cand.legs
                )
                result["capped_upside_compliant"] = not short_call_present

            # ----- caveats event-keyed -----
            klass = UNDERLYING_CLASS.get(scn["ticker"], "OTHER")
            lookup_has_key = has_event_caveat_key(view, klass)
            if lookup_has_key:
                # We have ground-truth caveats for this scenario; check that
                # at least one Narrator caveat matches one of the known event-
                # keyed strings. We do a lightweight substring match since
                # caveats may be polished by the LLM.
                from src.agents.narrator import EVENT_CAVEATS
                expected_strings: list[str] = []
                for key in ((view, klass), ("*", klass), (view, "*")):
                    expected_strings.extend(EVENT_CAVEATS.get(key, []))
                caveats_blob = " || ".join(memo.caveats).lower()
                hit = False
                for s in expected_strings:
                    # short distinctive token from the caveat
                    tokens = [
                        "iv crush", "pin risk", "theta drag", "fomc",
                        "opec", "earnings cycle", "forward, not spot",
                        "forward sits above spot",
                    ]
                    for t in tokens:
                        if t in s.lower() and t in caveats_blob:
                            hit = True
                            break
                    if hit:
                        break
                result["caveats_event_keyed"] = hit
            else:
                # No expected event caveat — count as N/A (treat as True so
                # we don't penalize). The aggregate metric only divides over
                # scenarios where the lookup *has* a key.
                result["caveats_event_keyed"] = None

            # ----- title regression check -----
            # Two checks combined:
            #   (a) underlying ticker present in title
            #   (b) title not the legacy hardcoded template
            #       "SPY Downside Protection (8m)" leaked from a fixture.
            # The hardcoded "(8m)" tenor is the smoking gun — only Narrator's
            # _compose_title produces it correctly from horizon_days; if the
            # rendered title shows "(8m)" but objective.horizon_days != ~240,
            # we have a leak.
            title = memo.title or ""
            ticker_uc = scn["ticker"].upper()
            ticker_present = ticker_uc in title.upper()
            horizon_days = scn["horizon_days"]
            # Compute the canonical tenor label our composer would emit.
            if horizon_days < 90:
                expected_tenor = f"{horizon_days}d"
            else:
                expected_tenor = f"{horizon_days // 30}m"
            # Detect leaked legacy template: contains "Downside Protection"
            # AND the tenor label inside parens does NOT match the canonical
            # one for this scenario's horizon.
            import re as _re
            tenor_match = _re.search(r"\(([0-9]+[dm])\)", title)
            tenor_in_title = tenor_match.group(1) if tenor_match else None
            template_leak = (
                tenor_in_title is not None
                and tenor_in_title != expected_tenor
            )
            result["title_includes_underlying"] = (
                ticker_present and not template_leak
            )
            result["_title_template_leak"] = template_leak
            result["_title_tenor_in_title"] = tenor_in_title
            result["_title_tenor_expected"] = expected_tenor

            # ----- barrier honored (only when barrier_appetite=True) -----
            if scn["barrier_appetite"]:
                any_barrier = any(
                    leg.option_type.startswith(("knockout_", "knockin_"))
                    for p in priced
                    for leg in p.candidate.legs
                )
                result["barrier_honored"] = any_barrier
            else:
                result["barrier_honored"] = None

            # ----- expected-kinds direction-fit -----
            kind_in_expected = cand.kind in scn["expected_kinds"]
            result["kind_in_expected"] = kind_in_expected

            # ----- MC parity on a European leg of recommended -----
            S0 = float(regime.spot)
            r = float(regime.risk_free_rate)
            q = float(regime.dividend_yield)
            sigma = float(
                regime.atm_iv or regime.realised_vol_30d
                or regime.realised_vol_90d or scn["vol_30d"]
            )
            eu_leg_idx = next(
                (j for j, leg in enumerate(cand.legs) if leg.option_type.startswith("european_")),
                None,
            )
            if eu_leg_idx is not None:
                leg = cand.legs[eu_leg_idx]
                T = leg.expiry_days / 365.0
                ST = _simulate_terminal(
                    S0=S0, r=r, q=q, sigma=sigma, T=T,
                    n_paths=50_000, seed=13,
                )
                opt = leg.option_type.split("_")[1]
                if opt == "put":
                    payoff = np.maximum(leg.strike - ST, 0.0)
                else:
                    payoff = np.maximum(ST - leg.strike, 0.0)
                disc = math.exp(-r * T)
                mc_pershare = float(disc * payoff.mean())
                ql_pershare = (
                    rec.per_leg_prices[eu_leg_idx] / abs(leg.quantity)
                    if eu_leg_idx < len(rec.per_leg_prices) and leg.quantity
                    else float("nan")
                )
                if math.isfinite(mc_pershare) and math.isfinite(ql_pershare) and abs(ql_pershare) > 1e-9:
                    drift_pct = abs((mc_pershare - ql_pershare) / ql_pershare) * 100.0
                    result["mc_drift_pct"] = drift_pct
                else:
                    result["mc_drift_pct"] = float("nan")
            else:
                # No European leg on the recommended candidate (e.g. pure
                # KO/KI structure). Leave drift NaN; MC parity is effectively
                # N/A for this scenario.
                result["mc_drift_pct"] = float("nan")

            # ----- grade -----
            mc_drift = result["mc_drift_pct"]
            mc_ok = (not math.isfinite(mc_drift)) or mc_drift < 2.0

            # FAIL conditions (any one of)
            wrong_direction = not result["direction_aligned"]
            cap_violation = not result["capped_upside_compliant"]
            title_leak = not result["title_includes_underlying"]
            if wrong_direction or cap_violation or title_leak:
                result["grade"] = "FAIL"
                reasons = []
                if wrong_direction:
                    reasons.append("wrong-direction Δ")
                if cap_violation:
                    reasons.append("capped-upside contradicted")
                if title_leak:
                    reasons.append("title leak")
                result["note"] = "; ".join(reasons)
            else:
                # PASS = within_budget AND direction_aligned AND
                #        capped_upside_compliant AND title_includes_underlying
                #        AND MC drift <2% AND validator did its job.
                # Validator-job: catches anything OR no faults exist. We treat
                # this as: if budget breach exists (>budget+10), validator
                # must have BLOCKed it. We already gate within_budget, so if
                # we are within_budget, validator's "did its job" is trivially
                # satisfied. If we are out of budget by >10bps, validator
                # should be blocking — check tally.
                validator_did_job = True
                if not result["within_budget"]:
                    # Should be a BLOCK finding present.
                    validator_did_job = result["validator_severity_tally"]["block"] > 0

                pass_signals = [
                    result["within_budget"],
                    result["direction_aligned"],
                    result["capped_upside_compliant"],
                    result["title_includes_underlying"],
                    mc_ok,
                    validator_did_job,
                ]
                if all(pass_signals):
                    result["grade"] = "PASS"
                    result["note"] = ""
                else:
                    result["grade"] = "WARN"
                    misses = []
                    if not result["within_budget"]:
                        misses.append(f"over budget ({rec.net_premium_bps:+.1f}bps)")
                    if not mc_ok:
                        misses.append(f"MC drift {mc_drift:.2f}%")
                    if not validator_did_job:
                        misses.append("validator missed budget breach")
                    result["note"] = "; ".join(misses) or "WARN"

            # Gate C → DONE (best-effort; not load-bearing for grade)
            try:
                orch.decide_gate(session.session_id, Gate.C, approved=True)
            except Exception:
                pass

            return result

    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc(limit=2)
        result["grade"] = "FAIL"
        result["note"] = f"engine/orchestrator exception: {exc}"
        result["exception"] = f"{type(exc).__name__}: {exc}\n{tb}"
        return result


# ---------------------------------------------------------------------------
# Aggregate + print
# ---------------------------------------------------------------------------
def _print_per_scenario(i: int, r: dict[str, Any]) -> None:
    bps = r.get("net_premium_bps")
    bps_s = f"{bps:+.1f}bps" if isinstance(bps, (int, float)) and bps == bps else "n/a"
    drift = r.get("mc_drift_pct")
    drift_s = f"{drift:.2f}%" if isinstance(drift, (int, float)) and drift == drift else "n/a"
    rec = r.get("recommended_kind") or "?"
    print(
        f"[{i:2d}] {r['grade']:4} {r['ticker']:<5} {r['view']:<16} "
        f"rec={rec:<22} {bps_s:>10} drift={drift_s:>6} "
        f"val={r['validator_findings']:>2}  {r['note']}"
    )


def main() -> int:
    print("=" * 100)
    print("20-scenario real-life validation run — DEMO_REPLAY mode")
    print("Branch: integration/stress-test-fixes  HEAD: c6f2e29  Date: 2026-05-03")
    print("=" * 100)
    results: list[dict[str, Any]] = []
    for i, scn in enumerate(SCENARIOS, start=1):
        r = _run_one(scn)
        results.append(r)
        _print_per_scenario(i, r)

    # ----- aggregate -----
    n = len(results)
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
    for r in results:
        counts[r["grade"]] = counts.get(r["grade"], 0) + 1

    drifts = [r["mc_drift_pct"] for r in results
              if isinstance(r["mc_drift_pct"], (int, float)) and r["mc_drift_pct"] == r["mc_drift_pct"]]
    mean_drift = (sum(drifts) / len(drifts)) if drifts else float("nan")
    max_drift = max(drifts) if drifts else float("nan")

    title_leaks = sum(1 for r in results if not r["title_includes_underlying"])
    pct_title_leak = 100.0 * title_leaks / n

    # event-keyed caveat: only over scenarios where the lookup has a key
    keyed = [r for r in results if r["caveats_event_keyed"] is not None]
    keyed_hits = sum(1 for r in keyed if r["caveats_event_keyed"])
    pct_event_caveat = (100.0 * keyed_hits / len(keyed)) if keyed else float("nan")

    barrier_runs = [r for r in results if r["barrier_honored"] is not None]
    barrier_honored = sum(1 for r in barrier_runs if r["barrier_honored"])
    pct_barrier = (100.0 * barrier_honored / len(barrier_runs)) if barrier_runs else float("nan")

    direction_runs = sum(1 for r in results if r["direction_aligned"])
    pct_direction = 100.0 * direction_runs / n

    mean_findings = sum(r["validator_findings"] for r in results) / n

    print()
    print("=" * 100)
    print("AGGREGATE METRICS")
    print("=" * 100)
    print(f"  PASS / WARN / FAIL    : {counts['PASS']} / {counts['WARN']} / {counts['FAIL']}  "
          f"(of {n})")
    print(f"  Mean MC drift         : {mean_drift:.3f}%   "
          f"max: {max_drift:.3f}%  (n={len(drifts)} valid)")
    print(f"  Title-leak runs       : {title_leaks}/{n} = {pct_title_leak:.1f}%  (target 0%)")
    print(f"  Event-keyed caveat    : {keyed_hits}/{len(keyed)} = {pct_event_caveat:.1f}%  "
          f"(target >=80%)")
    print(f"  Barrier honored       : {barrier_honored}/{len(barrier_runs)} = {pct_barrier:.1f}%  "
          f"(target 100%)")
    print(f"  Direction-aligned rec : {direction_runs}/{n} = {pct_direction:.1f}%  (target 100%)")
    print(f"  Mean validator findings/run: {mean_findings:.2f}")
    print()

    # JSON dump for the report writer.
    out_dir = os.path.join(_REPO_ROOT, "research", "stress_test_2026_05_03")
    os.makedirs(out_dir, exist_ok=True)
    out_filename = os.environ.get("STRESS_OUTPUT_FILE", "real_life_run_v2.json")
    with open(os.path.join(out_dir, out_filename), "w", encoding="utf-8") as fh:
        # Strip non-serializable expected_kinds set if leaked anywhere; results
        # already only carry primitives.
        json.dump(
            {
                "results": results,
                "aggregate": {
                    "counts": counts,
                    "mean_drift_pct": mean_drift,
                    "max_drift_pct": max_drift,
                    "title_leak_pct": pct_title_leak,
                    "event_caveat_hits": keyed_hits,
                    "event_caveat_eligible": len(keyed),
                    "event_caveat_pct": pct_event_caveat,
                    "barrier_honored": barrier_honored,
                    "barrier_eligible": len(barrier_runs),
                    "barrier_pct": pct_barrier,
                    "direction_pct": pct_direction,
                    "mean_findings_per_run": mean_findings,
                },
            },
            fh, indent=2, default=str,
        )
    print(f"Wrote {os.path.join(out_dir, out_filename)}")

    # Exit 0 always — validation runs aren't pass/fail at the script level.
    return 0


if __name__ == "__main__":
    sys.exit(main())
