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

  // Pricing Configuration
  nPaths: number;
  nSteps: number;
  varianceReduction: string;

  // Live IV surface (opt-in)
  useVolSurface: boolean;
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

  // Surface diagnostics — populated only when use_vol_surface succeeded
  sigmaUsed?: number;
  sigmaAtm?: number;
  sigmaBarrier?: number;
  surfaceQuotesInverted?: number;
  surfaceQuotesTotal?: number;
}

export type OptionType =
  | "american_put"
  | "american_call"
  | "european_put"
  | "european_call"
  | "knockout_put"
  | "knockout_call"
  | "knockin_put"
  | "knockin_call";

export const OPTION_TYPES: Record<OptionType, string> = {
  american_put: "American Put",
  american_call: "American Call",
  european_put: "European Put",
  european_call: "European Call",
  knockout_put: "Knockout Put",
  knockout_call: "Knockout Call",
  knockin_put: "Knockin Put",
  knockin_call: "Knockin Call",
};

export const VARIANCE_REDUCTION_METHODS = [
  { value: "none", label: "None" },
  { value: "antithetic", label: "Antithetic" },
  { value: "control_variate", label: "Control Variate" },
];

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
  useVolSurface: false,
};
