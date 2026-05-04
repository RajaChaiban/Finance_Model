import { useMemo } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

interface PayoffChartProps {
  optionType: string;
  strike: number;
  spot: number;
  premium: number;
  barrier?: number;
}

interface PayoffPoint {
  S: number;
  payoff: number;
  pnl: number;
}

function intrinsic(optionType: string, S: number, K: number, barrier?: number): number {
  const t = optionType.toLowerCase();
  const isCall = t.endsWith("_call");
  const isPut = t.endsWith("_put");
  const callPayoff = Math.max(S - K, 0);
  const putPayoff = Math.max(K - S, 0);
  const base = isCall ? callPayoff : isPut ? putPayoff : 0;

  if (t.startsWith("knockout_") && barrier !== undefined && barrier > 0) {
    // Up-and-out for calls (barrier above spot), down-and-out for puts (barrier below).
    // Approximate by zeroing out beyond the barrier.
    if (isCall && S >= barrier) return 0;
    if (isPut && S <= barrier) return 0;
  }
  if (t.startsWith("knockin_") && barrier !== undefined && barrier > 0) {
    if (isCall && S < barrier) return 0;
    if (isPut && S > barrier) return 0;
  }
  return base;
}

export function PayoffChart({ optionType, strike, spot, premium, barrier }: PayoffChartProps) {
  const safeStrike = Number.isFinite(strike) && strike > 0 ? strike : 0;
  const safeSpot = Number.isFinite(spot) && spot > 0 ? spot : safeStrike;
  const safePremium = Number.isFinite(premium) ? premium : 0;
  const safeBarrier =
    barrier !== undefined && Number.isFinite(barrier) && barrier > 0
      ? barrier
      : undefined;
  const data = useMemo<PayoffPoint[]>(() => {
    if (safeStrike <= 0 && safeSpot <= 0) return [];
    const lo = Math.max(0.01, Math.min(safeStrike, safeSpot) * 0.5);
    const hi = Math.max(safeStrike, safeSpot) * 1.5;
    if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) return [];
    const N = 80;
    const step = (hi - lo) / N;
    const points: PayoffPoint[] = [];
    for (let i = 0; i <= N; i++) {
      const S = lo + i * step;
      const p = intrinsic(optionType, S, safeStrike, safeBarrier);
      const pnl = p - safePremium;
      if (!Number.isFinite(S) || !Number.isFinite(p) || !Number.isFinite(pnl)) continue;
      points.push({ S: +S.toFixed(2), payoff: +p.toFixed(4), pnl: +pnl.toFixed(4) });
    }
    return points;
  }, [optionType, safeStrike, safeSpot, safePremium, safeBarrier]);

  return (
    <div className="vd-chart-card">
      <div className="vd-chart-head">
        <h4>Payoff &amp; P&amp;L at Expiry</h4>
        <span className="vd-chart-sub">
          Premium ${safePremium.toFixed(2)} • Strike ${safeStrike.toFixed(2)} • Spot ${safeSpot.toFixed(2)}
        </span>
      </div>
      <div className="vd-chart-body">
        <ResponsiveContainer width="100%" height={240}>
          <AreaChart data={data} margin={{ top: 10, right: 16, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="vd-gain" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--success)" stopOpacity={0.45} />
                <stop offset="100%" stopColor="var(--success)" stopOpacity={0} />
              </linearGradient>
              <linearGradient id="vd-loss" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--danger)" stopOpacity={0.0} />
                <stop offset="100%" stopColor="var(--danger)" stopOpacity={0.4} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
            <XAxis
              dataKey="S"
              stroke="var(--text-muted)"
              tick={{ fontSize: 11 }}
              tickFormatter={(v) => `$${Number(v).toFixed(0)}`}
            />
            <YAxis
              stroke="var(--text-muted)"
              tick={{ fontSize: 11 }}
              tickFormatter={(v) => `$${Number(v).toFixed(0)}`}
            />
            <Tooltip
              contentStyle={{
                background: "var(--bg-elevated)",
                border: "1px solid var(--border)",
                borderRadius: 6,
                color: "var(--text-primary)",
              }}
              formatter={(val: number, name) => [`$${val.toFixed(2)}`, name === "pnl" ? "P&L" : "Payoff"]}
              labelFormatter={(v) => `S_T = $${Number(v).toFixed(2)}`}
            />
            <ReferenceLine y={0} stroke="var(--text-muted)" strokeDasharray="2 2" />
            <ReferenceLine x={safeSpot} stroke="var(--accent)" strokeDasharray="3 3" label={{ value: "spot", position: "top", fill: "var(--accent-hover)", fontSize: 10 }} />
            <ReferenceLine x={safeStrike} stroke="var(--text-secondary)" strokeDasharray="3 3" label={{ value: "K", position: "top", fill: "var(--text-secondary)", fontSize: 10 }} />
            <Area
              type="monotone"
              dataKey="pnl"
              stroke="var(--accent)"
              strokeWidth={2}
              fill="url(#vd-gain)"
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
