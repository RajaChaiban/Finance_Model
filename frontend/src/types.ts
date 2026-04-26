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
}

export type OptionType =
  | "american_put"
  | "american_call"
  | "european_put"
  | "european_call"
  | "knockout_put"
  | "knockout_call";

export const OPTION_TYPES: Record<OptionType, string> = {
  american_put: "American Put",
  american_call: "American Call",
  european_put: "European Put",
  european_call: "European Call",
  knockout_put: "Knockout Put",
  knockout_call: "Knockout Call",
};

export const VARIANCE_REDUCTION_METHODS = [
  { value: "none", label: "None" },
  { value: "antithetic", label: "Antithetic" },
  { value: "control_variate", label: "Control Variate" },
];
