/**
 * API client for communicating with FastAPI backend.
 */

// Dynamically set API URL - defaults to localhost:8002, but can be overridden with VITE_API_URL env var
const API_BASE_URL = import.meta.env.VITE_API_URL || (() => {
  const protocol = window.location.protocol;
  const hostname = window.location.hostname;
  return `${protocol}//${hostname}:8003`;
})();

export interface PricingRequest {
  option_type: string;
  underlying: string;
  spot_price: number;
  strike_price: number;
  days_to_expiration: number;
  risk_free_rate: number;
  volatility: number;
  dividend_yield: number;
  n_paths: number;
  n_steps: number;
  variance_reduction: string;
  barrier_level?: number;
  use_vol_surface?: boolean;
  vol_surface_max_expiries?: number;
}

export interface PricingResult {
  price: number;
  std_error?: number;
  greeks: Record<string, number>;
  method: string;
  report_html: string;
  underlying: string;
  option_type: string;
  pricing_timestamp: string;

  // Surface diagnostics
  sigma_used?: number;
  sigma_atm?: number;
  sigma_barrier?: number;
  surface_quotes_inverted?: number;
  surface_quotes_total?: number;
}

export class APIClient {
  async price(request: PricingRequest): Promise<PricingResult> {
    const response = await fetch(`${API_BASE_URL}/api/price`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Pricing failed");
    }

    return response.json();
  }

  async healthCheck(): Promise<boolean> {
    try {
      const response = await fetch(`${API_BASE_URL}/health`);
      return response.ok;
    } catch {
      return false;
    }
  }

  async getSpotPrice(ticker: string): Promise<number> {
    const response = await fetch(
      `${API_BASE_URL}/api/market/spot-price?ticker=${ticker}`
    );

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Failed to fetch spot price");
    }

    const data = await response.json();
    return data.spot_price;
  }

  async getDividendYield(ticker: string): Promise<number> {
    const response = await fetch(
      `${API_BASE_URL}/api/market/dividend-yield?ticker=${ticker}`
    );

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Failed to fetch dividend yield");
    }

    const data = await response.json();
    return data.dividend_yield;
  }

  async getRiskFreeRate(daysToExpiration: number): Promise<number> {
    const response = await fetch(
      `${API_BASE_URL}/api/market/risk-free-rate?days_to_expiration=${daysToExpiration}`
    );

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Failed to fetch risk-free rate");
    }

    const data = await response.json();
    return data.risk_free_rate;
  }

  async getHistoricalVolatility(
    ticker: string,
    lookbackDays: number = 252
  ): Promise<number> {
    const response = await fetch(
      `${API_BASE_URL}/api/market/historical-volatility?ticker=${ticker}&lookback_days=${lookbackDays}`
    );

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Failed to fetch historical volatility");
    }

    const data = await response.json();
    return data.volatility;
  }

  async getDividendInfo(
    ticker: string
  ): Promise<{ next_dividend_date: string | null; next_dividend_amount: number }> {
    const response = await fetch(
      `${API_BASE_URL}/api/market/dividend-info?ticker=${ticker}`
    );

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Failed to fetch dividend info");
    }

    const data = await response.json();
    return {
      next_dividend_date: data.next_dividend_date,
      next_dividend_amount: data.next_dividend_amount,
    };
  }
}

export const apiClient = new APIClient();
