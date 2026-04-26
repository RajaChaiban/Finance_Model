import { PricingResult } from "../types";

interface ReportDisplayProps {
  result: PricingResult | null;
  isLoading: boolean;
  onNewScenario: () => void;
}

export function ReportDisplay({
  result,
  isLoading,
  onNewScenario,
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

      <div className="report-content">
        <div
          dangerouslySetInnerHTML={{ __html: result.reportHtml }}
          className="report-html"
        ></div>
      </div>
    </div>
  );
}
