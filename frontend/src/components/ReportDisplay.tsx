import { PricingResult } from "../types";
import { PayoffChart } from "./PayoffChart";
import { GreeksBar } from "./GreeksBar";
import { SensitivityHeatmap } from "./SensitivityHeatmap";

interface ReportDisplayProps {
  result: PricingResult | null;
  isLoading: boolean;
  onNewScenario: () => void;
  spot?: number;
  strike?: number;
  barrier?: number;
}

export function ReportDisplay({
  result,
  isLoading,
  onNewScenario,
  spot,
  strike,
  barrier,
}: ReportDisplayProps) {
  const downloadHTML = () => {
    if (!result) return;

    const element = document.createElement("a");
    const file = new Blob([result.reportHtml], { type: "text/html" });
    element.href = URL.createObjectURL(file);
    element.download = `report-${result.underlying}-${new Date().toISOString().split("T")[0]}.html`;
    document.body.appendChild(element);
    element.click();
    document.body.removeChild(element);
  };

  const downloadPDF = async () => {
    if (!result) return;

    try {
      // Dynamic import for pdf-lib only when needed
      const { jsPDF } = await import("jspdf");
      const { html2canvas } = await import("html2canvas");

      // Create a temporary div with the report HTML
      const tempDiv = document.createElement("div");
      tempDiv.innerHTML = result.reportHtml;
      tempDiv.style.position = "absolute";
      tempDiv.style.left = "-9999px";
      tempDiv.style.width = "800px";
      document.body.appendChild(tempDiv);

      const canvas = await html2canvas(tempDiv);
      const imgData = canvas.toDataURL("image/png");
      const pdf = new jsPDF({
        orientation: "portrait",
        unit: "mm",
        format: "a4",
      });

      const imgWidth = 210;
      const pageHeight = 297;
      const imgHeight = (canvas.height * imgWidth) / canvas.width;
      let heightLeft = imgHeight;
      let position = 0;

      pdf.addImage(imgData, "PNG", 0, position, imgWidth, imgHeight);
      heightLeft -= pageHeight;

      while (heightLeft >= 0) {
        position = heightLeft - imgHeight;
        pdf.addPage();
        pdf.addImage(imgData, "PNG", 0, position, imgWidth, imgHeight);
        heightLeft -= pageHeight;
      }

      pdf.save(
        `report-${result.underlying}-${new Date().toISOString().split("T")[0]}.pdf`
      );

      document.body.removeChild(tempDiv);
    } catch (error) {
      console.error("PDF generation failed:", error);
      alert(
        "PDF generation failed. Please download the HTML report instead."
      );
    }
  };

  if (isLoading) {
    return (
      <div className="report-display loading">
        <div className="loading-spinner">
          <div className="spinner"></div>
          <p>Running pricing model...</p>
          <p className="text-sm">This may take a few seconds</p>
        </div>
      </div>
    );
  }

  if (!result) {
    return (
      <div className="report-display empty">
        <div className="empty-state">
          <p className="text-lg">📊 Ready to Price</p>
          <p>Configure the parameters on the left and click "Run Pricing" to generate a report.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="report-display">
      <div className="report-header">
        <div className="report-info">
          <h3>
            {result.underlying} - {result.optionType.replace("_", " ")}
          </h3>
          <p className="text-sm">
            Priced on {new Date(result.pricingTimestamp).toLocaleString()}
          </p>
        </div>
        <div className="report-actions">
          <button onClick={downloadHTML} className="btn-secondary">
            📥 Download HTML
          </button>
          <button onClick={downloadPDF} className="btn-secondary">
            📄 Download PDF
          </button>
          <button onClick={onNewScenario} className="btn-primary">
            ✨ New Scenario
          </button>
        </div>
      </div>

      {/* Surface banner. Color-coded by ``surfaceStatus`` so the trader can
          tell at a glance whether the smile is trustworthy. Statuses:
            ok       → purple (default surface theme)
            suspect  → amber  (build succeeded but σ implausible)
            failed / empty_chain → red (fell back to flat-σ with a reason)
          We render the banner whenever sigmaAtm exists OR the user opted in
          but the surface didn't build (so they see the reason). */}
      {(result.sigmaAtm !== undefined && result.sigmaAtm !== null) ||
      (result.surfaceStatus &&
        result.surfaceStatus !== "ok" &&
        result.surfaceStatus !== "skipped") ? (
        (() => {
          const status = result.surfaceStatus ?? "ok";
          const palette =
            status === "suspect"
              ? {
                  bg: "linear-gradient(90deg, rgba(245, 158, 11, 0.14), rgba(217, 119, 6, 0.08))",
                  border: "1px solid rgba(245, 158, 11, 0.55)",
                  shadow: "0 0 24px rgba(245, 158, 11, 0.12)",
                  accent: "#f59e0b",
                  label: "Surface SUSPECT",
                }
              : status === "failed" || status === "empty_chain"
              ? {
                  bg: "linear-gradient(90deg, rgba(239, 68, 68, 0.14), rgba(220, 38, 38, 0.08))",
                  border: "1px solid rgba(239, 68, 68, 0.55)",
                  shadow: "0 0 24px rgba(239, 68, 68, 0.12)",
                  accent: "#ef4444",
                  label:
                    status === "empty_chain"
                      ? "Surface unavailable (empty chain)"
                      : "Surface build FAILED",
                }
              : {
                  bg: "linear-gradient(90deg, rgba(139, 92, 246, 0.10), rgba(99, 102, 241, 0.08))",
                  border: "1px solid rgba(139, 92, 246, 0.35)",
                  shadow: "0 0 24px rgba(139, 92, 246, 0.08)",
                  accent: "var(--accent-hover)",
                  label: "Live IV surface",
                };
          return (
        <div
          className="surface-banner"
          data-surface-status={status}
          style={{
            margin: "0.75rem 0",
            padding: "0.85rem 1.1rem",
            borderRadius: "8px",
            background: palette.bg,
            border: palette.border,
            fontSize: "0.9rem",
            color: "var(--text-primary)",
            boxShadow: palette.shadow,
          }}
        >
          <strong style={{ color: palette.accent }}>{palette.label}</strong>
          {result.surfaceFailureReason && (
            <span style={{ color: "var(--text-muted)", marginLeft: "0.5rem" }}>
              — {result.surfaceFailureReason}
            </span>
          )}
          {result.sigmaAtm !== undefined && result.sigmaAtm !== null && <>
          {" — "}
          σ at strike:{" "}
          <strong style={{ color: "var(--text-primary)" }}>
            {(result.sigmaAtm * 100).toFixed(2)}%
          </strong>
          {result.sigmaBarrier !== undefined && result.sigmaBarrier !== null && (
            <>
              {" · "}σ at barrier:{" "}
              <strong style={{ color: "var(--text-primary)" }}>
                {(result.sigmaBarrier * 100).toFixed(2)}%
              </strong>
              {" · "}smile lift:{" "}
              <strong style={{ color: "var(--text-primary)" }}>
                {(
                  (result.sigmaBarrier - result.sigmaAtm) *
                  10000
                ).toFixed(0)}{" "}
                bp
              </strong>
            </>
          )}
          {result.surfaceQuotesInverted !== undefined && (
            <>
              {" · "}
              <span style={{ color: "var(--text-muted)" }}>
                {result.surfaceQuotesInverted}/{result.surfaceQuotesTotal}{" "}
                quotes inverted
              </span>
            </>
          )}
          </>}
          {result.bridgeSigmaRule && (
            <div
              className="surface-bridge-rule"
              data-testid="bridge-sigma-rule"
              style={{
                marginTop: "0.4rem",
                fontSize: "0.78rem",
                color: "var(--text-muted)",
              }}
            >
              σ rule: <span style={{ color: "var(--text-secondary)" }}>{result.bridgeSigmaRule}</span>
            </div>
          )}
        </div>
          );
        })()
      ) : null}

      {strike !== undefined && spot !== undefined && (
        <div className="vd-chart-grid">
          <PayoffChart
            optionType={result.optionType}
            strike={strike}
            spot={spot}
            premium={result.price}
            barrier={barrier}
          />
          <GreeksBar greeks={result.greeks} pinRisk={result.pinRisk} />
        </div>
      )}

      {(result.scenarioGrid || result.gammaLadder) && (
        <SensitivityHeatmap
          scenarioGrid={result.scenarioGrid}
          gammaLadder={result.gammaLadder}
        />
      )}

      <div className="report-content">
        <div
          dangerouslySetInnerHTML={{ __html: result.reportHtml }}
          className="report-html"
        ></div>
      </div>
    </div>
  );
}
