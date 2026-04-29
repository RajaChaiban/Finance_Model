import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

interface GreeksBarProps {
  greeks: Record<string, number>;
}

const ORDER: { key: string; label: string }[] = [
  { key: "delta", label: "Δ Delta" },
  { key: "gamma", label: "Γ Gamma" },
  { key: "vega", label: "ν Vega" },
  { key: "theta", label: "Θ Theta" },
  { key: "rho", label: "ρ Rho" },
];

export function GreeksBar({ greeks }: GreeksBarProps) {
  if (!greeks) return null;
  const data = ORDER.filter((g) => greeks[g.key] !== undefined).map((g) => ({
    label: g.label,
    value: Number(greeks[g.key].toFixed(6)),
  }));

  if (data.length === 0) return null;

  return (
    <div className="vd-chart-card">
      <div className="vd-chart-head">
        <h4>Greeks</h4>
        <span className="vd-chart-sub">Sensitivities at current spot</span>
      </div>
      <div className="vd-chart-body">
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
            <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
            <XAxis dataKey="label" stroke="var(--text-muted)" tick={{ fontSize: 11 }} />
            <YAxis stroke="var(--text-muted)" tick={{ fontSize: 11 }} />
            <Tooltip
              contentStyle={{
                background: "var(--bg-elevated)",
                border: "1px solid var(--border)",
                borderRadius: 6,
                color: "var(--text-primary)",
              }}
              formatter={(v: number) => [v.toFixed(6), "Value"]}
            />
            <Bar dataKey="value" radius={[4, 4, 0, 0]}>
              {data.map((d, i) => (
                <Cell
                  key={i}
                  fill={d.value >= 0 ? "var(--accent)" : "var(--danger)"}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
