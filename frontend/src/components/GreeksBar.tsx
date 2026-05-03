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
  pinRisk?: boolean;
}

const ORDER: { key: string; label: string }[] = [
  { key: "delta", label: "Δ Delta" },
  { key: "gamma", label: "Γ Gamma" },
  { key: "vega", label: "ν Vega" },
  { key: "theta", label: "Θ Theta" },
  { key: "rho", label: "ρ Rho" },
];

// NaN/undefined-safe coercion. Returns null for unrenderable values so the UI
// shows "—" instead of crashing or printing "NaN".
function safeNumber(v: unknown): number | null {
  if (typeof v !== "number") return null;
  if (!Number.isFinite(v)) return null;
  return v;
}

export function GreeksBar({ greeks, pinRisk }: GreeksBarProps) {
  if (!greeks) return null;
  // Build a row per Greek; rows with NaN/undefined values are kept so the
  // user sees the dash, but they get value=0 in the chart so recharts doesn't
  // produce a NaN domain.
  const rows = ORDER.filter((g) => greeks[g.key] !== undefined).map((g) => {
    const safe = safeNumber(greeks[g.key]);
    return {
      label: g.label,
      value: safe === null ? 0 : Number(safe.toFixed(6)),
      display: safe === null ? "—" : safe.toFixed(6),
      isNaN: safe === null,
    };
  });

  if (rows.length === 0) return null;

  return (
    <div className="vd-chart-card">
      <div className="vd-chart-head">
        <h4>Greeks</h4>
        <span className="vd-chart-sub">Sensitivities at current spot</span>
      </div>
      {pinRisk && (
        <div
          className="pin-risk-banner"
          role="alert"
          data-testid="pin-risk-banner"
          style={{
            margin: "0 0.75rem 0.5rem",
            padding: "0.7rem 0.95rem",
            borderRadius: "var(--radius-md)",
            background: "rgba(245, 158, 11, 0.12)",
            border: "1px solid var(--warning)",
            color: "var(--text-primary)",
            fontSize: "0.85rem",
            lineHeight: 1.4,
          }}
        >
          <strong style={{ color: "var(--warning)" }}>⚠ Pin risk:</strong>{" "}
          spot is at the barrier. Greeks are unreliable in this regime — do
          not hedge against these values.
        </div>
      )}
      <div className="vd-chart-body">
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={rows} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
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
              formatter={(_v: number, _n, p: { payload?: { display?: string } }) => [
                p?.payload?.display ?? "—",
                "Value",
              ]}
            />
            <Bar dataKey="value" radius={[4, 4, 0, 0]}>
              {rows.map((d, i) => (
                <Cell
                  key={i}
                  fill={
                    d.isNaN
                      ? "var(--text-muted)"
                      : d.value >= 0
                      ? "var(--accent)"
                      : "var(--danger)"
                  }
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
