import {
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { GammaLadderPoint, ScenarioGrid } from "../types";

interface SensitivityHeatmapProps {
  scenarioGrid?: ScenarioGrid;
  gammaLadder?: GammaLadderPoint[];
}

// ---------------------------------------------------------------------------
// Colour helpers — opacity scaled to the cell's position in [min, max].
// We use the CSS-variable palette so we respect the dark institutional theme.
// ---------------------------------------------------------------------------

function cellBackground(value: number, min: number, max: number): string {
  if (max === min) return "rgba(139, 92, 246, 0.18)";
  const t = (value - min) / (max - min); // 0 → 1
  // low values: danger tint; high values: accent/success tint
  if (t < 0.5) {
    // transition from danger at 0 to neutral at 0.5
    const opacity = (0.5 - t) * 0.6;
    return `rgba(239, 68, 68, ${opacity.toFixed(3)})`;
  } else {
    // transition from neutral at 0.5 to accent at 1
    const opacity = (t - 0.5) * 0.7;
    return `rgba(16, 185, 129, ${opacity.toFixed(3)})`;
  }
}

// ---------------------------------------------------------------------------
// ScenarioGridTable — spot rows × vol columns, coloured by value
// ---------------------------------------------------------------------------

function ScenarioGridTable({ grid }: { grid: ScenarioGrid }) {
  const { values, spot_axis, vol_axis } = grid;

  // Flatten to find global min/max
  let min = Infinity;
  let max = -Infinity;
  for (const row of values) {
    for (const v of row) {
      if (v < min) min = v;
      if (v > max) max = v;
    }
  }

  return (
    <div
      data-testid="scenario-grid-table"
      style={{ overflowX: "auto", width: "100%" }}
    >
      <table
        style={{
          borderCollapse: "collapse",
          fontSize: "0.72rem",
          width: "100%",
          tableLayout: "auto",
          color: "var(--text-primary)",
        }}
      >
        <thead>
          <tr>
            {/* Top-left corner: spot \ vol */}
            <th
              style={{
                padding: "4px 6px",
                borderBottom: "1px solid var(--border)",
                borderRight: "1px solid var(--border)",
                color: "var(--text-muted)",
                textAlign: "right",
                whiteSpace: "nowrap",
              }}
            >
              S / σ
            </th>
            {vol_axis.map((v, ci) => (
              <th
                key={ci}
                style={{
                  padding: "4px 6px",
                  borderBottom: "1px solid var(--border)",
                  color: "var(--text-secondary)",
                  textAlign: "center",
                  whiteSpace: "nowrap",
                }}
              >
                {(v * 100).toFixed(0)}%
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {spot_axis.map((s, ri) => (
            <tr key={ri}>
              <td
                style={{
                  padding: "3px 6px",
                  borderRight: "1px solid var(--border)",
                  color: "var(--text-secondary)",
                  textAlign: "right",
                  whiteSpace: "nowrap",
                }}
              >
                ${s.toFixed(1)}
              </td>
              {(values[ri] ?? []).map((cell, ci) => (
                <td
                  key={ci}
                  style={{
                    padding: "3px 6px",
                    textAlign: "center",
                    background: cellBackground(cell, min, max),
                    borderRadius: "3px",
                    whiteSpace: "nowrap",
                  }}
                >
                  {cell.toFixed(2)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// GammaLadderChart — recharts ComposedChart with delta (left axis) and
// gamma (right axis) plotted against spot.
// ---------------------------------------------------------------------------

function GammaLadderChart({ ladder }: { ladder: GammaLadderPoint[] }) {
  // recharts needs a sorted array keyed by the x-axis field
  const data = [...ladder].sort((a, b) => a.spot - b.spot);

  return (
    <div data-testid="gamma-ladder-chart" style={{ width: "100%" }}>
      <ResponsiveContainer width="100%" height={240}>
        <ComposedChart
          data={data}
          margin={{ top: 10, right: 40, bottom: 0, left: 0 }}
        >
          <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
          <XAxis
            dataKey="spot"
            stroke="var(--text-muted)"
            tick={{ fontSize: 11 }}
            tickFormatter={(v) => `$${Number(v).toFixed(0)}`}
          />
          {/* Left axis — delta */}
          <YAxis
            yAxisId="delta"
            orientation="left"
            stroke="var(--accent)"
            tick={{ fontSize: 11, fill: "var(--accent)" }}
            tickFormatter={(v) => Number(v).toFixed(3)}
          />
          {/* Right axis — gamma */}
          <YAxis
            yAxisId="gamma"
            orientation="right"
            stroke="var(--success)"
            tick={{ fontSize: 11, fill: "var(--success)" }}
            tickFormatter={(v) => Number(v).toFixed(4)}
          />
          <Tooltip
            contentStyle={{
              background: "var(--bg-elevated)",
              border: "1px solid var(--border)",
              borderRadius: 6,
              color: "var(--text-primary)",
            }}
            formatter={(val: number, name: string) => [
              val.toFixed(5),
              name === "delta" ? "Delta (Δ)" : "Gamma (Γ)",
            ]}
            labelFormatter={(v) => `Spot $${Number(v).toFixed(2)}`}
          />
          <Legend
            wrapperStyle={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}
          />
          <Line
            yAxisId="delta"
            type="monotone"
            dataKey="delta"
            stroke="var(--accent)"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
            name="Delta (Δ)"
          />
          <Line
            yAxisId="gamma"
            type="monotone"
            dataKey="gamma"
            stroke="var(--success)"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
            name="Gamma (Γ)"
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export function SensitivityHeatmap({
  scenarioGrid,
  gammaLadder,
}: SensitivityHeatmapProps) {
  // Graceful no-op: render nothing if neither payload is present
  if (!scenarioGrid && !gammaLadder) return null;

  return (
    <div
      className="vd-chart-card"
      data-testid="sensitivity-heatmap"
      style={{ marginTop: "14px" }}
    >
      {/* ---- Scenario grid ---- */}
      {scenarioGrid && (
        <div style={{ marginBottom: gammaLadder ? "20px" : 0 }}>
          <div className="vd-chart-head">
            <h4>Scenario Sensitivity Grid</h4>
            <span className="vd-chart-sub">
              Option price across spot × vol scenarios (green = high, red = low)
            </span>
          </div>
          <div className="vd-chart-body">
            <ScenarioGridTable grid={scenarioGrid} />
          </div>
        </div>
      )}

      {/* ---- Gamma ladder ---- */}
      {gammaLadder && gammaLadder.length > 0 && (
        <div>
          <div className="vd-chart-head">
            <h4>Gamma Ladder</h4>
            <span className="vd-chart-sub">
              Delta (left axis) and Gamma (right axis) across spot range
            </span>
          </div>
          <div className="vd-chart-body">
            <GammaLadderChart ladder={gammaLadder} />
          </div>
        </div>
      )}
    </div>
  );
}
