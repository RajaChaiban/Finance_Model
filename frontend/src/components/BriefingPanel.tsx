import { useBriefing } from "../hooks/useBriefing";
import type {
  BriefingVol,
  BriefingStructurer,
  BriefingTrader,
  BriefingVolTerm,
  BriefingRvIv,
  BriefingCreditProxy,
  BriefingOvernightFuture,
  BriefingGlobalIndex,
  BriefingCrossAsset,
  BriefingVolCarry,
} from "../types";

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

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

/** Render a number that may be null as "—" */
function fmtOrDash(n: number | null, decimals = 2): string {
  return n == null ? "—" : n.toFixed(decimals);
}

function signOrDash(n: number | null): string {
  if (n == null) return "—";
  return n >= 0 ? `+${fmt(n)}%` : `${fmt(n)}%`;
}

function chgClass(n: number | null): string {
  if (n == null) return "";
  return n >= 0 ? "up" : "down";
}

// ---------------------------------------------------------------------------
// Theme helpers (unchanged from original)
// ---------------------------------------------------------------------------

const THEME_CATEGORY_KEYWORDS: Array<{ test: RegExp; key: string; label: string }> = [
  { test: /^rates\b/i,           key: "rates",   label: "Rates" },
  { test: /^yield\s*curve/i,     key: "curve",   label: "Curve" },
  { test: /^vol\s*regime/i,      key: "vol",     label: "Vol" },
  { test: /^skew\b/i,            key: "skew",    label: "Skew" },
  { test: /^sector\s*focus/i,    key: "sector",  label: "Sector" },
  { test: /^event\s*risk/i,      key: "event",   label: "Event" },
];

function parseTheme(raw: string): { key: string; label: string; body: string } {
  for (const { test, key, label } of THEME_CATEGORY_KEYWORDS) {
    if (test.test(raw)) {
      const colonIdx = raw.indexOf(":");
      const dashIdx = raw.indexOf("—");
      let body = raw;
      if (colonIdx !== -1 && (dashIdx === -1 || colonIdx < dashIdx)) {
        body = raw.slice(colonIdx + 1).trim();
      } else if (dashIdx !== -1) {
        body = raw.slice(dashIdx + 1).trim();
      }
      return { key, label, body };
    }
  }
  return { key: "default", label: "Theme", body: raw };
}

function formatAsOf(iso: string): string {
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const month = d.toLocaleString("en-US", { month: "short", timeZone: "America/New_York" });
    const day = d.toLocaleString("en-US", { day: "numeric", timeZone: "America/New_York" });
    const time = d.toLocaleString("en-US", {
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
      timeZone: "America/New_York",
    });
    return `${month} ${day}, ${time} ET`;
  } catch {
    return iso;
  }
}

// ---------------------------------------------------------------------------
// Signal / shape pill helpers
// ---------------------------------------------------------------------------

function volSignalClass(signal: BriefingRvIv["signal"]): string {
  if (signal === "sell vol") return "vd-pill-green";
  if (signal === "buy vol") return "vd-pill-red";
  return "vd-pill-muted";
}

function carrySignalClass(signal: BriefingVolCarry["carry_signal"]): string {
  if (signal === "sell premium") return "vd-pill-green";
  if (signal === "buy premium") return "vd-pill-red";
  return "vd-pill-muted";
}

function slopeShapeClass(shape: "contango" | "backwardation" | "flat"): string {
  if (shape === "contango") return "vd-pill-info";
  if (shape === "backwardation") return "vd-pill-amber";
  return "vd-pill-muted";
}

// ---------------------------------------------------------------------------
// Structurer's Snapshot sub-component
// ---------------------------------------------------------------------------

function StructurerPanel({ s }: { s: BriefingStructurer }) {
  // Build tenor order for vol term structure table
  const TENORS = ["9D", "30D", "3M", "6M"];
  const termMap: Record<string, BriefingVolTerm> = {};
  for (const vt of s.vol_term_structure) {
    termMap[vt.tenor] = vt;
  }

  const scalarMetrics = [
    { key: "skew_index",          label: "Skew Index",  data: s.skew_index },
    { key: "vvix",                label: "VVIX",        data: s.vvix },
    { key: "move",                label: "MOVE",        data: s.move },
    { key: "implied_correlation", label: "Impl. Corr.", data: s.implied_correlation },
  ];

  return (
    <details className="vd-briefing-structurer-panel" open>
      <summary className="vd-briefing-desk-summary">
        <span className="vd-briefing-desk-title">Structurer's Snapshot</span>
        <span className="vd-briefing-desk-subtitle">vol surface · skew · carry · credit</span>
        <span className="vd-briefing-desk-chevron" aria-hidden="true">›</span>
      </summary>

      <div className="vd-briefing-structurer-body">

        {/* 1. Vol term structure table */}
        {s.vol_term_structure.length > 0 && (
          <div className="vd-briefing-structurer-block">
            <div className="vd-briefing-col-sub">Vol Term Structure</div>
            <table className="vd-briefing-term-table">
              <thead>
                <tr>
                  <th>Tenor</th>
                  {TENORS.map((t) => <th key={t}>{t}</th>)}
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className="vd-briefing-term-label">Level</td>
                  {TENORS.map((t) => (
                    <td key={t} className="vd-briefing-term-val">
                      {fmtOrDash(termMap[t]?.level ?? null, 2)}
                    </td>
                  ))}
                </tr>
                <tr>
                  <td className="vd-briefing-term-label">Δ%</td>
                  {TENORS.map((t) => {
                    const cp = termMap[t]?.change_pct ?? null;
                    return (
                      <td key={t} className={`vd-briefing-term-chg ${chgClass(cp)}`}>
                        {signOrDash(cp)}
                      </td>
                    );
                  })}
                </tr>
              </tbody>
            </table>
            <div className="vd-briefing-term-slope-row">
              <span className={`vd-briefing-pill ${slopeShapeClass(s.term_structure_slope.shape)}`}>
                {s.term_structure_slope.shape}
              </span>
              <span className="vd-briefing-term-slope-ctx">{s.term_structure_slope.context}</span>
            </div>
          </div>
        )}

        {/* 2. Skew / VVIX / MOVE / Impl. Corr strip */}
        <div className="vd-briefing-structurer-block">
          <div className="vd-briefing-col-sub">Skew · Vol of Vol · Rates Vol · Correlation</div>
          <div className="vd-briefing-scalar-strip">
            {scalarMetrics.map(({ key, label, data }) => (
              <div key={key} className="vd-briefing-scalar-tile">
                <div className="vd-briefing-scalar-label">{label}</div>
                <div className="vd-briefing-scalar-sym">{data.symbol}</div>
                <div className="vd-briefing-scalar-level">{fmtOrDash(data.level, 2)}</div>
                <div className={`vd-briefing-scalar-chg ${chgClass(data.change_pct)}`}>
                  {signOrDash(data.change_pct)}
                </div>
                <div className="vd-briefing-scalar-ctx">{data.context}</div>
              </div>
            ))}
          </div>
        </div>

        {/* 3. Realized vs Implied table */}
        {s.realized_vs_implied.length > 0 && (
          <div className="vd-briefing-structurer-block">
            <div className="vd-briefing-col-sub">Realized vs Implied</div>
            <table className="vd-briefing-rv-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>RV 30D</th>
                  <th>IV</th>
                  <th>Spread</th>
                  <th>Signal</th>
                </tr>
              </thead>
              <tbody>
                {s.realized_vs_implied.map((row: BriefingRvIv) => (
                  <tr key={row.symbol}>
                    <td className="vd-briefing-rv-sym">{row.symbol}</td>
                    <td>{fmtOrDash(row.rv_30d, 1)}</td>
                    <td>{fmtOrDash(row.iv_proxy, 1)}</td>
                    <td className={row.spread_vol_pts != null && row.spread_vol_pts >= 0 ? "up" : "down"}>
                      {fmtOrDash(row.spread_vol_pts, 1)}
                    </td>
                    <td>
                      <span className={`vd-briefing-pill ${volSignalClass(row.signal)}`}>
                        {row.signal}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* 4. Credit proxy strip */}
        {s.credit_proxy.length > 0 && (
          <div className="vd-briefing-structurer-block">
            <div className="vd-briefing-col-sub">Credit Proxy</div>
            <div className="vd-briefing-credit-strip">
              {s.credit_proxy.map((cp: BriefingCreditProxy) => (
                <div key={cp.symbol} className="vd-briefing-credit-tile">
                  <div className="vd-briefing-eq-sym">{cp.symbol}</div>
                  <div className="vd-briefing-eq-full">{cp.name}</div>
                  <div className="vd-briefing-eq-level">{fmtOrDash(cp.level, 2)}</div>
                  <div className={`vd-briefing-eq-chg ${chgClass(cp.change_pct)}`}>
                    {signOrDash(cp.change_pct)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

      </div>
    </details>
  );
}

// ---------------------------------------------------------------------------
// Trader's Snapshot sub-component
// ---------------------------------------------------------------------------

function TraderPanel({ t }: { t: BriefingTrader }) {
  return (
    <details className="vd-briefing-trader-panel" open>
      <summary className="vd-briefing-desk-summary">
        <span className="vd-briefing-desk-title">Trader's Snapshot</span>
        <span className="vd-briefing-desk-subtitle">overnight · cross-asset · carry · global</span>
        <span className="vd-briefing-desk-chevron" aria-hidden="true">›</span>
      </summary>

      <div className="vd-briefing-trader-body">

        {/* 1. Overnight futures grid */}
        {t.overnight_futures.length > 0 && (
          <div className="vd-briefing-trader-block">
            <div className="vd-briefing-col-sub">Overnight Futures</div>
            <div className="vd-briefing-futures-grid">
              {t.overnight_futures.map((f: BriefingOvernightFuture) => (
                <div key={f.symbol} className="vd-briefing-future-tile">
                  <div className="vd-briefing-future-name">{f.name}</div>
                  <div className="vd-briefing-future-level">{fmtOrDash(f.level, 2)}</div>
                  <div className={`vd-briefing-eq-chg ${chgClass(f.change_pct)}`}>
                    {signOrDash(f.change_pct)}
                  </div>
                  {(f.session_low != null || f.session_high != null) && (
                    <div className="vd-briefing-future-range">
                      O/N {fmtOrDash(f.session_low, 0)} – {fmtOrDash(f.session_high, 0)}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 2. Global overnight strip */}
        {t.global_overnight.length > 0 && (
          <div className="vd-briefing-trader-block">
            <div className="vd-briefing-col-sub">Global Overnight</div>
            <div className="vd-briefing-global-strip">
              {t.global_overnight.map((g: BriefingGlobalIndex) => (
                <div key={g.symbol} className="vd-briefing-global-tile">
                  <div className="vd-briefing-global-name">{g.name}</div>
                  <div className={`vd-briefing-eq-chg ${chgClass(g.change_pct)}`}>
                    {signOrDash(g.change_pct)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 3. Cross-asset grid */}
        {t.cross_asset.length > 0 && (
          <div className="vd-briefing-trader-block">
            <div className="vd-briefing-col-sub">Cross-Asset</div>
            <div className="vd-briefing-crossasset-grid">
              {t.cross_asset.map((ca: BriefingCrossAsset) => (
                <div key={ca.symbol} className="vd-briefing-crossasset-tile">
                  <div className="vd-briefing-eq-sym">{ca.symbol}</div>
                  <div className="vd-briefing-eq-full">{ca.name}</div>
                  <div className="vd-briefing-future-level">{fmtOrDash(ca.level, 2)}</div>
                  <div className={`vd-briefing-eq-chg ${chgClass(ca.change_pct)}`}>
                    {signOrDash(ca.change_pct)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 4. Vol carry table */}
        {t.vol_carry.length > 0 && (
          <div className="vd-briefing-trader-block">
            <div className="vd-briefing-col-sub">Vol Carry</div>
            <table className="vd-briefing-carry-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>RV 10D</th>
                  <th>RV 30D</th>
                  <th>IV</th>
                  <th>Signal</th>
                </tr>
              </thead>
              <tbody>
                {t.vol_carry.map((vc: BriefingVolCarry) => (
                  <tr key={vc.symbol}>
                    <td className="vd-briefing-rv-sym">{vc.symbol}</td>
                    <td>{fmtOrDash(vc.rv_10d, 1)}</td>
                    <td>{fmtOrDash(vc.rv_30d, 1)}</td>
                    <td>{fmtOrDash(vc.iv_proxy, 1)}</td>
                    <td>
                      <span className={`vd-briefing-pill ${carrySignalClass(vc.carry_signal)}`}>
                        {vc.carry_signal}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

      </div>
    </details>
  );
}

// ---------------------------------------------------------------------------
// Main BriefingPanel export
// ---------------------------------------------------------------------------

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
          {/* Desk panel skeletons */}
          <div className="vd-briefing-skeleton-desk" />
          <div className="vd-briefing-skeleton-desk" />
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
          <span className="vd-briefing-asof">
            <span className="vd-briefing-asof-dot" aria-hidden="true" />
            Updated {formatAsOf(data.as_of)}
          </span>
        </div>

        {/* Summary */}
        <p className="vd-briefing-summary">{data.summary}</p>

        {/* Trade Themes — full width, above the grid */}
        {data.themes.length > 0 && (
          <div className="vd-briefing-themes-block">
            <div className="vd-briefing-col-sub vd-briefing-themes-head">Trade Themes</div>
            <div className="vd-briefing-themes vd-briefing-themes-multicol">
              {data.themes.map((t) => {
                const parsed = parseTheme(t);
                return (
                  <div key={t} className={`vd-briefing-theme-row vd-theme-${parsed.key}`}>
                    <span className="vd-briefing-theme-cat">{parsed.label}</span>
                    <span className="vd-briefing-theme-body">{parsed.body}</span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

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

          {/* Col 3: Headlines */}
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
          </div>
        </div>

        {/* ------------------------------------------------------------------ */}
        {/* Desk panels — render only when backend has populated them          */}
        {/* ------------------------------------------------------------------ */}
        {data.structurer && (
          <div className="vd-briefing-desk-panels">
            <StructurerPanel s={data.structurer} />
          </div>
        )}
        {data.trader && (
          <div className="vd-briefing-desk-panels">
            <TraderPanel t={data.trader} />
          </div>
        )}

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
