"""Typed state for the structuring co-pilot.

Every agent reads/writes slices of `StructuringSession`. The orchestrator owns
the session and is the only thing that mutates it across agents. Pydantic
gives us serialization for free (replay, audit log, SSE event payloads).

Sign conventions match the rest of the codebase:
    * Long position quantity > 0; short < 0.
    * Greeks per the existing engines (delta per $1 spot, vega per 1% σ,
      theta per calendar day, rho per 1% rate).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Gate(str, Enum):
    """The three human-in-the-loop checkpoints."""

    A = "gate_a_objective"        # after IntakeAgent
    B = "gate_b_candidates"       # after StrategistAgent
    C = "gate_c_memo"             # after Narrator


class SessionStatus(str, Enum):
    PENDING_INTAKE = "pending_intake"
    AWAITING_GATE_A = "awaiting_gate_a"
    PENDING_STRATEGIST = "pending_strategist"
    AWAITING_GATE_B = "awaiting_gate_b"
    PENDING_PRICING = "pending_pricing"
    PENDING_SCENARIO = "pending_scenario"
    PENDING_VALIDATION = "pending_validation"
    PENDING_NARRATOR = "pending_narrator"
    AWAITING_GATE_C = "awaiting_gate_c"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


class Severity(str, Enum):
    INFO = "info"
    WARN = "warn"
    BLOCK = "block"


class StructureKind(str, Enum):
    """The lego-block universe v1. All compose from existing engine primitives."""

    LONG_PUT = "long_put"
    LONG_CALL = "long_call"
    PUT_SPREAD = "put_spread"
    CALL_SPREAD = "call_spread"
    COLLAR = "collar"
    ZERO_COST_COLLAR = "zero_cost_collar"
    KO_PUT = "ko_put"
    KI_PUT = "ki_put"
    KO_CALL = "ko_call"
    KI_CALL = "ki_call"
    COVERED_CALL = "covered_call"
    RISK_REVERSAL = "risk_reversal"
    PUT_SPREAD_COLLAR = "put_spread_collar"


# ---------------------------------------------------------------------------
# Client intake
# ---------------------------------------------------------------------------


class ClientObjective(BaseModel):
    """The junior structurer's understanding of what the client wants.

    Populated by IntakeAgent (NL or form) and confirmed at Gate A. The
    strategist consumes this; if a load-bearing field is missing, IntakeAgent
    should ask one clarifying question rather than guess.
    """

    model_config = ConfigDict(extra="forbid")

    underlying: str = Field(..., description="Single-name ticker, e.g. 'AAPL', 'SPY'.")
    notional_usd: float = Field(..., gt=0, description="Total client position value in USD.")
    shares: Optional[float] = Field(None, description="If known; else inferred from notional/spot.")
    avg_cost: Optional[float] = Field(None, description="Client's cost basis if relevant.")

    view: str = Field(
        ...,
        description=(
            "One of: bearish, mildly_bearish, neutral, mildly_bullish, bullish, "
            "protect_gains, crash_hedge, earnings_hedge."
        ),
    )
    horizon_days: int = Field(..., gt=0, le=1825, description="Holding horizon in calendar days.")
    budget_bps_notional: float = Field(
        ...,
        ge=0,
        le=2000,
        description="Premium budget in bps of notional (0 = zero-cost only).",
    )
    premium_tolerance: str = Field(
        "low",
        description="One of: very_low, low, medium, high, zero_cost_only.",
    )
    capped_upside_ok: bool = Field(
        False,
        description="Will the client accept a cap on upside (collar / covered call)?",
    )
    barrier_appetite: bool = Field(
        False,
        description="Will the client accept barrier risk (KO/KI structures)?",
    )
    hedge_target_loss_pct: Optional[float] = Field(
        None,
        ge=0,
        le=1,
        description="Max acceptable downside as a fraction (0.10 = -10%).",
    )
    constraints: list[str] = Field(
        default_factory=list,
        description="Free-form constraints, e.g. 'no leverage', 'expiry before earnings'.",
    )

    raw_rfq: Optional[str] = Field(None, description="Original NL blob for audit.")
    clarifications: list[str] = Field(
        default_factory=list,
        description="Clarifying questions IntakeAgent asked + their answers.",
    )


# ---------------------------------------------------------------------------
# Market regime (Phase 1: minimal; Phase 2 fills the rest)
# ---------------------------------------------------------------------------


class MarketRegime(BaseModel):
    """Snapshot of market conditions as of session start.

    Phase 1: spot, dividend, rate, hist vol — what the existing market_data
    layer already gives us. Phase 2 adds atm IV, term slope, VIX, days to
    earnings, borrow proxy.
    """

    model_config = ConfigDict(extra="forbid")

    underlying: str
    spot: float
    dividend_yield: float = 0.0
    risk_free_rate: float = 0.045
    realised_vol_30d: Optional[float] = None
    realised_vol_90d: Optional[float] = None

    # Phase 2 fields (optional in Phase 1).
    atm_iv: Optional[float] = None
    iv_term_slope: Optional[float] = None
    vix: Optional[float] = None
    days_to_earnings: Optional[int] = None
    implied_borrow_rate: Optional[float] = None

    # Derived tags for the strategist's prompt.
    vol_regime: str = "normal"  # one of: low, normal, high, very_high
    earnings_proximity: str = "none"  # one of: none, near (<14d), imminent (<7d)

    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    data_source_warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Structures: legs → candidates → priced candidates
# ---------------------------------------------------------------------------


class Leg(BaseModel):
    """A single option leg in a multi-leg structure.

    `option_type` matches the router's keys exactly:
    {european_,american_,knockout_,knockin_}{call,put}.
    `quantity` > 0 = long, < 0 = short. Notional is per-leg; structure-level
    notional consistency is a Validator invariant.
    """

    model_config = ConfigDict(extra="forbid")

    option_type: str
    strike: float = Field(..., gt=0)
    expiry_days: int = Field(..., gt=0)
    quantity: float = Field(..., description=">0 long, <0 short. Number of contracts (or notional).")

    # Barrier-only fields.
    barrier_level: Optional[float] = None
    barrier_monitoring: str = "continuous"  # 'continuous' | 'daily' | 'weekly' | 'monthly'

    # Optional metadata.
    role: Optional[str] = Field(None, description="e.g. 'long_put_protective', 'short_call_yield'.")


class Candidate(BaseModel):
    """A proposed structure (1+ legs) with the strategist's rationale."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    kind: StructureKind
    name: str = Field(..., description="Human-readable name, e.g. 'Zero-cost SPY collar'.")
    legs: list[Leg] = Field(..., min_length=1)

    # Strategist's brief — *this is the IP*. Why this structure, why these strikes,
    # what it protects, what it gives up. 2–4 sentences.
    rationale: str

    # Soft hedging-cost premium added by the strategist (bps). Reflects illiquid
    # strikes / hard borrow / wide bid-ask. Senior structurers bake this in;
    # we are not generating a hedge plan for the desk.
    hedging_cost_premium_bps: float = 0.0

    notional_usd: float = Field(..., gt=0)


class GreeksSnapshot(BaseModel):
    """Aggregated Greeks across all legs of a structure."""

    delta: float = 0.0  # per $1 spot
    gamma: float = 0.0  # per $1 spot
    vega: float = 0.0   # per 1% σ
    theta: float = 0.0  # per calendar day
    rho: float = 0.0    # per 1% rate
    dv01: float = 0.0   # per 1bp rate (rho / 100)


class PricedCandidate(BaseModel):
    """Candidate after PricingAgent has filled in the numbers."""

    model_config = ConfigDict(extra="forbid")

    candidate: Candidate
    net_premium: float = Field(..., description="Net debit/credit in USD across all legs.")
    net_premium_bps: float = Field(..., description="Premium as bps of notional. Positive = debit.")
    greeks: GreeksSnapshot
    per_leg_prices: list[float] = Field(default_factory=list)
    method_label: str = ""

    # Structure-level summary stats.
    max_loss_usd: Optional[float] = None
    max_gain_usd: Optional[float] = None
    breakeven: Optional[list[float]] = None

    feasible: bool = True
    feasibility_notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Scenario + Validator outputs
# ---------------------------------------------------------------------------


class ScenarioRow(BaseModel):
    name: str
    description: str
    spot_shock_pct: float
    vol_shock_pct: float
    rate_shock_abs: float
    pnl_usd: float
    pnl_pct_notional: float


class ScenarioReport(BaseModel):
    """Per-candidate client-outcome scenario report. NOT a hedge plan."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    scenarios: list[ScenarioRow] = Field(default_factory=list)
    hedgeability_ok: bool = True
    hedgeability_reason: str = ""
    capacity_warning: Optional[str] = None
    backtest_summary: Optional[dict[str, Any]] = None  # Phase 3+


class ValidatorFinding(BaseModel):
    name: str
    severity: Severity
    message: str
    candidate_id: Optional[str] = None
    remediation: Optional[str] = None


class ValidatorReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[ValidatorFinding] = Field(default_factory=list)

    @property
    def has_blockers(self) -> bool:
        return any(f.severity == Severity.BLOCK for f in self.findings)

    @property
    def has_warnings(self) -> bool:
        return any(f.severity == Severity.WARN for f in self.findings)


# ---------------------------------------------------------------------------
# Memo
# ---------------------------------------------------------------------------


class TermSheetSnippet(BaseModel):
    candidate_id: str
    text: str  # plain monospace block content


class MemoArtifact(BaseModel):
    """The final 3-way comparison memo from NarratorAgent."""

    model_config = ConfigDict(extra="forbid")

    title: str
    objective_restatement: str
    comparison_table_md: str
    per_candidate_sections_md: list[str] = Field(default_factory=list)
    recommendation_md: str
    recommended_candidate_id: str
    term_sheets: list[TermSheetSnippet] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    rendered_html: Optional[str] = None  # Phase 4


# ---------------------------------------------------------------------------
# Audit + telemetry
# ---------------------------------------------------------------------------


class AuditEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent: str
    event: str  # 'enter' | 'exit' | 'gate_open' | 'gate_decision' | 'error' | 'warning'
    message: str = ""
    duration_s: Optional[float] = None
    tokens_input: Optional[int] = None
    tokens_output: Optional[int] = None
    tokens_cache_read: Optional[int] = None
    tokens_cache_create: Optional[int] = None
    cost_usd: Optional[float] = None
    payload: Optional[dict[str, Any]] = None


# ---------------------------------------------------------------------------
# The session
# ---------------------------------------------------------------------------


class StructuringSession(BaseModel):
    """Single source of truth carried through the agent pipeline."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    status: SessionStatus = SessionStatus.PENDING_INTAKE

    # User input (form path) and / or NL blob (demo path).
    intake_form: Optional[dict[str, Any]] = None
    intake_nl: Optional[str] = None

    # Agent outputs. Populated by the orchestrator step by step.
    objective: Optional[ClientObjective] = None
    regime: Optional[MarketRegime] = None
    candidates: list[Candidate] = Field(default_factory=list)
    priced: list[PricedCandidate] = Field(default_factory=list)
    scenarios: list[ScenarioReport] = Field(default_factory=list)
    validator: Optional[ValidatorReport] = None
    memo: Optional[MemoArtifact] = None

    # Gate decisions (None means not yet decided; True = approved, False = rejected).
    gate_a_decision: Optional[bool] = None
    gate_b_decision: Optional[bool] = None
    gate_c_decision: Optional[bool] = None
    gate_a_edits: Optional[ClientObjective] = None  # if junior edited at the gate
    gate_b_swap: Optional[dict[str, str]] = None    # candidate_id -> replacement intent
    gate_c_edits: Optional[str] = None              # free-text memo edits

    # Validator retry counter (orchestrator owns).
    validator_retries: int = 0

    # Audit + cost tracking.
    audit: list[AuditEntry] = Field(default_factory=list)
    total_cost_usd: float = 0.0
    total_tokens_input: int = 0
    total_tokens_output: int = 0

    # Last error for surfaced UI display.
    last_error: Optional[str] = None

    def append_audit(self, entry: AuditEntry) -> None:
        self.audit.append(entry)
        self.updated_at = datetime.now(timezone.utc)
        if entry.cost_usd:
            self.total_cost_usd += entry.cost_usd
        if entry.tokens_input:
            self.total_tokens_input += entry.tokens_input
        if entry.tokens_output:
            self.total_tokens_output += entry.tokens_output
