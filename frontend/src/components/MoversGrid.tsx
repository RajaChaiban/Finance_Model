import { MoverRow } from "../api/client";

type Variant = "gain" | "lose" | "vol";

interface MoverColumnProps {
  title: string;
  subtitle: string;
  rows: MoverRow[];
  variant: Variant;
  onPick: (ticker: string, price: number) => void;
}

function fmtPct(pct: number) {
  const sign = pct >= 0 ? "+" : "";
  return `${sign}${pct.toFixed(2)}%`;
}

function fmtHv(hv: number | null) {
  if (hv === null || hv === undefined) return "—";
  return `${(hv * 100).toFixed(1)}%`;
}

function MoverColumn({ title, subtitle, rows, variant, onPick }: MoverColumnProps) {
  return (
    <div className={`vd-mover-col vd-mover-col--${variant}`}>
      <div className="vd-mover-col-head">
        <h3 className="vd-mover-col-title">{title}</h3>
        <span className="vd-mover-col-sub">{subtitle}</span>
      </div>
      {rows.length === 0 ? (
        <div className="vd-mover-empty">No data</div>
      ) : (
        <ul className="vd-mover-list">
          {rows.map((row, idx) => {
            const showHv = variant === "vol";
            return (
              <li key={row.ticker}>
                <button
                  type="button"
                  className="vd-mover-row"
                  onClick={() => onPick(row.ticker, row.price)}
                  title={`Click to load ${row.ticker} into the pricer`}
                >
                  <span className="vd-mover-rank">{idx + 1}</span>
                  <span className="vd-mover-ticker">{row.ticker}</span>
                  <span className="vd-mover-price">
                    ${row.price.toLocaleString("en-US", { maximumFractionDigits: 2 })}
                  </span>
                  {showHv ? (
                    <span className="vd-mover-metric">{fmtHv(row.hv30)}</span>
                  ) : (
                    <span
                      className={`vd-mover-metric ${row.change_pct >= 0 ? "up" : "down"}`}
                    >
                      {fmtPct(row.change_pct)}
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

interface MoversGridProps {
  gainers: MoverRow[];
  losers: MoverRow[];
  volatile: MoverRow[];
  isLoading: boolean;
  isStale: boolean;
  onPickTicker: (ticker: string, price: number) => void;
}

export function MoversGrid({
  gainers,
  losers,
  volatile,
  isLoading,
  isStale,
  onPickTicker,
}: MoversGridProps) {
  if (isLoading && gainers.length === 0 && losers.length === 0 && volatile.length === 0) {
    return (
      <section className="vd-movers-section" data-testid="vd-movers-grid">
        <div className="vd-movers-grid">
          {["Gainers", "Losers", "Volatile"].map((t) => (
            <div className="vd-mover-col vd-skeleton-col" key={t}>
              <div className="vd-mover-col-head">
                <h3 className="vd-mover-col-title">{t}</h3>
              </div>
              <div className="vd-mover-empty">Loading…</div>
            </div>
          ))}
        </div>
      </section>
    );
  }

  return (
    <section className="vd-movers-section" data-testid="vd-movers-grid">
      <div className="vd-movers-header">
        <h2 className="vd-movers-title">Market Movers</h2>
        <p className="vd-movers-sub">
          Click any row to load it into the pricer.{" "}
          {isStale && <span className="vd-stale">Showing last cached snapshot.</span>}
        </p>
      </div>
      <div className="vd-movers-grid">
        <MoverColumn
          title="Top Gainers"
          subtitle="Daily % change"
          rows={gainers}
          variant="gain"
          onPick={onPickTicker}
        />
        <MoverColumn
          title="Top Losers"
          subtitle="Daily % change"
          rows={losers}
          variant="lose"
          onPick={onPickTicker}
        />
        <MoverColumn
          title="Most Volatile"
          subtitle="Realised vol — 30d annualised"
          rows={volatile}
          variant="vol"
          onPick={onPickTicker}
        />
      </div>
    </section>
  );
}
