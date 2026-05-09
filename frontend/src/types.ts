/**
 * TypeScript type definitions for the app.
 */

export interface ConfigFormState {
  // Underlier
  underlying: string;
  spotPrice: number;

  // Option Type
  optionType: string;

  // Basic Parameters
  strikePrice: number;
  daysToExpiration: number;

  // Advanced Parameters
  riskFreeRate: number;
  volatility: number;
  dividendYield: number;
  barrierLevel?: number;

  // Asian (only used when optionType startsWith "asian_")
  averagingMethod?: "geometric" | "arithmetic";
  averagingFrequency?: "daily" | "weekly" | "monthly";

  // Lookback (only used when optionType startsWith "lookback_")
  lookbackType?: "fixed" | "floating";

  // Barrier monitoring frequency (only used when optionType startsWith
  // "knockout_" or "knockin_"). Defaults to "continuous" if omitted.
  monitoring?: "continuous" | "daily" | "weekly" | "monthly";

  // Discrete dividend schedule (only used for American options). Each entry
  // is a tuple of [iso_date_str, amount]. An empty list / undefined means
  // "use the continuous yield instead".
  dividendSchedule?: Array<[string, number]>;

  // Pricing Configuration
  nPaths: number;
  nSteps: number;
  varianceReduction: string;

  // Engine selector — defaults to "auto" which lets the backend pick.
  // "mc" forces Monte Carlo (Longstaff-Schwartz for American).
  engine?: "auto" | "analytic" | "tree" | "mc" | "fdm";

  // Live IV surface (opt-in)
  useVolSurface: boolean;

  // Deep risk — scenario grid + gamma ladder
  deepRisk?: boolean;
}

// ---- Deep risk types -------------------------------------------------------

export interface ScenarioGrid {
  /** 2-D price matrix: rows = spot_axis, cols = vol_axis */
  values: number[][];
  spot_axis: number[];
  vol_axis: number[];
}

export interface GammaLadderPoint {
  spot: number;
  delta: number;
  gamma: number;
}

export interface PricingResult {
  price: number;
  stdError?: number;
  greeks: Record<string, number>;
  method: string;
  reportHtml: string;
  underlying: string;
  optionType: string;
  pricingTimestamp: string;

  // Surface diagnostics — populated for every response. ``surfaceStatus``
  // distinguishes the five build outcomes; the UI uses it to color the
  // banner (green = ok, amber = suspect, red = failed/empty_chain) so the
  // trader cannot mistake a silent fall-back for an active surface.
  surfaceStatus?: "skipped" | "ok" | "suspect" | "failed" | "empty_chain";
  surfaceFailureReason?: string;
  sigmaUsed?: number;
  sigmaAtm?: number;
  sigmaBarrier?: number;
  surfaceQuotesInverted?: number;
  surfaceQuotesTotal?: number;

  // Pin risk — true when spot is sitting on the barrier and Greeks are
  // unreliable. The UI surfaces a warning banner when this is true.
  pinRisk?: boolean;

  // Bridge sigma rule — non-null (e.g. "max(sigma_K, sigma_B)") only when a
  // vol surface is active on a barrier product. Rendered as a small caption
  // near the σ display in the surface diagnostics block.
  bridgeSigmaRule?: string | null;

  // Deep risk — populated only when deep_risk=true on the request
  scenarioGrid?: ScenarioGrid;
  gammaLadder?: GammaLadderPoint[];
}

export type OptionType =
  | "american_put"
  | "american_call"
  | "european_put"
  | "european_call"
  | "knockout_put"
  | "knockout_call"
  | "knockin_put"
  | "knockin_call"
  | "asian_call"
  | "asian_put"
  | "lookback_call"
  | "lookback_put";

export const OPTION_TYPES: Record<OptionType, string> = {
  american_put: "American Put",
  american_call: "American Call",
  european_put: "European Put",
  european_call: "European Call",
  knockout_put: "Knockout Put",
  knockout_call: "Knockout Call",
  knockin_put: "Knockin Put",
  knockin_call: "Knockin Call",
  asian_call: "Asian Call (Average Price)",
  asian_put: "Asian Put (Average Price)",
  lookback_call: "Lookback Call",
  lookback_put: "Lookback Put",
};

// Grouped for the option-type dropdown so users see Vanilla / Barrier /
// Path-Dependent rather than a flat 12-item list.
export const OPTION_TYPE_GROUPS: { label: string; types: OptionType[] }[] = [
  {
    label: "Vanilla",
    types: ["european_call", "european_put", "american_call", "american_put"],
  },
  {
    label: "Barrier",
    types: ["knockout_call", "knockout_put", "knockin_call", "knockin_put"],
  },
  {
    label: "Path-Dependent",
    types: ["asian_call", "asian_put", "lookback_call", "lookback_put"],
  },
];

// Engine selector — friendly labels in the UI map to the backend's
// engine field. We expose only "auto" and "mc" by default; analytic/tree/fdm
// are reserved for future explicit routing.
export const ENGINE_OPTIONS: { value: NonNullable<ConfigFormState["engine"]>; label: string; hint: string }[] = [
  {
    value: "auto",
    label: "Auto (recommended)",
    hint:
      "Let the pricer pick. European → closed-form, American → binomial tree, Asian/Lookback → analytic where possible.",
  },
  {
    value: "mc",
    label: "Most accurate (Monte Carlo)",
    hint:
      "Force Longstaff-Schwartz Monte Carlo. Useful for American options when you want a path-based price with a standard error. Slower than the tree.",
  },
];

export const VARIANCE_REDUCTION_METHODS = [
  { value: "none", label: "None" },
  { value: "antithetic", label: "Antithetic" },
  { value: "control_variate", label: "Control Variate" },
];

// ---- Macro Briefing types ---------------------------------------------------

export interface BriefingMacroRow {
  label: string;
  series_id: string;
  value: string;
  delta_1d: string;
  as_of: string;
  context: string;
}

export interface BriefingIndex {
  symbol: string;
  name: string;
  level: number;
  change_pct: number;
  ytd_pct: number;
}

export interface BriefingVol {
  symbol: string;
  level: number;
  change_pct: number;
  regime: "low" | "normal" | "elevated" | "stressed";
}

export interface BriefingSectorMover {
  sector: string;
  etf: string;
  change_pct: number;
  driver: string;
}

export interface BriefingHeadline {
  title: string;
  source: string;
  url: string;
  published: string;
}

export interface BriefingSource {
  name: string;
  url: string;
  fetched_at: string;
}

// ---- Structurer desk types --------------------------------------------------

export type BriefingVolTerm = {
  tenor: string;
  symbol: string;
  level: number | null;
  change_pct: number | null;
};

export type BriefingTermSlope = {
  shape: "contango" | "backwardation" | "flat";
  ratio_30d_3m: number | null;
  context: string;
};

export type BriefingScalarMetric = {
  symbol: string;
  level: number | null;
  change_pct: number | null;
  context: string;
};

export type BriefingRvIv = {
  symbol: string;
  rv_30d: number | null;
  iv_proxy: number | null;
  spread_vol_pts: number | null;
  signal: "sell vol" | "buy vol" | "neutral";
};

export type BriefingCreditProxy = {
  symbol: string;
  name: string;
  level: number | null;
  change_pct: number | null;
};

export type BriefingStructurer = {
  vol_term_structure: BriefingVolTerm[];
  term_structure_slope: BriefingTermSlope;
  skew_index: BriefingScalarMetric;
  vvix: BriefingScalarMetric;
  move: BriefingScalarMetric;
  implied_correlation: BriefingScalarMetric;
  realized_vs_implied: BriefingRvIv[];
  credit_proxy: BriefingCreditProxy[];
};

// ---- Trader desk types ------------------------------------------------------

export type BriefingOvernightFuture = {
  symbol: string;
  name: string;
  level: number | null;
  change_pct: number | null;
  session_high: number | null;
  session_low: number | null;
};

export type BriefingGlobalIndex = {
  symbol: string;
  name: string;
  change_pct: number | null;
};

export type BriefingCrossAsset = {
  symbol: string;
  name: string;
  level: number | null;
  change_pct: number | null;
};

export type BriefingVolCarry = {
  symbol: string;
  name: string;
  rv_10d: number | null;
  rv_30d: number | null;
  iv_proxy: number | null;
  carry_signal: "sell premium" | "buy premium" | "neutral";
};

export type BriefingTrader = {
  overnight_futures: BriefingOvernightFuture[];
  global_overnight: BriefingGlobalIndex[];
  cross_asset: BriefingCrossAsset[];
  vol_carry: BriefingVolCarry[];
};

// ---- Briefing root ----------------------------------------------------------

export interface Briefing {
  as_of: string;
  title: string;
  summary: string;
  macro: BriefingMacroRow[];
  equity: {
    indices: BriefingIndex[];
    vol: BriefingVol[];
    sector_movers: BriefingSectorMover[];
  };
  headlines: BriefingHeadline[];
  themes: string[];
  sources: BriefingSource[];
  structurer?: BriefingStructurer;
  trader?: BriefingTrader;
}

export const DEFAULT_CONFIG: ConfigFormState = {
  underlying: "SPY",
  spotPrice: 0,
  optionType: "american_put",
  strikePrice: 0,
  daysToExpiration: 90,
  riskFreeRate: 0.045,
  volatility: 0.1845,
  dividendYield: 0.015,
  nPaths: 10000,
  nSteps: 90,
  varianceReduction: "antithetic",
  engine: "auto",
  useVolSurface: false,
  deepRisk: false,
};
