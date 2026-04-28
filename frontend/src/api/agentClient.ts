/**
 * Typed client for /api/agent/* — the multi-agent structuring co-pilot.
 *
 * The session is the source of truth on the backend; this client just polls
 * and pushes gate decisions. Phase 2 swaps polling for the SSE stream
 * exposed at /api/agent/sessions/{id}/events.
 */

const API_BASE_URL =
  import.meta.env.VITE_API_URL ||
  (() => {
    const protocol = window.location.protocol;
    const hostname = window.location.hostname;
    return `${protocol}//${hostname}:8003`;
  })();

export interface IntakeForm {
  underlying: string;
  notional_usd: number;
  view: string;
  horizon_days: number;
  budget_bps_notional: number;
  premium_tolerance?: string;
  capped_upside_ok?: boolean;
  barrier_appetite?: boolean;
  hedge_target_loss_pct?: number | null;
  shares?: number | null;
  avg_cost?: number | null;
  constraints?: string[];
}

export interface ClientObjective {
  underlying: string;
  notional_usd: number;
  view: string;
  horizon_days: number;
  budget_bps_notional: number;
  premium_tolerance: string;
  capped_upside_ok: boolean;
  barrier_appetite: boolean;
  hedge_target_loss_pct: number | null;
  raw_rfq?: string | null;
  clarifications?: string[];
  shares?: number | null;
  avg_cost?: number | null;
  constraints?: string[];
}

export interface MarketRegime {
  underlying: string;
  spot: number;
  dividend_yield: number;
  risk_free_rate: number;
  realised_vol_30d?: number | null;
  realised_vol_90d?: number | null;
  vol_regime: string;
  earnings_proximity: string;
  data_source_warnings?: string[];
}

export interface Leg {
  option_type: string;
  strike: number;
  expiry_days: number;
  quantity: number;
  barrier_level?: number | null;
  barrier_monitoring?: string;
  role?: string | null;
}

export interface Candidate {
  candidate_id: string;
  kind: string;
  name: string;
  legs: Leg[];
  rationale: string;
  hedging_cost_premium_bps: number;
  notional_usd: number;
}

export interface GreeksSnapshot {
  delta: number;
  gamma: number;
  vega: number;
  theta: number;
  rho: number;
  dv01: number;
}

export interface PricedCandidate {
  candidate: Candidate;
  net_premium: number;
  net_premium_bps: number;
  greeks: GreeksSnapshot;
  per_leg_prices: number[];
  method_label: string;
  max_loss_usd?: number | null;
  max_gain_usd?: number | null;
  breakeven?: number[] | null;
  feasible: boolean;
  feasibility_notes: string[];
}

export interface ScenarioRow {
  name: string;
  description: string;
  spot_shock_pct: number;
  vol_shock_pct: number;
  rate_shock_abs: number;
  pnl_usd: number;
  pnl_pct_notional: number;
}

export interface ScenarioReport {
  candidate_id: string;
  scenarios: ScenarioRow[];
  hedgeability_ok: boolean;
  hedgeability_reason: string;
  capacity_warning?: string | null;
}

export interface ValidatorFinding {
  name: string;
  severity: "info" | "warn" | "block";
  message: string;
  candidate_id?: string | null;
  remediation?: string | null;
}

export interface ValidatorReport {
  findings: ValidatorFinding[];
}

export interface TermSheet {
  candidate_id: string;
  text: string;
}

export interface MemoArtifact {
  title: string;
  objective_restatement: string;
  comparison_table_md: string;
  per_candidate_sections_md: string[];
  recommendation_md: string;
  recommended_candidate_id: string;
  term_sheets: TermSheet[];
  caveats: string[];
}

export interface SessionView {
  session_id: string;
  status: string;
  last_error?: string | null;
  objective?: ClientObjective | null;
  regime?: MarketRegime | null;
  candidates: Candidate[];
  priced: PricedCandidate[];
  scenarios: ScenarioReport[];
  validator?: ValidatorReport | null;
  memo?: MemoArtifact | null;
  gate_a_decision?: boolean | null;
  gate_b_decision?: boolean | null;
  gate_c_decision?: boolean | null;
  total_cost_usd: number;
  total_tokens_input: number;
  total_tokens_output: number;
}

export type Gate = "a" | "b" | "c";

export class AgentClient {
  async startSession(
    body: { intake_form?: IntakeForm; intake_nl?: string }
  ): Promise<{ session_id: string; status: string; message: string }> {
    const r = await fetch(`${API_BASE_URL}/api/agent/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || `Start failed (${r.status})`);
    }
    return r.json();
  }

  async getSession(id: string): Promise<SessionView> {
    const r = await fetch(`${API_BASE_URL}/api/agent/sessions/${id}`);
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || `Get failed (${r.status})`);
    }
    return r.json();
  }

  async decideGate(
    id: string,
    gate: Gate,
    body: { approved: boolean; payload?: Record<string, unknown> }
  ): Promise<SessionView> {
    const r = await fetch(`${API_BASE_URL}/api/agent/sessions/${id}/gate/${gate}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || `Gate ${gate} failed (${r.status})`);
    }
    return r.json();
  }
}

export const agentClient = new AgentClient();
