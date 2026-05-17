/**
 * eSMM Lab — Listed-options Market-Making Research panel.
 *
 * Single-page research console for the new market-making layer:
 *  • Configure a backtest (spread, skew, inventory cap, hedge bands)
 *  • Run it on a synthetic order book
 *  • See P&L cards, TCA breakdown, mid-and-inventory chart
 *  • Run a multi-symbol Central Risk Book simulation alongside
 */

import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  BacktestResponse,
  CRBBookFlow,
  CRBBookResult,
  esmmClient,
  MarketMakingConfig,
  OrderBookSnapshot,
} from "../api/esmmClient";

// ---------------------------------------------------------------------------
// Defaults
// ---------------------------------------------------------------------------

const DEFAULT_CONFIG: Required<MarketMakingConfig> = {
  symbol: "SPY",
  base_half_spread_bps: 8,
  inventory_skew_bps_per_unit: 0.05,
  max_inventory: 500,
  quote_size: 50,
  fee_bps: -0.2,
  delta_hedge_threshold: 200,
  delta_hedge_band: 50,
  gamma_hedge_threshold: 0,
  gamma_hedge_band: 0,
};

const DEFAULT_RUN = {
  n_snaps: 300,
  start_price: 500,
  sigma_per_step: 0.0005,
  base_spread_bps: 5,
  seed: 42,
};

const DEFAULT_FLOWS: CRBBookFlow[] = [
  { symbol: "SPY", incoming_buys: 5000, incoming_sells: 4000 },
  { symbol: "QQQ", incoming_buys: 1500, incoming_sells: 3200 },
  { symbol: "IWM", incoming_buys: 800, incoming_sells: 800 },
];

// Color palette for TCA buckets — matches design tokens.
const TCA_COLORS = {
  spread: "#10B981",      // green — captured edge
  inventory: "#0EA5E9",   // sky — exposure P&L
  hedge: "#F59E0B",       // amber — hedging cost
  adverse: "#F43F5E",     // rose — adverse selection
  fees: "#6366F1",        // indigo — exchange fees / rebates
} as const;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtUSD(n: number): string {
  const sign = n >= 0 ? "" : "-";
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(2)}k`;
  return `${sign}$${abs.toFixed(2)}`;
}

function fmtNum(n: number, dp: number = 2): string {
  return n.toFixed(dp);
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ConfigSliderRow(props: {
  label: string;
  hint?: string;
  value: number;
  onChange: (n: number) => void;
  min: number;
  max: number;
  step: number;
  fmt?: (n: number) => string;
}) {
  const { label, hint, value, onChange, min, max, step, fmt } = props;
  return (
    <div className="esmm-cfg-row">
      <div className="esmm-cfg-label">
        <span>{label}</span>
        <span className="esmm-cfg-value">{fmt ? fmt(value) : value}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="esmm-slider"
      />
      {hint && <div className="esmm-cfg-hint">{hint}</div>}
    </div>
  );
}

function HeadlineCard(props: {
  label: string;
  value: string;
  positive?: boolean | null;
  delta?: string;
}) {
  const cls =
    props.positive === true
      ? "esmm-headline-card esmm-headline-positive"
      : props.positive === false
        ? "esmm-headline-card esmm-headline-negative"
        : "esmm-headline-card";
  const indicator =
    props.positive === true ? "▲" : props.positive === false ? "▼" : "·";
  return (
    <div className={cls}>
      <div className="esmm-headline-stripe" aria-hidden />
      <div className="esmm-headline-row-top">
        <div className="esmm-headline-label">{props.label}</div>
        <span className="esmm-headline-indicator">{indicator}</span>
      </div>
      <div className="esmm-headline-value">{props.value}</div>
      {props.delta && <div className="esmm-headline-delta">{props.delta}</div>}
    </div>
  );
}

function TCAChart({ tca }: { tca: BacktestResponse["tca"] }) {
  const data = [
    { name: "Spread", value: tca.spread_capture_pnl, fill: TCA_COLORS.spread },
    { name: "Inventory", value: tca.inventory_pnl, fill: TCA_COLORS.inventory },
    { name: "Hedge", value: tca.hedge_pnl, fill: TCA_COLORS.hedge },
    { name: "Adv. Sel.", value: tca.adverse_selection_pnl, fill: TCA_COLORS.adverse },
    { name: "Fees", value: tca.fees_pnl, fill: TCA_COLORS.fees },
  ];
  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={data} margin={{ top: 10, right: 18, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="#E2E8F0" strokeDasharray="3 3" />
        <XAxis dataKey="name" tick={{ fill: "#475569", fontSize: 12 }} />
        <YAxis
          tick={{ fill: "#475569", fontSize: 12 }}
          tickFormatter={(v) => fmtUSD(v)}
        />
        <Tooltip
          formatter={(v: number) => fmtUSD(v)}
          contentStyle={{ borderRadius: 8, borderColor: "#CBD5E1" }}
        />
        <ReferenceLine y={0} stroke="#94A3B8" />
        <Bar dataKey="value">
          {data.map((d, i) => <Cell key={i} fill={d.fill} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

function MidAndInventoryChart(props: {
  mid: [number, number][];
  inv: [number, number][];
}) {
  const data = useMemo(
    () => props.mid.map((m, i) => ({
      ts: m[0],
      mid: m[1],
      inv: props.inv[i]?.[1] ?? 0,
    })),
    [props.mid, props.inv]
  );
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={data} margin={{ top: 10, right: 18, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="#E2E8F0" strokeDasharray="3 3" />
        <XAxis dataKey="ts" tick={{ fill: "#475569", fontSize: 11 }} tickFormatter={(v) => `${v.toFixed(0)}s`} />
        <YAxis
          yAxisId="left"
          tick={{ fill: "#475569", fontSize: 11 }}
          tickFormatter={(v) => v.toFixed(2)}
          label={{ value: "Mid", angle: -90, position: "insideLeft", fill: "#475569", fontSize: 11 }}
        />
        <YAxis
          yAxisId="right"
          orientation="right"
          tick={{ fill: "#475569", fontSize: 11 }}
          label={{ value: "Inventory", angle: 90, position: "insideRight", fill: "#475569", fontSize: 11 }}
        />
        <Tooltip
          contentStyle={{ borderRadius: 8, borderColor: "#CBD5E1" }}
          formatter={(v: number, k: string) => k === "mid" ? v.toFixed(4) : v.toFixed(2)}
        />
        <Legend />
        <Line yAxisId="left" type="monotone" dataKey="mid" stroke="#0EA5E9" dot={false} strokeWidth={2} />
        <Line yAxisId="right" type="monotone" dataKey="inv" stroke="#F59E0B" dot={false} strokeWidth={2} />
      </LineChart>
    </ResponsiveContainer>
  );
}

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

export function EsmmLabPanel() {
  const [config, setConfig] = useState<Required<MarketMakingConfig>>(DEFAULT_CONFIG);
  const [run, setRun] = useState(DEFAULT_RUN);
  const [result, setResult] = useState<BacktestResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // CRB simulator state
  const [flows, setFlows] = useState<CRBBookFlow[]>(DEFAULT_FLOWS);
  const [capPct, setCapPct] = useState(1.0);
  const [crbResult, setCrbResult] = useState<CRBBookResult | null>(null);
  const [crbBusy, setCrbBusy] = useState(false);
  const [crbError, setCrbError] = useState<string | null>(null);

  const handleRun = async () => {
    setBusy(true);
    setError(null);
    try {
      const resp = await esmmClient.runBacktest({ config, ...run });
      setResult(resp);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleCRB = async () => {
    setCrbBusy(true);
    setCrbError(null);
    try {
      const symbols = Array.from(new Set(flows.map((f) => f.symbol)));
      const snaps: OrderBookSnapshot[] = [];
      for (const sym of symbols) {
        const path = await esmmClient.getSyntheticBook(20, sym, sym.charCodeAt(0));
        snaps.push(path[path.length - 1]);
      }
      const resp = await esmmClient.runCRBBook(snaps, flows, capPct);
      setCrbResult(resp);
    } catch (e) {
      setCrbError(e instanceof Error ? e.message : String(e));
    } finally {
      setCrbBusy(false);
    }
  };

  // Auto-run a backtest on first mount so the UI shows live data immediately.
  useEffect(() => {
    handleRun();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="esmm-panel">
      <div className="esmm-panel-header">
        <div className="esmm-panel-header-left">
          <div className="esmm-eyebrow">
            <span className="esmm-eyebrow-dot" />
            Research console · {result?.n_quotes ?? "—"} quotes
          </div>
          <h2 className="esmm-title">eSMM Lab</h2>
          <p className="esmm-subtitle">
            Listed-options market-making research with central-risk-book simulation.
          </p>
        </div>
        <div className="esmm-pill-row">
          <span className="esmm-pill esmm-pill-paper">PAPER</span>
          <span className="esmm-pill esmm-pill-info">SYNTHETIC L2</span>
          <span className="esmm-pill esmm-pill-mono">SEED · {run.seed}</span>
          <span className="esmm-pill esmm-pill-mono">{config.symbol}</span>
        </div>
      </div>

      <div className="esmm-grid">
        {/* Left rail — backtest config */}
        <div className="esmm-card">
          <h3 className="esmm-card-title">Backtest Configuration</h3>

          <div className="esmm-cfg-symbol">
            <label>Symbol</label>
            <input
              type="text"
              value={config.symbol}
              onChange={(e) => setConfig({ ...config, symbol: e.target.value.toUpperCase() })}
              className="esmm-input"
            />
          </div>

          <ConfigSliderRow
            label="Half-spread (bps)"
            hint="Width either side of fair value before inventory skew"
            value={config.base_half_spread_bps}
            onChange={(v) => setConfig({ ...config, base_half_spread_bps: v })}
            min={1} max={50} step={0.5}
            fmt={(v) => `${v} bps`}
          />
          <ConfigSliderRow
            label="Inventory skew (bps/unit)"
            hint="How aggressively quotes lean to lighten position"
            value={config.inventory_skew_bps_per_unit}
            onChange={(v) => setConfig({ ...config, inventory_skew_bps_per_unit: v })}
            min={0} max={2} step={0.01}
            fmt={(v) => v.toFixed(2)}
          />
          <ConfigSliderRow
            label="Max inventory"
            hint="Quote pulled past this absolute position"
            value={config.max_inventory}
            onChange={(v) => setConfig({ ...config, max_inventory: v })}
            min={50} max={5000} step={50}
            fmt={(v) => v.toFixed(0)}
          />
          <ConfigSliderRow
            label="Quote size"
            value={config.quote_size}
            onChange={(v) => setConfig({ ...config, quote_size: v })}
            min={10} max={500} step={10}
            fmt={(v) => v.toFixed(0)}
          />
          <ConfigSliderRow
            label="Hedge threshold"
            hint="|net delta| triggers a hedge above this"
            value={config.delta_hedge_threshold}
            onChange={(v) => setConfig({ ...config, delta_hedge_threshold: v })}
            min={20} max={1000} step={10}
            fmt={(v) => v.toFixed(0)}
          />
          <ConfigSliderRow
            label="Hedge band"
            hint="Hedge brings |net delta| back to this"
            value={config.delta_hedge_band}
            onChange={(v) => setConfig({ ...config, delta_hedge_band: v })}
            min={0} max={500} step={5}
            fmt={(v) => v.toFixed(0)}
          />
          <ConfigSliderRow
            label="Snapshots"
            hint="Length of the synthetic order-book path"
            value={run.n_snaps}
            onChange={(v) => setRun({ ...run, n_snaps: Math.round(v) })}
            min={50} max={2000} step={50}
            fmt={(v) => v.toFixed(0)}
          />
          <ConfigSliderRow
            label="Vol per step"
            hint="Synthetic GBM step volatility (0.0005 ≈ 8 bps)"
            value={run.sigma_per_step}
            onChange={(v) => setRun({ ...run, sigma_per_step: v })}
            min={0.0001} max={0.005} step={0.0001}
            fmt={(v) => `${(v * 1e4).toFixed(1)} bps`}
          />
          <ConfigSliderRow
            label="Seed"
            value={run.seed}
            onChange={(v) => setRun({ ...run, seed: Math.round(v) })}
            min={0} max={9999} step={1}
            fmt={(v) => v.toFixed(0)}
          />

          <button onClick={handleRun} disabled={busy} className="esmm-btn-primary">
            {busy ? "Running…" : "▶ Run Backtest"}
          </button>
          {error && <div className="esmm-error">{error}</div>}
        </div>

        {/* Right rail — results */}
        <div className="esmm-card-stack">
          {result ? (
            <>
              <div className="esmm-headline-row">
                <HeadlineCard
                  label="Total P&L"
                  value={fmtUSD(result.total_pnl)}
                  positive={result.total_pnl > 0 ? true : result.total_pnl < 0 ? false : null}
                />
                <HeadlineCard
                  label="Realised"
                  value={fmtUSD(result.realised_pnl)}
                  positive={result.realised_pnl > 0 ? true : result.realised_pnl < 0 ? false : null}
                />
                <HeadlineCard
                  label="Unrealised"
                  value={fmtUSD(result.unrealised_pnl)}
                  positive={result.unrealised_pnl > 0 ? true : result.unrealised_pnl < 0 ? false : null}
                />
                <HeadlineCard label="# Quotes" value={result.n_quotes.toString()} />
                <HeadlineCard label="# Fills" value={result.n_fills.toString()} />
                <HeadlineCard label="Final Inv." value={fmtNum(result.final_inventory)} />
              </div>

              <div className="esmm-card">
                <h3 className="esmm-card-title">P&L Attribution (TCA)</h3>
                <p className="esmm-card-sub">
                  Decomposes total P&L into the canonical market-making buckets.
                  Spread capture = quoted edge per fill. Inventory = MTM on residual
                  position. Hedge = cost of paying away half-spread on hedge legs.
                  Adverse selection = post-fill markout to fair value.
                </p>
                <TCAChart tca={result.tca} />
              </div>

              <div className="esmm-card">
                <h3 className="esmm-card-title">Mid Path × Inventory Trajectory</h3>
                <p className="esmm-card-sub">
                  Synthetic mid (left axis) and live inventory (right axis). Watch
                  inventory mean-revert when the hedger fires.
                </p>
                <MidAndInventoryChart
                  mid={result.mid_path_sample}
                  inv={result.inventory_path_sample}
                />
              </div>
            </>
          ) : (
            <div className="esmm-card esmm-empty">
              <p>Configure and run a backtest to see results here.</p>
            </div>
          )}
        </div>
      </div>

      {/* CRB simulator section */}
      <div className="esmm-card esmm-crb-card">
        <h3 className="esmm-card-title">Central Risk Book — Multi-symbol Internalisation</h3>
        <p className="esmm-card-sub">
          Imagine all of the firm's incoming flow lands in a single book. The CRB
          matches overlap internally and only sends the residual to the street,
          saving the bid-offer spread on every internalised share.
        </p>

        <table className="esmm-crb-table">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Incoming Buys</th>
              <th>Incoming Sells</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {flows.map((f, i) => (
              <tr key={i}>
                <td>
                  <input
                    type="text"
                    value={f.symbol}
                    onChange={(e) => {
                      const next = [...flows];
                      next[i] = { ...next[i], symbol: e.target.value.toUpperCase() };
                      setFlows(next);
                    }}
                    className="esmm-input-sm"
                  />
                </td>
                <td>
                  <input
                    type="number"
                    value={f.incoming_buys}
                    onChange={(e) => {
                      const next = [...flows];
                      next[i] = { ...next[i], incoming_buys: parseFloat(e.target.value) || 0 };
                      setFlows(next);
                    }}
                    className="esmm-input-sm"
                  />
                </td>
                <td>
                  <input
                    type="number"
                    value={f.incoming_sells}
                    onChange={(e) => {
                      const next = [...flows];
                      next[i] = { ...next[i], incoming_sells: parseFloat(e.target.value) || 0 };
                      setFlows(next);
                    }}
                    className="esmm-input-sm"
                  />
                </td>
                <td>
                  <button
                    className="esmm-btn-ghost"
                    onClick={() => setFlows(flows.filter((_, j) => j !== i))}
                  >
                    ✕
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <button
          className="esmm-btn-ghost"
          onClick={() => setFlows([...flows, { symbol: "NEW", incoming_buys: 0, incoming_sells: 0 }])}
        >
          + Add row
        </button>

        <div className="esmm-crb-controls">
          <ConfigSliderRow
            label="Internalisation cap"
            hint="Fraction of overlap the CRB is allowed to match"
            value={capPct}
            onChange={setCapPct}
            min={0} max={1} step={0.05}
            fmt={(v) => `${(v * 100).toFixed(0)}%`}
          />
          <button onClick={handleCRB} disabled={crbBusy} className="esmm-btn-primary">
            {crbBusy ? "Computing…" : "▶ Run CRB"}
          </button>
        </div>
        {crbError && <div className="esmm-error">{crbError}</div>}

        {crbResult && (
          <div className="esmm-headline-row esmm-crb-results">
            <HeadlineCard
              label="Internalised Notional"
              value={fmtUSD(crbResult.total_internalised_notional)}
              positive={true}
            />
            <HeadlineCard
              label="Saved (weighted bps)"
              value={`${crbResult.total_estimated_savings_bps_weighted.toFixed(2)} bps`}
              positive={true}
            />
            <HeadlineCard
              label="Residual to Street (BUY)"
              value={fmtUSD(crbResult.total_residual_buy_notional)}
            />
            <HeadlineCard
              label="Residual to Street (SELL)"
              value={fmtUSD(crbResult.total_residual_sell_notional)}
            />
          </div>
        )}
      </div>
    </div>
  );
}
