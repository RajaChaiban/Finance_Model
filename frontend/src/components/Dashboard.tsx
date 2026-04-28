import { useState } from "react";
import { ConfigForm } from "./ConfigForm";
import { ReportDisplay } from "./ReportDisplay";
import { CopilotPanel } from "./CopilotPanel";
import { ConfigFormState, PricingResult, DEFAULT_CONFIG } from "../types";
import { apiClient } from "../api/client";

type Mode = "pricer" | "copilot";

export function Dashboard() {
  const [mode, setMode] = useState<Mode>("pricer");
  const [result, setResult] = useState<PricingResult | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [activeStep, setActiveStep] = useState(1);
  // Lifted form state — survives switching between Configure and Results
  // tabs so "New Scenario" doesn't wipe the user's spot/strike/etc.
  const [formData, setFormData] = useState<ConfigFormState>(DEFAULT_CONFIG);

  const handlePricingSubmit = async (config: ConfigFormState) => {
    setIsLoading(true);
    try {
      const request = {
        option_type: config.optionType,
        underlying: config.underlying,
        spot_price: config.spotPrice,
        strike_price: config.strikePrice,
        days_to_expiration: config.daysToExpiration,
        risk_free_rate: config.riskFreeRate,
        volatility: config.volatility,
        dividend_yield: config.dividendYield,
        n_paths: config.nPaths,
        n_steps: config.nSteps,
        variance_reduction: config.varianceReduction,
        barrier_level: config.barrierLevel,
        use_vol_surface: config.useVolSurface,
      };

      const response = await apiClient.price(request);

      const formattedResult: PricingResult = {
        price: response.price,
        stdError: response.std_error,
        greeks: response.greeks,
        method: response.method,
        reportHtml: response.report_html,
        underlying: response.underlying,
        optionType: response.option_type,
        pricingTimestamp: response.pricing_timestamp,
        sigmaUsed: response.sigma_used,
        sigmaAtm: response.sigma_atm,
        sigmaBarrier: response.sigma_barrier,
        surfaceQuotesInverted: response.surface_quotes_inverted,
        surfaceQuotesTotal: response.surface_quotes_total,
      };

      setResult(formattedResult);
      setActiveStep(2);
    } finally {
      setIsLoading(false);
    }
  };

  const handleNewScenario = () => {
    setResult(null);
    setActiveStep(1);
  };

  return (
    <div className="dashboard-onepage">
      {/* Hero Section */}
      <section className="hero-section">
        <div className="hero-content">
          <h1 className="hero-title">Derivatives Pricing Dashboard</h1>
          <p className="hero-subtitle">
            Professional options pricing with real-time Greeks and risk analysis
          </p>
          <div className="hero-divider"></div>
        </div>
      </section>

      {/* Main Content */}
      <div className="main-content">
        {/* Mode Switcher: Quick Pricer (existing) vs Structuring Co-pilot (new) */}
        <div className="mode-switcher">
          <button
            className={`mode-btn ${mode === "pricer" ? "active" : ""}`}
            onClick={() => setMode("pricer")}
          >
            Quick Pricer
          </button>
          <button
            className={`mode-btn ${mode === "copilot" ? "active" : ""}`}
            onClick={() => setMode("copilot")}
          >
            Structuring Co-pilot
          </button>
        </div>

        {mode === "copilot" ? (
          <CopilotPanel />
        ) : (
        <>
        {/* Step Indicator */}
        <div className="step-indicator">
          <div className={`step ${activeStep === 1 ? "active" : "completed"}`}>
            <div className="step-number">1</div>
            <div className="step-label">Configure</div>
          </div>
          <div className="step-line"></div>
          <div className={`step ${activeStep === 2 ? "active" : ""}`}>
            <div className="step-number">2</div>
            <div className="step-label">Results</div>
          </div>
        </div>

        {/* Content Sections */}
        {activeStep === 1 ? (
          <section className="config-section">
            <div className="section-container">
              <h2 className="section-title">Price Your Option</h2>
              <p className="section-description">
                Enter the option parameters below to calculate price and Greeks
              </p>
              <ConfigForm
                onSubmit={handlePricingSubmit}
                isLoading={isLoading}
                formData={formData}
                setFormData={setFormData}
              />
            </div>
          </section>
        ) : (
          <section className="results-section">
            <div className="section-container">
              <div className="results-header">
                <h2 className="section-title">Pricing Results</h2>
                <button
                  onClick={handleNewScenario}
                  className="btn-new-scenario"
                >
                  ✏️ New Scenario
                </button>
              </div>
              <ReportDisplay
                result={result}
                isLoading={isLoading}
                onNewScenario={handleNewScenario}
              />
            </div>
          </section>
        )}
        </>
        )}
      </div>

      {/* Footer */}
      <footer className="dashboard-footer">
        <p>
          Built for traders and quants • Powered by QuantLib and Monte Carlo
        </p>
        <p className="footer-date">
          {new Date().toLocaleDateString("en-US", {
            weekday: "long",
            year: "numeric",
            month: "long",
            day: "numeric",
          })}
        </p>
      </footer>
    </div>
  );
}
