import { Area, AreaChart, ResponsiveContainer } from "recharts";
import { MoverRow } from "../api/client";

interface IndexCardProps {
  row: MoverRow;
  onPickTicker?: (ticker: string, price: number) => void;
}

function IndexCard({ row, onPickTicker }: IndexCardProps) {
  const up = row.change_pct >= 0;
  const data = row.spark.map((c, i) => ({ i, c }));
  const colorStroke = up ? "var(--success)" : "var(--danger)";
  const display = row.ticker.replace("^", "");
  const clickable = Boolean(onPickTicker);

  const inner = (
    <>
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
    </>
  );

  if (!clickable) {
    return <div className="vd-index-card">{inner}</div>;
  }

  return (
    <button
      type="button"
      className="vd-index-card vd-index-card-clickable"
      onClick={() => onPickTicker!(row.ticker, row.price)}
      aria-label={`Load ${display} into the pricer`}
    >
      {inner}
    </button>
  );
}

interface IndexTickerStripProps {
  indices: MoverRow[];
  isLoading?: boolean;
  onPickTicker?: (ticker: string, price: number) => void;
}

export function IndexTickerStrip({ indices, isLoading, onPickTicker }: IndexTickerStripProps) {
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
        <IndexCard key={row.ticker} row={row} onPickTicker={onPickTicker} />
      ))}
    </div>
  );
}
