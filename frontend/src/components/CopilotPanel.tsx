import { useEffect, useRef, useState } from "react";
import { agentClient, IntakeForm, SessionView } from "../api/agentClient";

const TERMINAL_STATUSES = new Set(["done", "error", "cancelled"]);
const AWAITING_STATUSES = new Set([
  "awaiting_gate_a",
  "awaiting_gate_b",
  "awaiting_gate_c",
]);

const SAMPLE_RFQ =
  "Asset manager client, $50M long SPY, wants downside protection through year-end (about 8 months), comfortable spending up to 1% of notional, OK with capping upside above 8%.";

type InputMode = "nl" | "form";

const DEFAULT_FORM: IntakeForm = {
  underlying: "SPY",
  notional_usd: 50_000_000,
  view: "bearish",
  horizon_days: 240,
  budget_bps_notional: 100,
  premium_tolerance: "low",
  capped_upside_ok: true,
  barrier_appetite: false,
};

export function CopilotPanel() {
  const [mode, setMode] = useState<InputMode>("nl");
  const [rfq, setRfq] = useState(SAMPLE_RFQ);
  const [form, setForm] = useState<IntakeForm>(DEFAULT_FORM);
  const [session, setSession] = useState<SessionView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // SSE subscription handle. Replaces the previous setInterval polling so
  // state transitions reach the UI within one event-loop tick instead of
  // up to 1s polling latency. The backend stream auto-closes when a gate is
  // hit OR a terminal status is reached, at which point we close locally
  // and re-subscribe after the next gate decision.
  const esRef = useRef<EventSource | null>(null);

  const stopEvents = () => {
    if (esRef.current !== null) {
      esRef.current.close();
      esRef.current = null;
    }
  };

  // Always tear down the SSE subscription on unmount so we don't leak
  // network connections when the user switches away from the Co-pilot tab.
  useEffect(() => {
    return stopEvents;
  }, []);

  const startEvents = (id: string) => {
    stopEvents();
    esRef.current = agentClient.subscribeToEvents(id, async (ev) => {
      // Heartbeats are pure liveness signals — ignore.
      if (ev.type === "heartbeat") return;

      // Server closed the stream (gate or terminal). Refresh once so the UI
      // shows the final pre-gate state, then drop the subscription.
      if (ev.type === "stream_close") {
        try {
          setSession(await agentClient.getSession(id));
        } catch {
          /* swallow — we're tearing down anyway */
        }
        stopEvents();
        return;
      }

      // For every other state-bearing event (agent_started, agent_finished,
      // gate_pending, market_context, error, done, cancelled), refresh the
      // session view from the source of truth. The event payload itself is
      // a notification, not a delta — pulling the full SessionView keeps
      // the rendering logic dead-simple.
      try {
        const s = await agentClient.getSession(id);
        setSession(s);
        if (TERMINAL_STATUSES.has(s.status) || AWAITING_STATUSES.has(s.status)) {
          stopEvents();
        }
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e);
        setError(msg);
        stopEvents();
      }
    });
  };

  const handleStart = async () => {
    setError(null);
    setSession(null);
    setBusy(true);
    try {
      const body =
        mode === "nl"
          ? { intake_nl: rfq }
          : { intake_form: form };
      const { session_id } = await agentClient.startSession(body);
      const s = await agentClient.getSession(session_id);
      setSession(s);
      if (!TERMINAL_STATUSES.has(s.status) && !AWAITING_STATUSES.has(s.status)) {
        startEvents(session_id);
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
    } finally {
      setBusy(false);
    }
  };

  const handleGate = async (gate: "a" | "b" | "c", approved: boolean) => {
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      const next = await agentClient.decideGate(session.session_id, gate, { approved });
      setSession(next);
      if (
        approved &&
        !TERMINAL_STATUSES.has(next.status) &&
        !AWAITING_STATUSES.has(next.status)
      ) {
        // Re-subscribe — the previous SSE connection was closed by the
        // server when it hit the gate the user just decided.
        startEvents(session.session_id);
      } else {
        stopEvents();
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
    } finally {
      setBusy(false);
    }
  };

  const handleReset = () => {
    stopEvents();
    setSession(null);
    setError(null);
  };

  return (
    <div className="copilot-panel">
      <div className="copilot-header">
        <h2>Structuring Co-pilot</h2>
        <p className="copilot-subtitle">
          Junior structurer's bench: paste an RFQ or fill the form. The agent
          proposes three structures, prices them, stress-tests them, validates
          for no-arb / structural sanity, and writes the comparison memo.
        </p>
      </div>

      {!session && (
        <div className="copilot-intake-form">
          <div className="copilot-mode-switch">
            <button
              className={mode === "nl" ? "active" : ""}
              onClick={() => setMode("nl")}
            >
              Natural-language RFQ
            </button>
            <button
              className={mode === "form" ? "active" : ""}
              onClick={() => setMode("form")}
            >
              Structured form
            </button>
          </div>

          {mode === "nl" ? (
            <textarea
              className="copilot-rfq"
              value={rfq}
              onChange={(e) => setRfq(e.target.value)}
              rows={6}
              placeholder="Describe the client situation in your own words…"
            />
          ) : (
            <div className="copilot-form-grid">
              <FormField label="Underlying" value={form.underlying} onChange={(v) => setForm({ ...form, underlying: v })} />
              <FormField label="Notional (USD)" type="number" value={form.notional_usd} onChange={(v) => setForm({ ...form, notional_usd: Number(v) })} />
              <FormField label="View" value={form.view} onChange={(v) => setForm({ ...form, view: v })} />
              <FormField label="Horizon (days)" type="number" value={form.horizon_days} onChange={(v) => setForm({ ...form, horizon_days: Number(v) })} />
              <FormField label="Budget (bps notional)" type="number" value={form.budget_bps_notional} onChange={(v) => setForm({ ...form, budget_bps_notional: Number(v) })} />
              <FormField label="Premium tolerance" value={form.premium_tolerance ?? "low"} onChange={(v) => setForm({ ...form, premium_tolerance: v })} />
              <FormCheckbox label="Capped upside OK" checked={!!form.capped_upside_ok} onChange={(v) => setForm({ ...form, capped_upside_ok: v })} />
              <FormCheckbox label="Barrier appetite" checked={!!form.barrier_appetite} onChange={(v) => setForm({ ...form, barrier_appetite: v })} />
            </div>
          )}

          <button className="copilot-run" onClick={handleStart} disabled={busy}>
            {busy ? "Running…" : "Run"}
          </button>
        </div>
      )}

      {(error || session?.last_error) && (
        <div className="copilot-error" data-testid="copilot-error-banner">
          <strong>Error:</strong> {error || session?.last_error}
          {session?.last_error?.includes("RESOURCE_EXHAUSTED") && (
            <p className="copilot-error-hint">
              The configured LLM provider is out of credits. Restart the backend with{" "}
              <code>LLM_PROVIDER=mock</code> or <code>DEMO_REPLAY=1</code> to keep
              demoing, or switch to <code>LLM_PROVIDER=anthropic</code> /{" "}
              <code>LLM_PROVIDER=openai</code> with the matching API key set.
            </p>
          )}
        </div>
      )}

      {session && (
        <div className="copilot-lanes">
          <Lane
            title="① Intake"
            status={session.status}
            done={!!session.objective}
          >
            {session.objective ? (
              <ObjectiveCard objective={session.objective} regime={session.regime ?? null} />
            ) : (
              <p>Parsing client objective…</p>
            )}
            {session.status === "awaiting_gate_a" && (
              <GateButtons
                onApprove={() => handleGate("a", true)}
                onReject={() => handleGate("a", false)}
                approveLabel="Looks right — propose 3 structures"
                rejectLabel="Stop"
                disabled={busy}
              />
            )}
          </Lane>

          <Lane
            title="② Strategist"
            status={session.status}
            done={session.candidates.length > 0}
          >
            {session.candidates.length === 0 && (
              <p className="copilot-placeholder">Awaiting Gate A.</p>
            )}
            {session.candidates.length > 0 && (
              <div className="copilot-candidates">
                {session.candidates.map((c) => (
                  <CandidateCard
                    key={c.candidate_id}
                    candidate={c}
                    priced={
                      session.priced.find(
                        (p) => p.candidate.candidate_id === c.candidate_id
                      ) || null
                    }
                  />
                ))}
              </div>
            )}
            {session.status === "awaiting_gate_b" && (
              <GateButtons
                onApprove={() => handleGate("b", true)}
                onReject={() => handleGate("b", false)}
                approveLabel="Price + stress-test all three"
                rejectLabel="Reject — start over"
                disabled={busy}
              />
            )}
          </Lane>

          <Lane
            title="③ Memo"
            status={session.status}
            done={!!session.memo}
          >
            {!session.memo && (
              <p className="copilot-placeholder">Awaiting Gate B.</p>
            )}
            {session.memo && (
              <MemoView memo={session.memo} session={session} />
            )}
            {session.status === "awaiting_gate_c" && (
              <GateButtons
                onApprove={() => handleGate("c", true)}
                onReject={() => handleGate("c", false)}
                approveLabel="Approve memo"
                rejectLabel="Reject"
                disabled={busy}
              />
            )}
          </Lane>

          <div className="copilot-footer-bar">
            <span>session: {session.session_id.slice(0, 8)}</span>
            <span>status: {session.status}</span>
            <span>cost: ${session.total_cost_usd.toFixed(4)}</span>
            <span>tokens: in {session.total_tokens_input.toLocaleString()} / out {session.total_tokens_output.toLocaleString()}</span>
            <button className="copilot-reset" onClick={handleReset}>
              New RFQ
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function Lane({
  title,
  done,
  children,
}: {
  title: string;
  status: string;
  done: boolean;
  children: React.ReactNode;
}) {
  return (
    <section className={`copilot-lane ${done ? "done" : ""}`}>
      <h3>{title}</h3>
      <div className="copilot-lane-body">{children}</div>
    </section>
  );
}

function GateButtons({
  onApprove,
  onReject,
  approveLabel,
  rejectLabel,
  disabled,
}: {
  onApprove: () => void;
  onReject: () => void;
  approveLabel: string;
  rejectLabel: string;
  disabled?: boolean;
}) {
  return (
    <div className="copilot-gate">
      <button className="gate-approve" onClick={onApprove} disabled={disabled}>
        {approveLabel}
      </button>
      <button className="gate-reject" onClick={onReject} disabled={disabled}>
        {rejectLabel}
      </button>
    </div>
  );
}

function ObjectiveCard({ objective, regime }: { objective: any; regime: any }) {
  return (
    <div className="objective-card">
      <div className="objective-row"><strong>Underlying:</strong> {objective.underlying}</div>
      <div className="objective-row"><strong>Notional:</strong> ${Number(objective.notional_usd).toLocaleString()}</div>
      <div className="objective-row"><strong>View:</strong> {objective.view}</div>
      <div className="objective-row"><strong>Horizon:</strong> {objective.horizon_days} days</div>
      <div className="objective-row"><strong>Budget:</strong> {objective.budget_bps_notional} bps notional</div>
      <div className="objective-row"><strong>Premium tol.:</strong> {objective.premium_tolerance}</div>
      <div className="objective-row"><strong>Capped upside OK:</strong> {objective.capped_upside_ok ? "yes" : "no"}</div>
      <div className="objective-row"><strong>Barrier OK:</strong> {objective.barrier_appetite ? "yes" : "no"}</div>
      {regime && (
        <div className="objective-regime">
          regime: spot ${regime.spot?.toFixed(2)}, vol regime <em>{regime.vol_regime}</em>
          {regime.realised_vol_30d ? `, σ30 ${(regime.realised_vol_30d * 100).toFixed(1)}%` : ""}
        </div>
      )}
      {objective.clarifications && objective.clarifications.length > 0 && (
        <div className="objective-clarifications">
          <strong>Clarifications I'd ask:</strong>
          <ul>
            {objective.clarifications.map((c: string, i: number) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function CandidateCard({
  candidate,
  priced,
}: {
  candidate: any;
  priced: any;
}) {
  return (
    <div className="candidate-card">
      <h4>{candidate.name}</h4>
      <p className="candidate-rationale">{candidate.rationale}</p>
      <ul className="candidate-legs">
        {candidate.legs.map((l: any, i: number) => (
          <li key={i}>
            {l.option_type} K={l.strike} qty={l.quantity > 0 ? "+" : ""}{l.quantity}
            {l.barrier_level ? ` B=${l.barrier_level}` : ""}
            {" "}({l.expiry_days}d)
          </li>
        ))}
      </ul>
      {priced && (
        <div className="candidate-pricing">
          <div>Premium: ${priced.net_premium.toLocaleString(undefined, { maximumFractionDigits: 0 })} ({priced.net_premium_bps.toFixed(1)} bps)</div>
          <div>Δ {priced.greeks.delta.toFixed(3)} · Γ {priced.greeks.gamma.toFixed(4)} · V {priced.greeks.vega.toFixed(2)}</div>
        </div>
      )}
    </div>
  );
}

function MemoView({ memo, session }: { memo: any; session: SessionView }) {
  const [showReport, setShowReport] = useState(false);

  const verdict = parseVerdict(memo.title);
  const split = parseRecommendation(memo.recommendation_md || "");
  const citationsCount = split.citations.length;
  const dealsCount = split.deals.length;
  const caveatsCount = memo.caveats?.length || 0;

  return (
    <div className="memo-view">
      <div className="memo-header-row">
        <div className="memo-header-text">
          <div className="memo-eyebrow">VERDICT</div>
          <h4 className="memo-verdict-headline">{verdict.recommendation || memo.title}</h4>
          {verdict.justification && (
            <p className="memo-verdict-sub">{verdict.justification}</p>
          )}
        </div>
        <button
          type="button"
          className="memo-report-button"
          onClick={() => setShowReport(true)}
          aria-label="Open full memo report"
        >
          📄 View Full Report
        </button>
      </div>
      <div className="memo-quick-stats">
        <span className="memo-stat-pill">📊 {memo.term_sheets?.length || 0} candidates</span>
        <span className="memo-stat-pill">📚 {citationsCount} MI citations</span>
        <span className="memo-stat-pill">📅 {dealsCount} comparable deals</span>
        <span className="memo-stat-pill memo-stat-pill-warn">⚠️ {caveatsCount} caveats</span>
      </div>

      {showReport && (
        <MemoModal
          memo={memo}
          session={session}
          verdict={verdict}
          split={split}
          onClose={() => setShowReport(false)}
        />
      )}
    </div>
  );
}

// ----- Memo modal: structured full report -----

type ParsedVerdict = { recommendation: string; justification: string };
type Citation = {
  agent: string;
  intent: string;
  confidence: string;
  sources: string[];
  text: string;
};
type Deal = {
  number: string;
  id: string;
  asset: string;
  asOf: string;
  lag: string;
  snippet: string;
};
type SplitRec = {
  recommendation: string;
  citations: Citation[];
  deals: Deal[];
  freshness: string | null;
};

function parseVerdict(title: string): ParsedVerdict {
  if (!title) return { recommendation: "", justification: "" };
  const m = title.match(/VERDICT:\s*(.+?)(?:\n|$)/);
  const text = (m ? m[1] : title).replace(/\*\*/g, "").trim();
  const dash = text.indexOf(" — ");
  if (dash >= 0) {
    return {
      recommendation: text.slice(0, dash).trim(),
      justification: text.slice(dash + 3).trim(),
    };
  }
  return { recommendation: text, justification: "" };
}

function parseRecommendation(rawMd: string): SplitRec {
  const citationsIdx = rawMd.indexOf("### Market Intelligence Citations");
  const dealsIdx = rawMd.indexOf("### Recent Comparable Deals");
  let recommendation = rawMd;
  let citationsSection = "";
  let dealsSection = "";
  if (citationsIdx >= 0 && dealsIdx >= 0) {
    recommendation = rawMd.slice(0, citationsIdx);
    citationsSection = rawMd.slice(citationsIdx, dealsIdx);
    dealsSection = rawMd.slice(dealsIdx);
  } else if (citationsIdx >= 0) {
    recommendation = rawMd.slice(0, citationsIdx);
    citationsSection = rawMd.slice(citationsIdx);
  } else if (dealsIdx >= 0) {
    recommendation = rawMd.slice(0, dealsIdx);
    dealsSection = rawMd.slice(dealsIdx);
  }
  return {
    recommendation: recommendation.replace(/^Recommendation:\s*/i, "").trim(),
    citations: parseCitations(citationsSection),
    deals: parseDeals(dealsSection),
    freshness: extractFreshness(dealsSection),
  };
}

function parseCitations(section: string): Citation[] {
  if (!section) return [];
  const items = section.split(/\n(?=\d+\.\s)/);
  const out: Citation[] = [];
  for (const item of items) {
    const m = item.match(
      /^\d+\.\s+\*\*([^*]+)\*\*\s+\(([^,]+),\s+confidence:\s+(\w+)\)(?:\s*—\s*sources:\s*([^\n]+))?\s*\n?\s*([\s\S]*)$/,
    );
    if (m) {
      const text = m[5].replace(/\s+/g, " ").trim();
      out.push({
        agent: m[1].trim(),
        intent: m[2].trim(),
        confidence: m[3].trim().toLowerCase(),
        sources: (m[4] || "")
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        text: text.length > 600 ? text.slice(0, 600) + "…" : text,
      });
    }
  }
  return out;
}

function parseDeals(section: string): Deal[] {
  if (!section) return [];
  const lines = section.split("\n").filter((l) => l.trim().startsWith("|"));
  if (lines.length < 3) return [];
  const out: Deal[] = [];
  for (const line of lines.slice(2)) {
    const cells = line.split("|").slice(1, -1).map((c) => c.trim());
    if (cells.length < 6) continue;
    if (cells[0] === "…" || /more not shown/.test(cells[1])) continue;
    out.push({
      number: cells[0],
      id: cells[1].replace(/`/g, ""),
      asset: cells[2],
      asOf: cells[3],
      lag: cells[4],
      snippet: cells[5],
    });
  }
  return out;
}

function extractFreshness(section: string): string | null {
  if (!section) return null;
  const m = section.match(/_([^_]*Corpus freshness[^_]*)_/);
  return m ? m[1].trim() : null;
}

function parseMarkdownTable(md: string): {
  headers: string[];
  rows: { cells: string[]; recommended: boolean }[];
} {
  if (!md) return { headers: [], rows: [] };
  const lines = md.split("\n").filter((l) => l.trim().startsWith("|"));
  if (lines.length < 3) return { headers: [], rows: [] };
  const headers = lines[0].split("|").slice(1, -1).map((c) => c.trim());
  const rows = lines.slice(2).map((line) => {
    const cells = line.split("|").slice(1, -1).map((c) => c.trim());
    const recommended = cells[0]?.includes("**>>**") ?? false;
    const cleanCells = cells.map((c) => c.replace(/\*\*>>\*\*\s*/, "").replace(/\*\*/g, ""));
    return { cells: cleanCells, recommended };
  });
  return { headers, rows };
}

function MemoModal({
  memo,
  session,
  verdict,
  split,
  onClose,
}: {
  memo: any;
  session: SessionView;
  verdict: ParsedVerdict;
  split: SplitRec;
  onClose: () => void;
}) {
  // Esc-to-close.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const table = parseMarkdownTable(memo.comparison_table_md || "");

  return (
    <div
      className="memo-modal-overlay"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Structuring memo full report"
    >
      <div className="memo-modal" onClick={(e) => e.stopPropagation()}>
        <button
          type="button"
          className="memo-modal-close"
          onClick={onClose}
          aria-label="Close report"
        >
          ✕
        </button>

        <header className="memo-modal-hero">
          <div className="memo-modal-eyebrow">3-Way Structuring Memo</div>
          <h1 className="memo-modal-verdict">{verdict.recommendation}</h1>
          {verdict.justification && (
            <p className="memo-modal-subtitle">{verdict.justification}</p>
          )}
        </header>

        <section className="memo-modal-section">
          <h2>Client Objective</h2>
          <p className="memo-modal-objective">{memo.objective_restatement}</p>
        </section>

        {table.rows.length > 0 && (
          <section className="memo-modal-section">
            <h2>3-Way Comparison</h2>
            <div className="memo-modal-table-wrap">
              <table className="memo-modal-comparison-table">
                <thead>
                  <tr>
                    {table.headers.map((h, i) => (
                      <th key={i}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {table.rows.map((row, i) => (
                    <tr key={i} className={row.recommended ? "recommended" : ""}>
                      {row.cells.map((cell, j) => (
                        <td key={j}>{cell}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="memo-modal-table-legend">
              Recommended pick highlighted. Validator: OK = clean / WARN = warning / BLOCK = blocker.
            </p>
          </section>
        )}

        {split.recommendation && (
          <section className="memo-modal-section">
            <h2>Analyst Recommendation</h2>
            <p className="memo-modal-recommendation">{split.recommendation}</p>
          </section>
        )}

        {memo.caveats && memo.caveats.length > 0 && (
          <section className="memo-modal-section">
            <h2>Caveats ({memo.caveats.length})</h2>
            <ul className="memo-modal-caveats">
              {memo.caveats.map((c: string, i: number) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          </section>
        )}

        {split.citations.length > 0 && (
          <section className="memo-modal-section">
            <h2>Market Intelligence Citations ({split.citations.length})</h2>
            <div className="memo-modal-citations">
              {split.citations.map((cit, i) => (
                <div className="memo-modal-citation" key={i}>
                  <div className="memo-modal-citation-header">
                    <strong>{i + 1}. {cit.agent}</strong>
                    <span className="memo-modal-chip">{cit.intent}</span>
                    <span
                      className={`memo-modal-confidence memo-modal-confidence-${cit.confidence}`}
                    >
                      {cit.confidence}
                    </span>
                  </div>
                  {cit.sources.length > 0 && (
                    <div className="memo-modal-sources">
                      {cit.sources.map((s) => (
                        <code key={s}>{s}</code>
                      ))}
                    </div>
                  )}
                  <p>{cit.text}</p>
                </div>
              ))}
            </div>
          </section>
        )}

        {split.deals.length > 0 && (
          <section className="memo-modal-section">
            <h2>Recent Comparable Deals</h2>
            {split.freshness && (
              <p className="memo-modal-freshness">{split.freshness}</p>
            )}
            <div className="memo-modal-table-wrap">
              <table className="memo-modal-deals-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Source ID</th>
                    <th>Asset</th>
                    <th>As Of</th>
                    <th>Lag</th>
                    <th>Snippet</th>
                  </tr>
                </thead>
                <tbody>
                  {split.deals.map((d, i) => (
                    <tr key={i}>
                      <td>{d.number}</td>
                      <td><code>{d.id}</code></td>
                      <td>{d.asset}</td>
                      <td>{d.asOf}</td>
                      <td>{d.lag}</td>
                      <td className="memo-modal-snippet-cell">{d.snippet}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {memo.term_sheets && memo.term_sheets.length > 0 && (
          <section className="memo-modal-section">
            <h2>Term Sheets</h2>
            <div className="memo-modal-termsheets">
              {memo.term_sheets.map((t: any, i: number) => (
                <details key={i}>
                  <summary>Term sheet {i + 1} — <code>{t.candidate_id}</code></summary>
                  <pre>{t.text}</pre>
                </details>
              ))}
            </div>
          </section>
        )}

        {session.validator?.findings && session.validator.findings.length > 0 && (
          <section className="memo-modal-section">
            <h2>Validator Findings ({session.validator.findings.length})</h2>
            <ul className="memo-modal-validator-list">
              {session.validator.findings.map((f: any, i: number) => (
                <li key={i} data-severity={f.severity}>
                  <span className="memo-modal-severity">{f.severity}</span>
                  <div>
                    <strong>{f.message}</strong>
                    {f.remediation && <em> — {f.remediation}</em>}
                  </div>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  );
}

function FormField({
  label,
  value,
  onChange,
  type = "text",
}: {
  label: string;
  value: string | number;
  onChange: (v: string) => void;
  type?: string;
}) {
  return (
    <label className="form-field">
      <span>{label}</span>
      <input
        type={type}
        value={value as any}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

function FormCheckbox({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="form-checkbox">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>{label}</span>
    </label>
  );
}
