import { useEffect, useState } from "react";

function isMarketOpen(now: Date): boolean {
  // NYSE 09:30–16:00 America/New_York, weekdays.
  const utc = now.getTime() + now.getTimezoneOffset() * 60_000;
  // Approximation: ET = UTC - 4 (DST) or - 5 (standard). Use -4 as default —
  // accuracy here is cosmetic; the badge is informational, not authoritative.
  const et = new Date(utc - 4 * 60 * 60_000);
  const day = et.getUTCDay(); // 0=Sun, 6=Sat
  if (day === 0 || day === 6) return false;
  const minutes = et.getUTCHours() * 60 + et.getUTCMinutes();
  return minutes >= 9 * 60 + 30 && minutes < 16 * 60;
}

interface HeaderProps {
  asOf?: string;
  source?: "api" | "cache";
}

export function Header({ asOf, source }: HeaderProps) {
  const [now, setNow] = useState<Date>(new Date());

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const open = isMarketOpen(now);
  const clock = now.toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });

  return (
    <header className="vd-header" data-testid="vd-header">
      <div className="vd-header-inner">
        <div className="vd-brand">
          <div className="vd-brand-mark" aria-hidden>AP</div>
          <div className="vd-brand-text">
            <span className="vd-brand-name">ArgoPilot</span>
            <span className="vd-brand-tag">Agentic Derivatives Pricing &amp; Structuring</span>
          </div>
        </div>
        <div className="vd-header-meta">
          <span className={`vd-status-pill ${open ? "open" : "closed"}`}>
            <span className="vd-status-dot" /> {open ? "Market Open" : "Market Closed"}
          </span>
          {asOf && (
            <span className="vd-asof" title={`Source: ${source ?? "—"}`}>
              as of {new Date(asOf).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })}
            </span>
          )}
          <span className="vd-clock">{clock} <span className="vd-clock-tz">ET</span></span>
        </div>
      </div>
    </header>
  );
}
