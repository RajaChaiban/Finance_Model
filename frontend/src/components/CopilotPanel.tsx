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
  const pollRef = useRef<number | null>(null);

  const stopPolling = () => {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  useEffect(() => {
    return stopPolling;
  }, []);

  const startPolling = (id: string) => {
    stopPolling();
    pollRef.current = window.setInterval(async () => {
      try {
        const s = await agentClient.getSession(id);
        setSession(s);
        if (TERMINAL_STATUSES.has(s.status) || AWAITING_STATUSES.has(s.status)) {
          stopPolling();
        }
      } catch (e: any) {
        setError(e?.message ?? String(e));
        stopPolling();
      }
    }, 1000);
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
        startPolling(session_id);
      }
    } catch (e: any) {
      setError(e?.message ?? String(e));
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
        startPolling(session.session_id);
      } else {
        stopPolling();
      }
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleReset = () => {
    stopPolling();
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

      {error && (
        <div className="copilot-error">
          <strong>Error:</strong> {error}
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
  return (
    <div className="memo-view">
      <h4>{memo.title}</h4>
      <p>{memo.objective_restatement}</p>
      <pre className="memo-comparison">{memo.comparison_table_md}</pre>
      <div className="memo-recommendation">
        <strong>Recommendation:</strong>
        <p>{memo.recommendation_md}</p>
      </div>
      {memo.caveats && memo.caveats.length > 0 && (
        <ul className="memo-caveats">
          {memo.caveats.map((c: string, i: number) => (
            <li key={i}>⚠️ {c}</li>
          ))}
        </ul>
      )}
      <details className="memo-termsheets">
        <summary>Term-sheet snippets</summary>
        {memo.term_sheets.map((t: any, i: number) => (
          <pre key={i} className="termsheet">{t.text}</pre>
        ))}
      </details>
      {session.validator?.findings && session.validator.findings.length > 0 && (
        <details className="memo-validator">
          <summary>Validator findings ({session.validator.findings.length})</summary>
          <ul>
            {session.validator.findings.map((f: any, i: number) => (
              <li key={i}>
                <strong>{f.severity}:</strong> {f.message}
                {f.remediation && <em> — {f.remediation}</em>}
              </li>
            ))}
          </ul>
        </details>
      )}
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
