import { useEffect, useMemo, useRef, useState } from "react";
import type { MoverRow, MoversPayload } from "../api/client";
import { useBriefing } from "../hooks/useBriefing";

type Workspace = "pricer" | "copilot" | "esmm";

interface LandingHomeProps {
  movers: MoversPayload | null;
  moversLoading: boolean;
  moversStale: boolean;
  onMoversRefresh?: () => void;
  onPickWorkspace: (mode: Workspace) => void;
  onPickTicker: (ticker: string, price: number) => void;
}

const fmtPct = (n: number | null | undefined, decimals = 2): string => {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const sign = n >= 0 ? "+" : "−";
  return `${sign}${Math.abs(n).toFixed(decimals)}%`;
};

const fmtNum = (n: number | null | undefined, decimals = 2): string => {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
};

function isMarketOpen(now: Date): boolean {
  const utc = now.getTime() + now.getTimezoneOffset() * 60_000;
  const et = new Date(utc - 4 * 60 * 60_000);
  const day = et.getUTCDay();
  if (day === 0 || day === 6) return false;
  const minutes = et.getUTCHours() * 60 + et.getUTCMinutes();
  return minutes >= 9 * 60 + 30 && minutes < 16 * 60;
}

function Sparkline({
  points,
  up,
  width = 64,
  height = 22,
}: {
  points: number[];
  up: boolean;
  width?: number;
  height?: number;
}) {
  if (!points || points.length < 2) {
    return <svg className="argo-mkt-spark" width={width} height={height} />;
  }
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const step = width / (points.length - 1);
  const polyline = points
    .map((p, i) => {
      const x = i * step;
      const y = height - 3 - ((p - min) / range) * (height - 6);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const stroke = up ? "var(--argo-up)" : "var(--argo-down)";
  return (
    <svg
      className="argo-mkt-spark"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      aria-hidden
    >
      <polyline fill="none" stroke={stroke} strokeWidth={1.4} points={polyline} />
    </svg>
  );
}

function tickerLabel(raw: string): string {
  // Tighten yfinance-style symbols ("^GSPC" → "SPX", "^IXIC" → "NDX").
  const map: Record<string, string> = {
    "^GSPC": "SPX",
    "^IXIC": "NDX",
    "^DJI": "DJI",
    "^RUT": "RTY",
    "^VIX": "VIX",
  };
  return map[raw] ?? raw.replace("^", "");
}

// ---------------------------------------------------------------------------
// Top bar — brand, live ticker tape, market pill, clock
// ---------------------------------------------------------------------------

function TopBar({ asOf, source, tickerRows }: { asOf?: string; source?: string; tickerRows: MoverRow[] }) {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  // Compute a negative animation-delay so the ticker tape picks up at the
  // correct phase in its 90s cycle on every mount (including remounts after
  // navigating away and back). Without this the animation always resets to
  // translateX(0), causing a visible snap/jump when returning to home.
  const TICKER_DURATION_S = 90;
  const tickerDelayRef = useRef<string>(
    `-${((Date.now() / 1000) % TICKER_DURATION_S).toFixed(3)}s`
  );

  const open = isMarketOpen(now);
  const clock = now.toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });

  // Render twice to enable seamless CSS marquee scroll.
  const block = (idx: number) => (
    <div className="argo-ticker-block" key={idx}>
      {tickerRows.map((t, i) => {
        const up = t.change_pct >= 0;
        return (
          <div className="argo-tk" key={`${t.ticker}-${idx}-${i}`}>
            <b>{tickerLabel(t.ticker)}</b>
            <span className="argo-tk-p">
              {t.price.toLocaleString("en-US", { maximumFractionDigits: 2 })}
            </span>
            <span className={`argo-tk-c ${up ? "up" : "down"}`}>
              {up ? "▲" : "▼"} {Math.abs(t.change_pct).toFixed(2)}%
            </span>
          </div>
        );
      })}
    </div>
  );

  return (
    <header className="argo-topbar vd-header" data-testid="vd-header">
      <div className="argo-brand">
        <div className="argo-brand-mark" aria-hidden>AP</div>
        <div className="argo-brand-text">
          <div className="argo-brand-name">ArgoPilot</div>
          <div className="argo-brand-tag">Agentic derivatives pricing &amp; structuring</div>
        </div>
      </div>

      <div className="argo-topbar-mid">
        {tickerRows.length > 0 ? (
          <div
            className="argo-ticker-track"
            style={{ animationDelay: tickerDelayRef.current }}
          >
            {block(0)}{block(1)}
          </div>
        ) : (
          <div className="argo-ticker-empty">Loading tape…</div>
        )}
      </div>

      <div className="argo-topbar-r">
        <span className={`argo-pill vd-status-pill ${open ? "open" : "closed"}`}>
          <span className="argo-dot vd-status-dot" />
          {open ? "Market open" : "Market closed"}
        </span>
        {asOf && (
          <span className="argo-asof" title={`Source: ${source ?? "—"}`}>
            as of {new Date(asOf).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })}
          </span>
        )}
        <span className="argo-clock vd-clock">{clock}</span>
        <span className="argo-clock-z">ET</span>
      </div>
    </header>
  );
}

// ---------------------------------------------------------------------------
// Markets strip — six-column snapshot under the top bar
// ---------------------------------------------------------------------------

function MarketsStrip({ indices }: { indices: MoverRow[] }) {
  // Carry up to 6 indices; if we have fewer, pad with sensible defaults from the design.
  const cells = useMemo(() => {
    const provided = indices.slice(0, 6);
    return provided;
  }, [indices]);

  const stamp = new Date().toLocaleString("en-US", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });

  return (
    <section className="argo-markets vd-index-strip" data-testid="vd-index-strip" aria-label="Markets snapshot">
      <div className="argo-markets-label">
        <div className="argo-markets-l1">Snapshot</div>
        <div className="argo-markets-l2">{stamp} ET</div>
      </div>
      {cells.length === 0 ? (
        <div className="argo-markets-empty">Loading market data…</div>
      ) : (
        cells.map((row) => {
          const up = row.change_pct >= 0;
          const label = tickerLabel(row.ticker);
          return (
            <div className="argo-mkt vd-index-card" key={row.ticker}>
              <div className="argo-mkt-sym">{label}</div>
              <div className="argo-mkt-row">
                <span className="argo-mkt-px vd-index-price">
                  ${row.price.toLocaleString("en-US", { maximumFractionDigits: 2 })}
                </span>
                <span className={`argo-mkt-chg vd-index-change ${up ? "up" : "down"}`}>
                  {up ? "▲" : "▼"} {Math.abs(row.change_pct).toFixed(2)}%
                </span>
                <Sparkline points={row.spark} up={up} />
              </div>
            </div>
          );
        })
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Workspaces — three flagship cards (Quick Pricer, Co-pilot, eSMM Lab)
// ---------------------------------------------------------------------------

interface WorkspaceDef {
  mode: Workspace;
  index: string;
  kicker: string;
  title: string;
  description: string;
  tags: string[];
  status: { label: string; tone: "live" | "amber" | "idle" };
  stats: { lbl: string; val: string; delta?: string; tone?: "up" | "down" | "amber" }[];
  runs: { t: string; n: string; v: string }[];
  visual: "spark" | "agents" | "bars";
}

const WORKSPACES: WorkspaceDef[] = [
  {
    mode: "pricer",
    index: "01 / Pricing",
    kicker: "Price one option, fast",
    title: "Quick Pricer",
    description:
      "12 option types — vanilla, barrier (KO / KI), Asian, lookback. Real-time Greeks, smile-aware engines, full HTML report.",
    tags: ["QuantLib", "Vol Surface", "Greeks"],
    status: { label: "Live", tone: "live" },
    stats: [
      { lbl: "Runs today", val: "47", delta: "+12", tone: "up" },
      { lbl: "Avg latency", val: "142", delta: "ms" },
      { lbl: "P99", val: "318", delta: "ms" },
    ],
    runs: [
      { t: "11:19 ET", n: "AAPL 220C · 21Jun", v: "$3.42" },
      { t: "11:14 ET", n: "SPX 5800P · 19Sep", v: "$48.10" },
      { t: "11:08 ET", n: "TSLA 350C KO 400", v: "$6.81" },
    ],
    visual: "spark",
  },
  {
    mode: "copilot",
    index: "02 / Structuring",
    kicker: "Multi-agent OTC workflow",
    title: "Structuring Co-pilot",
    description:
      "Seven-agent flow with HITL gates: intake → strategist → pricing → scenario → validator → narrator. Ships a memo end-to-end.",
    tags: ["7 Agents", "HITL", "OTC Memo"],
    status: { label: "2 awaiting HITL", tone: "amber" },
    stats: [
      { lbl: "In progress", val: "3" },
      { lbl: "Memos this wk", val: "12" },
      { lbl: "HITL queue", val: "2", tone: "amber" },
    ],
    runs: [
      { t: "#A-204", n: "EURUSD digital range", v: "Scenario" },
      { t: "#A-203", n: "SPX autocallable 18m", v: "HITL" },
      { t: "#A-201", n: "XAU TARN 24m", v: "HITL" },
    ],
    visual: "agents",
  },
  {
    mode: "esmm",
    index: "03 / Market-making",
    kicker: "Equity market-making sandbox",
    title: "eSMM Lab",
    description:
      "Avellaneda-Stoikov quote engine, fill-level backtester, TCA decomposition, and a 3-agent observe → propose → score loop.",
    tags: ["Stoikov", "Backtest", "TCA"],
    status: { label: "Idle", tone: "idle" },
    stats: [
      { lbl: "Last Sharpe", val: "1.84", tone: "up" },
      { lbl: "Inventory σ", val: "0.42" },
      { lbl: "Quote engines", val: "24" },
    ],
    runs: [
      { t: "14 May", n: "SPY · γ=0.1 · κ=1.5", v: "+$3.2k" },
      { t: "12 May", n: "QQQ · γ=0.05", v: "+$1.8k" },
      { t: "11 May", n: "IWM · γ=0.2", v: "−$0.4k" },
    ],
    visual: "bars",
  },
];

function WorkspaceVisual({ kind }: { kind: WorkspaceDef["visual"] }) {
  if (kind === "spark") {
    return (
      <svg className="argo-ws-spark" viewBox="0 0 280 36" preserveAspectRatio="none">
        <defs>
          <linearGradient id="argo-ws-spark-grad" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="var(--argo-up)" stopOpacity="0.25" />
            <stop offset="100%" stopColor="var(--argo-up)" stopOpacity="0" />
          </linearGradient>
        </defs>
        <polyline
          fill="none"
          stroke="var(--argo-up)"
          strokeWidth="1.4"
          points="0,28 14,26 28,24 42,22 56,25 70,20 84,18 98,21 112,16 126,18 140,12 154,14 168,10 182,11 196,8 210,9 224,6 238,8 252,5 266,7 280,3"
        />
        <polygon
          fill="url(#argo-ws-spark-grad)"
          points="0,28 14,26 28,24 42,22 56,25 70,20 84,18 98,21 112,16 126,18 140,12 154,14 168,10 182,11 196,8 210,9 224,6 238,8 252,5 266,7 280,3 280,36 0,36"
        />
      </svg>
    );
  }
  if (kind === "agents") {
    const bars = [
      { tone: "up" },
      { tone: "up" },
      { tone: "up" },
      { tone: "amber-pulse" },
      { tone: "muted" },
      { tone: "muted" },
      { tone: "muted" },
    ];
    return (
      <div className="argo-agent-bar">
        <div className="argo-agent-bar-track">
          {bars.map((b, i) => (
            <div key={i} className={`argo-agent-step argo-agent-step--${b.tone}`} />
          ))}
        </div>
        <span className="argo-agent-bar-meta">4 / 7</span>
      </div>
    );
  }
  const heights = [42, 58, 38, 22, 62, 78, 52, 30, 48, 68, 74, 88, 34, 54, 70, 82, 62, 90];
  return (
    <div className="argo-pnl-bars">
      {heights.map((h, i) => {
        const down = h < 36;
        return (
          <div
            key={i}
            className={`argo-pnl-bar ${down ? "down" : "up"}`}
            style={{ height: `${h}%` }}
          />
        );
      })}
    </div>
  );
}

function WorkspacesGrid({ onPickWorkspace }: { onPickWorkspace: (m: Workspace) => void }) {
  return (
    <section className="argo-section">
      <div className="argo-section-eyebrow"><span className="argo-eyebrow-n">§ 01</span><span>Workspaces</span></div>
      <div className="argo-section-head">
        <div>
          <h2 className="argo-section-h">Pick a workspace to begin.</h2>
          <div className="argo-section-sub">Derivatives pricing, structuring, and equity market-making — three pipelines, one cockpit.</div>
        </div>
        <div className="argo-section-right">
          <span className="argo-crumb">3 active</span>
          <span>·</span>
          <span className="argo-crumb">62 runs today</span>
          <span>·</span>
          <span className="argo-kbd">⌘ K</span>
          <span style={{ marginLeft: 4 }}>to search</span>
        </div>
      </div>

      <div className="argo-workspaces">
        {WORKSPACES.map((ws) => (
          <button
            key={ws.mode}
            type="button"
            className="argo-ws"
            onClick={() => onPickWorkspace(ws.mode)}
            aria-label={`Open ${ws.title}`}
          >
            <div className="argo-ws-head">
              <span className="argo-ws-num">{ws.index}</span>
              <span className={`argo-ws-status argo-ws-status--${ws.status.tone}`}>
                <span className="argo-dot" />
                {ws.status.label}
              </span>
            </div>
            <div className="argo-ws-body">
              <div className="argo-ws-kicker">{ws.kicker}</div>
              <h3 className="argo-ws-title">{ws.title}</h3>
              <p className="argo-ws-desc">{ws.description}</p>

              <div className="argo-ws-stats">
                {ws.stats.map((s) => (
                  <div className="argo-ws-stat" key={s.lbl}>
                    <div className="argo-ws-stat-lbl">{s.lbl}</div>
                    <div className={`argo-ws-stat-val${s.tone ? ` argo-ws-stat-val--${s.tone}` : ""}`}>
                      {s.val}
                      {s.delta && <span className={`argo-ws-stat-delta${s.tone === "up" ? " up" : ""}`}>{s.delta}</span>}
                    </div>
                  </div>
                ))}
              </div>

              <WorkspaceVisual kind={ws.visual} />

              <div className="argo-ws-runs">
                <div className="argo-ws-runs-h">
                  {ws.mode === "pricer" ? "Recent pricings" : ws.mode === "copilot" ? "Active memos" : "Last backtest"}
                </div>
                {ws.runs.map((r, i) => (
                  <div className="argo-ws-runs-r" key={i}>
                    <span className="argo-ws-runs-t">{r.t}</span>
                    <span className="argo-ws-runs-n">{r.n}</span>
                    <span className="argo-ws-runs-v">{r.v}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="argo-ws-foot">
              <div className="argo-ws-tags">
                {ws.tags.map((t) => (
                  <span key={t} className="argo-tag">{t}</span>
                ))}
              </div>
              <span className="argo-ws-cta">
                Open <span className="argo-kbd argo-kbd-soft">↵</span>
              </span>
            </div>
          </button>
        ))}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Intelligence row — Structurer's briefing + Vol regime
// ---------------------------------------------------------------------------

function BriefingCard({
  data,
  isLoading,
  error,
  onRetry,
  isStale,
  asOf,
}: {
  data: ReturnType<typeof useBriefing>["data"];
  isLoading: boolean;
  error: string | null;
  onRetry: () => void;
  isStale: boolean;
  asOf: string | null;
}) {
  const formattedAsOf = useMemo(() => {
    if (!data) return "—";
    try {
      return new Date(data.as_of).toLocaleTimeString("en-US", {
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "America/New_York",
      });
    } catch {
      return data.as_of;
    }
  }, [data]);

  if (isLoading && !data) {
    return (
      <div className="argo-card argo-briefing" aria-busy="true">
        <div className="argo-card-head">
          <h3>Structurer's briefing</h3>
          <span className="argo-card-meta argo-skel argo-skel-text" style={{ width: 120 }} />
        </div>
        <div className="argo-skel argo-skel-lede" />
        <div className="argo-skel argo-skel-lede" style={{ width: "82%" }} />
        <div className="argo-skel argo-skel-body" />
        <div className="argo-skel argo-skel-body" style={{ width: "94%" }} />
        <div className="argo-skel argo-skel-body" style={{ width: "76%" }} />
        <div className="argo-briefing-skel-bullets">
          {[0, 1, 2, 3].map((i) => (
            <div className="argo-briefing-skel-bullet" key={i}>
              <span className="argo-skel argo-skel-pill" />
              <span className="argo-skel argo-skel-text" />
              <span className="argo-skel argo-skel-pill" style={{ width: 64 }} />
            </div>
          ))}
        </div>
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="argo-card argo-briefing argo-briefing-empty">
        <div className="argo-card-head">
          <h3>Structurer's briefing</h3>
          <span className="argo-card-meta">Awaiting refresh</span>
        </div>
        <div className="argo-empty-state">
          <svg
            className="argo-empty-glyph"
            viewBox="0 0 56 56"
            fill="none"
            aria-hidden
          >
            <rect x="10" y="10" width="36" height="36" rx="6" stroke="currentColor" strokeWidth="1.4" opacity="0.45" />
            <path d="M18 22h14M18 30h20M18 38h10" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" opacity="0.7" />
            <circle cx="40" cy="38" r="6" stroke="currentColor" strokeWidth="1.4" fill="var(--argo-surface)" />
            <path d="M40 36v2.5l1.6 1.6" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
          <div className="argo-empty-h">No briefing yet</div>
          <div className="argo-empty-sub">
            {error
              ? "We couldn't reach the briefing service. Try again, or check back at 7:30 AM ET when the next memo is generated."
              : "Today's structurer's memo hasn't been generated yet — it runs at 7:30 AM ET each session."}
          </div>
          <button type="button" className="argo-empty-cta" onClick={onRetry}>
            <span className="argo-empty-cta-arrow" aria-hidden>↻</span>
            Try again
          </button>
        </div>
      </div>
    );
  }

  const summarySentences = data.summary.split(/(?<=\.)\s+/);
  const lede = summarySentences[0] ?? data.summary;
  const body = summarySentences.slice(1).join(" ");
  const bullets = data.themes.slice(0, 4).map((t, i) => {
    const colonIdx = t.indexOf(":");
    const dashIdx = t.indexOf("—");
    const splitIdx =
      colonIdx !== -1 && (dashIdx === -1 || colonIdx < dashIdx)
        ? colonIdx
        : dashIdx;
    if (splitIdx === -1) {
      return { tag: "Theme", body: t, sentimentUp: i % 2 === 0 };
    }
    return {
      tag: t.slice(0, splitIdx).trim() || "Theme",
      body: t.slice(splitIdx + 1).trim(),
      sentimentUp: i % 2 === 0,
    };
  });

  return (
    <div className="argo-card argo-briefing">
      {isStale && asOf && (
        <div
          className="briefing-stale-banner"
          style={{ color: "#c08400", fontSize: "0.78rem", fontWeight: 600 }}
        >
          STALE — snapshot taken {new Date(asOf).toLocaleString("en-US", {
            month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
            timeZone: "America/New_York",
          })} ET
        </div>
      )}
      <div className="argo-card-head">
        <h3>
          Structurer's briefing <span className="argo-card-mono">— {formattedAsOf} ET</span>
        </h3>
        <span className="argo-card-meta">By <strong>Argo-04</strong> · 5 min read</span>
      </div>
      <p className="argo-briefing-lede">{lede}</p>
      {body && <p className="argo-briefing-body">{body}</p>}
      {bullets.length > 0 && (
        <ul className="argo-briefing-bullets">
          {bullets.map((b, i) => (
            <li key={i}>
              <span className="argo-briefing-bullet-t">{b.tag}</span>
              <span className="argo-briefing-bullet-x">{b.body}</span>
              <span className={`argo-briefing-bullet-edge ${b.sentimentUp ? "up" : "down"}`}>
                {b.sentimentUp ? "edge" : "watch"}
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="argo-briefing-foot">
        <span>{data.title}</span>
        <span>Read full memo →</span>
      </div>
    </div>
  );
}

function VolRegimeCard({
  data,
  isLoading,
  moversVix,
}: {
  data: ReturnType<typeof useBriefing>["data"];
  isLoading: boolean;
  moversVix: number | null;
}) {
  const structurer = data?.structurer;
  const vix = data?.equity.vol.find((v) => v.symbol.toUpperCase().includes("VIX"));

  if (isLoading && !data) {
    return (
      <div className="argo-card" aria-busy="true">
        <div className="argo-card-head">
          <h3>Vol regime</h3>
          <span className="argo-card-meta argo-skel argo-skel-text" style={{ width: 100 }} />
        </div>
        <div className="argo-skel argo-skel-regime" />
        <div className="argo-vol-grid argo-vol-grid-skel">
          {["VIX (spot)", "VVIX", "MOVE", "SKEW"].map((lbl) => (
            <div className="argo-vol-cell" key={lbl}>
              <div className="argo-vol-cell-l">{lbl}</div>
              <div className="argo-skel argo-skel-num" />
              <div className="argo-skel argo-skel-text" style={{ width: "55%", marginTop: 6 }} />
            </div>
          ))}
        </div>
        <div className="argo-term-label">VIX term structure</div>
        <div className="argo-skel argo-skel-chart" />
      </div>
    );
  }

  if (!data) {
    return (
      <div className="argo-card argo-vol-empty">
        <div className="argo-card-head">
          <h3>Vol regime</h3>
          <span className="argo-card-meta">Awaiting refresh</span>
        </div>
        <div className="argo-empty-state">
          <svg className="argo-empty-glyph" viewBox="0 0 56 56" fill="none" aria-hidden>
            <path d="M8 40 L20 30 L28 36 L40 22 L50 28" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" opacity="0.7" />
            <circle cx="20" cy="30" r="3" fill="var(--argo-surface)" stroke="currentColor" strokeWidth="1.4" />
            <circle cx="28" cy="36" r="3" fill="var(--argo-surface)" stroke="currentColor" strokeWidth="1.4" />
            <circle cx="40" cy="22" r="3" fill="var(--argo-surface)" stroke="currentColor" strokeWidth="1.4" />
            <line x1="6" y1="46" x2="52" y2="46" stroke="currentColor" strokeWidth="1.2" opacity="0.45" />
          </svg>
          <div className="argo-empty-h">Vol surface offline</div>
          <div className="argo-empty-sub">
            VIX, VVIX, MOVE and term-structure feeds are unreachable. Retry in a moment, or check your yfinance / FRED keys.
          </div>
        </div>
      </div>
    );
  }

  const cells = [
    {
      lbl: "VIX (spot)",
      val: fmtNum(moversVix !== null ? moversVix : (vix?.level ?? null), 2),
      chg: fmtPct(vix?.change_pct ?? null, 2),
      chgUp: (vix?.change_pct ?? 0) >= 0,
    },
    {
      lbl: "VVIX",
      val: fmtNum(structurer?.vvix.level ?? null, 1),
      chg: fmtPct(structurer?.vvix.change_pct ?? null, 2),
      chgUp: (structurer?.vvix.change_pct ?? 0) >= 0,
    },
    {
      lbl: "MOVE",
      val: fmtNum(structurer?.move.level ?? null, 1),
      chg: fmtPct(structurer?.move.change_pct ?? null, 2),
      chgUp: (structurer?.move.change_pct ?? 0) >= 0,
    },
    {
      lbl: "SKEW",
      val: fmtNum(structurer?.skew_index.level ?? null, 1),
      chg: fmtPct(structurer?.skew_index.change_pct ?? null, 2),
      chgUp: (structurer?.skew_index.change_pct ?? 0) >= 0,
    },
  ];

  const regime = vix?.regime ?? "normal";
  const regimeTone =
    regime === "stressed" ? "red" : regime === "elevated" ? "amber" : "green";
  const regimeLabel =
    regime === "low"
      ? "Low-vol bid"
      : regime === "normal"
      ? "Calm tape"
      : regime === "elevated"
      ? "Elevated"
      : "Stressed";

  // VIX term structure — render up to 7 plotted points.
  const term = structurer?.vol_term_structure ?? [];
  const termPoints = term
    .filter((t) => t.level !== null && t.level !== undefined)
    .slice(0, 7);
  const hasTerm = termPoints.length >= 2;
  const w = 320, h = 80;
  let polyline = "0,58 53,46 106,38 160,30 213,24 266,20 320,18";
  let footTenors: { tenor: string; lvl: string }[] = [
    { tenor: "1M", lvl: "—" },
    { tenor: "2M", lvl: "—" },
    { tenor: "3M", lvl: "—" },
    { tenor: "6M", lvl: "—" },
    { tenor: "9M", lvl: "—" },
    { tenor: "12M", lvl: "—" },
  ];
  if (hasTerm) {
    const levels = termPoints.map((t) => t.level as number);
    const min = Math.min(...levels);
    const max = Math.max(...levels);
    const range = max - min || 1;
    const step = w / (termPoints.length - 1);
    polyline = termPoints
      .map((t, i) => {
        const x = i * step;
        const y = h - 22 - (((t.level as number) - min) / range) * (h - 36);
        return `${x.toFixed(0)},${y.toFixed(0)}`;
      })
      .join(" ");
    footTenors = termPoints.map((t) => ({
      tenor: t.tenor,
      lvl: fmtNum(t.level, 1),
    }));
  }
  const pointCoords = polyline
    .split(" ")
    .map((p) => p.split(",").map(Number) as [number, number]);

  return (
    <div className="argo-card">
      <div className="argo-card-head">
        <h3>Vol regime</h3>
        <span className="argo-card-meta">
          Last update {data ? new Date(data.as_of).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" }) : "—"}
        </span>
      </div>

      <div className="argo-regime-row">
        <span className={`argo-regime-pill argo-regime-pill--${regimeTone}`}>{regimeLabel}</span>
        <span className="argo-regime-txt">
          Regime <strong>{regime}</strong> — equities {regime === "low" || regime === "normal" ? "calm" : "twitchy"}.
          {structurer?.term_structure_slope?.context ? ` ${structurer.term_structure_slope.context}` : ""}
        </span>
      </div>

      <div className="argo-vol-grid">
        {cells.map((c) => (
          <div className="argo-vol-cell" key={c.lbl}>
            <div className="argo-vol-cell-l">{c.lbl}</div>
            <div className="argo-vol-cell-v">{c.val}</div>
            <div className={`argo-vol-cell-d ${c.chgUp ? "up" : "down"}`}>{c.chg}</div>
          </div>
        ))}
      </div>

      <div className="argo-term-label">VIX term structure</div>
      {hasTerm ? (
        <>
          <svg className="argo-term-chart" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
            <line x1="0" y1={h - 1} x2={w} y2={h - 1} stroke="var(--argo-rule)" strokeWidth="1" />
            <line x1="0" y1={h * 0.5} x2={w} y2={h * 0.5} stroke="var(--argo-rule-2)" strokeWidth="1" strokeDasharray="2 3" />
            <line x1="0" y1={h * 0.75} x2={w} y2={h * 0.75} stroke="var(--argo-rule-2)" strokeWidth="1" strokeDasharray="2 3" />
            <line x1="0" y1={h * 0.25} x2={w} y2={h * 0.25} stroke="var(--argo-rule-2)" strokeWidth="1" strokeDasharray="2 3" />
            <polyline fill="none" stroke="var(--argo-ink)" strokeWidth="1.8" points={polyline} />
            {pointCoords.map(([x, y], i) => (
              <circle key={i} cx={x} cy={y} r={3} fill="var(--argo-ink)" />
            ))}
          </svg>
          <div className="argo-term-foot">
            {footTenors.map((t) => (
              <span key={t.tenor}>{t.tenor} · {t.lvl}</span>
            ))}
          </div>
        </>
      ) : (
        <div className="argo-term-empty">
          <div className="argo-term-empty-glyph" aria-hidden />
          <span>Term structure unavailable</span>
        </div>
      )}
    </div>
  );
}

function IntelligenceSection({ movers }: { movers: MoversPayload | null }) {
  const briefing = useBriefing();
  const moversVix = movers?.indices?.find(
    (r) => r.ticker === "^VIX" || r.ticker === "VIX"
  )?.price ?? null;
  const liveTone = briefing.error
    ? "argo-pill-stale"
    : briefing.isLoading && !briefing.data
    ? "argo-pill-loading"
    : briefing.isStale
    ? "argo-pill-stale"
    : "argo-pill-live";
  const liveLabel = briefing.error
    ? "Stale · click to retry"
    : briefing.isLoading && !briefing.data
    ? "Loading…"
    : briefing.isStale
    ? "STALE"
    : "Live";
  return (
    <section className="argo-section">
      <div className="argo-section-eyebrow"><span className="argo-eyebrow-n">§ 02</span><span>Market intelligence</span></div>
      <div className="argo-section-head">
        <div>
          <h2 className="argo-section-h">Today's tape, in one glance.</h2>
          <div className="argo-section-sub">Structurer's briefing, vol regime, and movers — refreshed every 5 minutes.</div>
        </div>
        <div className="argo-section-right">
          <button
            type="button"
            className={`argo-pill ${liveTone}`}
            onClick={briefing.refresh}
            aria-label="Refresh market intelligence"
            disabled={briefing.isLoading}
          >
            <span className="argo-dot" />
            {liveLabel}
          </button>
        </div>
      </div>

      <div className="argo-intel">
        <BriefingCard
          data={briefing.data}
          isLoading={briefing.isLoading}
          error={briefing.error}
          onRetry={briefing.refresh}
          isStale={briefing.isStale}
          asOf={briefing.asOf}
        />
        <VolRegimeCard data={briefing.data} isLoading={briefing.isLoading} moversVix={moversVix} />
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Movers — Top gainers, losers, most volatile
// ---------------------------------------------------------------------------

interface MoverCardProps {
  title: string;
  variant: "gain" | "lose" | "vol";
  rows: MoverRow[];
  isLoading: boolean;
  onPick: (ticker: string, price: number) => void;
  onRetry?: () => void;
}

function MoverCard({ title, variant, rows, isLoading, onPick, onRetry }: MoverCardProps) {
  const tone =
    variant === "gain" ? "var(--argo-up)" : variant === "lose" ? "var(--argo-down)" : "var(--argo-amber)";
  const subtitle = variant === "vol" ? "30d RV ann." : "Daily Δ%";
  const showSkel = isLoading && rows.length === 0;
  const showEmpty = !isLoading && rows.length === 0;
  return (
    <div className={`argo-mv-card vd-mover-col vd-mover-col--${variant}`}>
      <div className="argo-mv-head">
        <h4 style={{ color: tone }}>
          <span className="argo-mv-dot" style={{ background: tone }} />
          {title}
        </h4>
        <span className="argo-mv-meta">{subtitle}</span>
      </div>
      {showSkel ? (
        Array.from({ length: 5 }).map((_, i) => (
          <div className="argo-mv-row argo-mv-row-skel" key={`skel-${i}`} aria-hidden>
            <span className="argo-skel argo-skel-sym" />
            <span />
            <span className="argo-skel argo-skel-num argo-skel-num-sm" />
            <span className="argo-skel argo-skel-pill" style={{ marginLeft: "auto" }} />
          </div>
        ))
      ) : showEmpty ? (
        <div className="argo-mv-empty-state">
          <svg className="argo-empty-glyph argo-empty-glyph-sm" viewBox="0 0 56 56" fill="none" aria-hidden>
            <rect x="10" y="14" width="36" height="28" rx="5" stroke="currentColor" strokeWidth="1.4" opacity="0.45" />
            <path d="M16 22h24M16 28h18M16 34h22" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" opacity="0.55" />
          </svg>
          <div className="argo-empty-h argo-empty-h-sm">Tape unavailable</div>
          <div className="argo-empty-sub argo-empty-sub-sm">
            Data feed is offline or rate-limited.
          </div>
          {onRetry && (
            <button type="button" className="argo-empty-cta argo-empty-cta-sm" onClick={onRetry}>
              <span className="argo-empty-cta-arrow" aria-hidden>↻</span>
              Refresh
            </button>
          )}
        </div>
      ) : (
        rows.slice(0, 5).map((r) => {
          const up = r.change_pct >= 0;
          const metric =
            variant === "vol"
              ? r.hv30 !== null && r.hv30 !== undefined
                ? `${(r.hv30 * 100).toFixed(1)}%`
                : "—"
              : fmtPct(r.change_pct, 2);
          const metricCls =
            variant === "vol" ? "argo-mv-amber" : up ? "up" : "down";
          return (
            <button
              type="button"
              key={r.ticker}
              className="argo-mv-row vd-mover-row"
              onClick={() => onPick(r.ticker, r.price)}
              title={`Click to load ${r.ticker} into the pricer`}
            >
              <span className="argo-mv-sym vd-mover-ticker">{r.ticker.replace("^", "")}</span>
              <span className="argo-mv-name" />
              <span className="argo-mv-px">
                {r.price.toLocaleString("en-US", { maximumFractionDigits: 2 })}
              </span>
              <span className={`argo-mv-chg ${metricCls}`}>{metric}</span>
            </button>
          );
        })
      )}
      <div className="argo-mv-foot">
        <span>Click a row to price</span>
        <span className="argo-mv-foot-link">View all →</span>
      </div>
    </div>
  );
}

function MoversRow({
  gainers,
  losers,
  volatile,
  isLoading,
  onPickTicker,
  onRetry,
}: {
  gainers: MoverRow[];
  losers: MoverRow[];
  volatile: MoverRow[];
  isLoading: boolean;
  onPickTicker: (ticker: string, price: number) => void;
  onRetry?: () => void;
}) {
  return (
    <div className="argo-movers vd-movers-grid" data-testid="vd-movers-grid">
      <MoverCard title="Top gainers" variant="gain" rows={gainers} isLoading={isLoading} onPick={onPickTicker} onRetry={onRetry} />
      <MoverCard title="Top losers" variant="lose" rows={losers} isLoading={isLoading} onPick={onPickTicker} onRetry={onRetry} />
      <MoverCard title="Most volatile" variant="vol" rows={volatile} isLoading={isLoading} onPick={onPickTicker} onRetry={onRetry} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Recent work — desk activity row (placeholder until backend endpoint lands)
// ---------------------------------------------------------------------------

const RECENT_DEMO = [
  { t: "11:19", ws: "Quick Pricer", color: "var(--argo-accent)", name: "AAPL 220C · 21 Jun 25 · vanilla", sym: "AAPL", val: "$3.42 · IV 22.4%", status: "Done", tone: "ok", action: "Open →" },
  { t: "11:14", ws: "Structuring", color: "#7a5af8", name: "#A-204 · EURUSD digital range 3m", sym: "EURUSD", val: "Notional €25m", status: "Scenario", tone: "run", action: "Resume →" },
  { t: "10:58", ws: "Quick Pricer", color: "var(--argo-accent)", name: "SPX 5800P · 19 Sep 25 · vanilla", sym: "SPX", val: "$48.10 · IV 16.8%", status: "Done", tone: "ok", action: "Open →" },
  { t: "10:42", ws: "Structuring", color: "#7a5af8", name: "#A-203 · SPX autocallable 18m · KI 65%", sym: "SPX", val: "Coupon 9.4%", status: "Awaiting HITL", tone: "wait", action: "Review →" },
  { t: "10:31", ws: "eSMM Lab", color: "#e8851a", name: "SPY · γ=0.1 · κ=1.5 · 1d backtest", sym: "SPY", val: "Sharpe 1.84", status: "Done", tone: "ok", action: "Open →" },
  { t: "10:08", ws: "Quick Pricer", color: "var(--argo-accent)", name: "TSLA 350C · KO 400 · 19 Dec 25", sym: "TSLA", val: "$6.81 · IV 54.2%", status: "Done", tone: "ok", action: "Open →" },
  { t: "09:47", ws: "Structuring", color: "#7a5af8", name: "#A-201 · XAU TARN 24m · 6 fixings", sym: "XAU", val: "Notional $40m", status: "Awaiting HITL", tone: "wait", action: "Review →" },
];

function RecentWork() {
  return (
    <section className="argo-section">
      <div className="argo-section-eyebrow"><span className="argo-eyebrow-n">§ 03</span><span>Recent work</span></div>
      <div className="argo-section-head">
        <div>
          <h2 className="argo-section-h">Your desk, last 24 hours.</h2>
          <div className="argo-section-sub">Open pricings, in-flight memos, and the last few backtests.</div>
        </div>
        <div className="argo-section-right">
          <span className="argo-crumb">Filter:</span>
          <span className="argo-tag argo-tag-soft">All workspaces</span>
          <span className="argo-tag argo-tag-soft">Today</span>
        </div>
      </div>
      <div className="argo-recent">
        <div className="argo-rec-head">
          <div>Time</div><div>Workspace</div><div>Item</div><div>Underlying</div><div>Value</div><div>Status</div><div />
        </div>
        {RECENT_DEMO.map((row, i) => (
          <div className="argo-rec-row" key={i}>
            <span className="argo-rec-time">{row.t}</span>
            <span className="argo-rec-ws">
              <span className="argo-rec-sq" style={{ background: row.color }} />
              {row.ws}
            </span>
            <span className="argo-rec-name">{row.name}</span>
            <span className="argo-rec-sym">{row.sym}</span>
            <span className="argo-rec-val">{row.val}</span>
            <span>
              <span className={`argo-rec-status argo-rec-status--${row.tone}`}>{row.status}</span>
            </span>
            <span className="argo-rec-action">{row.action}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// LandingHome — assembled
// ---------------------------------------------------------------------------

export function LandingHome({
  movers,
  moversLoading,
  moversStale,
  onMoversRefresh,
  onPickWorkspace,
  onPickTicker,
}: LandingHomeProps) {
  // Build a richer ticker stream by concatenating indices and top movers.
  const indices = movers?.indices ?? [];
  const tickerRows: MoverRow[] = useMemo(() => {
    const merged: MoverRow[] = [];
    const seen = new Set<string>();
    for (const r of [
      ...indices,
      ...(movers?.gainers ?? []),
      ...(movers?.volatile ?? []),
      ...(movers?.losers ?? []),
    ]) {
      if (!seen.has(r.ticker)) {
        seen.add(r.ticker);
        merged.push(r);
      }
    }
    return merged.slice(0, 18);
  }, [movers]);

  return (
    <div className="argo-page" data-screen="01-landing">
      <TopBar asOf={movers?.as_of} source={movers?.source} tickerRows={tickerRows} />
      <MarketsStrip indices={indices} />

      <WorkspacesGrid onPickWorkspace={onPickWorkspace} />
      <IntelligenceSection movers={movers} />

      <section className="argo-section">
        <div className="argo-section-eyebrow"><span className="argo-eyebrow-n">§ 02b</span><span>Movers</span></div>
        <div className="argo-section-head">
          <div>
            <h2 className="argo-section-h">Movers — click any row to price.</h2>
            <div className="argo-section-sub">
              Top gainers, losers, and most volatile names from the cash tape.
              {moversStale && <span className="argo-section-stale"> Showing last cached snapshot.</span>}
            </div>
          </div>
        </div>
        <MoversRow
          gainers={movers?.gainers ?? []}
          losers={movers?.losers ?? []}
          volatile={movers?.volatile ?? []}
          isLoading={moversLoading}
          onPickTicker={onPickTicker}
          onRetry={onMoversRefresh}
        />
      </section>

      <RecentWork />

      <footer className="argo-footer">
        <div>Built for traders &amp; quants · v3.2.0</div>
        <div className="argo-footer-meta">Powered by QuantLib · Monte Carlo · Avellaneda-Stoikov</div>
        <div className="argo-footer-right">All times ET · Data delayed 15m unless authenticated</div>
      </footer>
    </div>
  );
}
