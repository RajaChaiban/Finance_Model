/**
 * Typed client for /api/esmm/* endpoints.
 *
 * Types mirror src/esmm/schemas.py + the BacktestResponse / BacktestRecordView
 * declared in src/api/esmm_router.py.
 */

import { getApiBaseUrl } from "./baseUrl";
import { errorFromResponse } from "./errors";

const API_BASE_URL = getApiBaseUrl();

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface MarketMakingConfig {
  symbol: string;
  base_half_spread_bps?: number;
  inventory_skew_bps_per_unit?: number;
  max_inventory?: number;
  quote_size?: number;
  fee_bps?: number;
  delta_hedge_threshold?: number;
  delta_hedge_band?: number;
  gamma_hedge_threshold?: number;
  gamma_hedge_band?: number;
}

export interface OrderBookLevel {
  price: number;
  size: number;
}

export interface OrderBookSnapshot {
  ts: number;
  symbol: string;
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
}

export interface TCABreakdown {
  spread_capture_pnl: number;
  inventory_pnl: number;
  hedge_pnl: number;
  adverse_selection_pnl: number;
  fees_pnl: number;
  total_pnl: number;
  n_fills: number;
  avg_fill_size: number;
}

export interface BacktestResponse {
  n_quotes: number;
  n_fills: number;
  final_inventory: number;
  final_mid: number;
  realised_pnl: number;
  unrealised_pnl: number;
  total_pnl: number;
  tca: TCABreakdown;
  mid_path_sample: [number, number][];
  inventory_path_sample: [number, number][];
  saved_id?: string | null;
}

export interface BacktestRecordView {
  id: string;
  created_ts: number;
  symbol: string;
  n_quotes: number;
  n_fills: number;
  total_pnl: number;
  final_inventory: number;
  config: Record<string, unknown>;
  tca: Record<string, number>;
}

export interface Quote {
  ts: number;
  symbol: string;
  bid_price: number;
  bid_size: number;
  ask_price: number;
  ask_size: number;
  fair_value: number;
  skew_bps: number;
  half_spread_bps: number;
}

export interface CRBInternalisationResult {
  symbol: string;
  incoming_buy: number;
  incoming_sell: number;
  internalised: number;
  residual_to_street: number;
  estimated_savings_bps: number;
}

export interface CRBBookFlow {
  symbol: string;
  incoming_buys: number;
  incoming_sells: number;
  street_spread_bps?: number;
}

export interface CRBBookResult {
  per_symbol: CRBInternalisationResult[];
  total_internalised_notional: number;
  total_residual_buy_notional: number;
  total_residual_sell_notional: number;
  total_estimated_savings_bps_weighted: number;
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

export interface BacktestRequest {
  config: MarketMakingConfig;
  n_snaps?: number;
  start_price?: number;
  sigma_per_step?: number;
  base_spread_bps?: number;
  seed?: number;
}

export class EsmmClient {
  async runBacktest(request: BacktestRequest): Promise<BacktestResponse> {
    const resp = await fetch(`${API_BASE_URL}/api/esmm/backtest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
    if (!resp.ok) throw await errorFromResponse(resp, "Backtest failed");
    return resp.json();
  }

  async getQuote(snapshot: OrderBookSnapshot, config: MarketMakingConfig,
                 seedPositionQty?: number, adverseSelectionBps?: number): Promise<Quote> {
    const body = {
      snapshot,
      config,
      seed_position: seedPositionQty
        ? { symbol: config.symbol, quantity: seedPositionQty, avg_cost: snapshot.bids[0]?.price ?? 0 }
        : undefined,
      adverse_selection_bps: adverseSelectionBps ?? 0,
    };
    const resp = await fetch(`${API_BASE_URL}/api/esmm/quote`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw await errorFromResponse(resp, "Quote failed");
    return resp.json();
  }

  async getSyntheticBook(nSnaps: number, symbol: string = "SPY", seed: number = 42): Promise<OrderBookSnapshot[]> {
    const resp = await fetch(`${API_BASE_URL}/api/esmm/synthetic-book`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ n_snaps: nSnaps, symbol, seed }),
    });
    if (!resp.ok) throw await errorFromResponse(resp, "Synthetic book fetch failed");
    return resp.json();
  }

  async runCRBBook(snapshots: OrderBookSnapshot[], flows: CRBBookFlow[],
                   capPct: number = 1.0): Promise<CRBBookResult> {
    const resp = await fetch(`${API_BASE_URL}/api/esmm/crb/internalise-book`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ snapshots, flows, internalisation_cap_pct: capPct }),
    });
    if (!resp.ok) throw await errorFromResponse(resp, "CRB book run failed");
    return resp.json();
  }

  async listBacktests(limit: number = 20): Promise<BacktestRecordView[]> {
    const resp = await fetch(`${API_BASE_URL}/api/esmm/backtests?limit=${limit}`);
    if (!resp.ok) throw await errorFromResponse(resp, "List backtests failed");
    return resp.json();
  }
}

export const esmmClient = new EsmmClient();
