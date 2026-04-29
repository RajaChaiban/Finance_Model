import { Area, AreaChart, ResponsiveContainer } from "recharts";
import { MoverRow } from "../api/client";

interface IndexCardProps {
  row: MoverRow;
}

function IndexCard({ row }: IndexCardProps) {
  const up = row.change_pct >= 0;
  const data = row.spark.map((c, i) => ({ i, c }));
  const colorStroke = up ? "var(--success)" : "var(--danger)";
  const colorFill = up ? "rgba(16,185,129,0.18)" : "rgba(239,68,68,0.18)";
  const display = row.ticker.replace("^", "");

  return (
    <div className="vd-index-card">
      <div className="vd-index-row">
        <span className="vd-index-ticker">{display}</span>
        <span className={`vd-index-change ${up ? "up" : "down"}`}>
          {up ? "▲" : "▼"} {Math.abs(row.change_pct).toFixed(2)}%
        </span>
      </div>
      <div className="vd-index-price">
        ${row.price.toLocaleString("en-US", { maximumFractionDigits: 2 })}
      </div>
      <div className="vd-index-spark">
        <ResponsiveContainer width="100%" height={36}>
          <AreaChart data={data} margin={{ top: 2, right: 0, bottom: 2, left: 0 }}>
            <defs>
              <linearGradient id={`g-${row.ticker}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={colorStroke} stopOpacity={0.45} />
                <stop offset="100%" stopColor={colorStroke} stopOpacity={0} />
              </linearGradient>
            </defs>
            <Area
              type="monotone"
              dataKey="c"
              stroke={colorStroke}
              strokeWidth={1.6}
              fill={`url(#g-${row.ticker})`}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

interface IndexTickerStripProps {
  indices: MoverRow[];
  isLoading?: boolean;
}

export function IndexTickerStrip({ indices, isLoading }: IndexTickerStripProps) {
  if (isLoading && indices.length === 0) {
    return (
      <div className="vd-index-strip" data-testid="vd-index-strip">
        {Array.from({ length: 5 }).map((_, i) => (
          <div className="vd-index-card vd-skeleton" key={i} />
        ))}
      </div>
    );
  }
  return (
    <div className="vd-index-strip" data-testid="vd-index-strip">
      {indices.map((row) => (
        <IndexCard key={row.ticker} row={row} />
      ))}
    </div>
  );
}
