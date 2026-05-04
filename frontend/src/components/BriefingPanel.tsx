import { useBriefing } from "../hooks/useBriefing";
import type { BriefingVol } from "../types";

const REGIME_CLASS: Record<BriefingVol["regime"], string> = {
  low: "vd-briefing-regime-low",
  normal: "vd-briefing-regime-normal",
  elevated: "vd-briefing-regime-elevated",
  stressed: "vd-briefing-regime-stressed",
};

function fmt(n: number, decimals = 2): string {
  return n.toFixed(decimals);
}

function sign(n: number): string {
  return n >= 0 ? `+${fmt(n)}%` : `${fmt(n)}%`;
}

export function BriefingPanel() {
  const { data, isLoading, error } = useBriefing();

  if (isLoading) {
    return (
      <div className="vd-briefing-section">
        <div className="vd-briefing-card vd-briefing-skeleton">
          <div className="vd-briefing-skeleton-row" />
          <div className="vd-briefing-skeleton-row vd-briefing-skeleton-short" />
          <div className="vd-briefing-skeleton-grid">
            <div className="vd-briefing-skeleton-col" />
            <div className="vd-briefing-skeleton-col" />
            <div className="vd-briefing-skeleton-col" />
          </div>
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="vd-briefing-section">
        <div className="vd-briefing-empty">
          <span className="vd-briefing-empty-icon">&#9200;</span>
          <span>Briefing not yet generated — check back at 7:30 AM ET</span>
        </div>
      </div>
    );
  }

  const top3Sectors = data.equity.sector_movers.slice(0, 3);
  const top4Headlines = data.headlines.slice(0, 4);

  return (
    <div className="vd-briefing-section">
      <div className="vd-briefing-card">
        {/* Title row */}
        <div className="vd-briefing-title-row">
          <h2 className="vd-briefing-title">{data.title}</h2>
          <span className="vd-briefing-asof">as of {data.as_of}</span>
        </div>

        {/* Summary */}
        <p className="vd-briefing-summary">{data.summary}</p>

        {/* Three-column grid */}
        <div className="vd-briefing-grid">
          {/* Col 1: Macro rates */}
          <div className="vd-briefing-col">
            <div className="vd-briefing-col-head">Macro Rates</div>
            {data.macro.map((row) => {
              const isUp = !row.delta_1d.startsWith("-");
              return (
                <div key={row.series_id} className="vd-briefing-macro-row">
                  <div className="vd-briefing-macro-label">{row.label}</div>
                  <div className="vd-briefing-macro-value-row">
                    <span className="vd-briefing-macro-value">{row.value}</span>
                    <span className={`vd-briefing-macro-delta ${isUp ? "up" : "down"}`}>
                      {row.delta_1d}
                    </span>
                  </div>
                  <div className="vd-briefing-macro-context">{row.context}</div>
                </div>
              );
            })}
          </div>

          {/* Col 2: Equity & vol */}
          <div className="vd-briefing-col">
            <div className="vd-briefing-col-head">Equity &amp; Vol</div>
            {data.equity.indices.map((idx) => (
              <div key={idx.symbol} className="vd-briefing-eq-row">
                <div className="vd-briefing-eq-name">
                  <span className="vd-briefing-eq-sym">{idx.symbol}</span>
                  <span className="vd-briefing-eq-full">{idx.name}</span>
                </div>
                <div className="vd-briefing-eq-nums">
                  <span className="vd-briefing-eq-level">{fmt(idx.level, 0)}</span>
                  <span className={`vd-briefing-eq-chg ${idx.change_pct >= 0 ? "up" : "down"}`}>
                    {sign(idx.change_pct)}
                  </span>
                </div>
              </div>
            ))}
            <div className="vd-briefing-divider" />
            {data.equity.vol.map((v) => (
              <div key={v.symbol} className="vd-briefing-eq-row">
                <div className="vd-briefing-eq-name">
                  <span className="vd-briefing-eq-sym">{v.symbol}</span>
                  <span className={`vd-briefing-regime-pill ${REGIME_CLASS[v.regime]}`}>
                    {v.regime}
                  </span>
                </div>
                <div className="vd-briefing-eq-nums">
                  <span className="vd-briefing-eq-level">{fmt(v.level, 2)}</span>
                  <span className={`vd-briefing-eq-chg ${v.change_pct >= 0 ? "up" : "down"}`}>
                    {sign(v.change_pct)}
                  </span>
                </div>
              </div>
            ))}
            {top3Sectors.length > 0 && (
              <>
                <div className="vd-briefing-divider" />
                <div className="vd-briefing-col-sub">Sector Movers</div>
                {top3Sectors.map((s) => (
                  <div key={s.etf} className="vd-briefing-sector-row">
                    <div className="vd-briefing-sector-left">
                      <span className="vd-briefing-eq-sym">{s.etf}</span>
                      <span className="vd-briefing-sector-name">{s.sector}</span>
                    </div>
                    <div className="vd-briefing-sector-right">
                      <span className={`vd-briefing-eq-chg ${s.change_pct >= 0 ? "up" : "down"}`}>
                        {sign(s.change_pct)}
                      </span>
                      <span className="vd-briefing-sector-driver">{s.driver}</span>
                    </div>
                  </div>
                ))}
              </>
            )}
          </div>

          {/* Col 3: Headlines & themes */}
          <div className="vd-briefing-col">
            <div className="vd-briefing-col-head">Headlines</div>
            {top4Headlines.map((h, i) => (
              <a
                key={i}
                href={h.url}
                target="_blank"
                rel="noopener noreferrer"
                className="vd-briefing-headline"
              >
                <span className="vd-briefing-headline-title">{h.title}</span>
                <span className="vd-briefing-headline-meta">
                  {h.source} · {h.published}
                </span>
              </a>
            ))}
            {data.themes.length > 0 && (
              <>
                <div className="vd-briefing-col-sub" style={{ marginTop: "12px" }}>Themes</div>
                <div className="vd-briefing-themes">
                  {data.themes.map((t) => (
                    <span key={t} className="vd-briefing-theme-chip">{t}</span>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>

        {/* Footer */}
        {data.sources.length > 0 && (
          <div className="vd-briefing-footer">
            Sources:{" "}
            {data.sources.map((s, i) => (
              <span key={s.name}>
                <a href={s.url} target="_blank" rel="noopener noreferrer" className="vd-briefing-source-link">
                  {s.name}
                </a>
                {i < data.sources.length - 1 && " · "}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
