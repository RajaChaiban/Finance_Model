import { useRef, useState } from "react";
import { ConfigForm } from "./ConfigForm";
import { ReportDisplay } from "./ReportDisplay";
import { CopilotPanel } from "./CopilotPanel";
import { EsmmLabPanel } from "./EsmmLabPanel";
import { Header } from "./Header";
import { IndexTickerStrip } from "./IndexTickerStrip";
import { MoversGrid } from "./MoversGrid";
import { BriefingPanel } from "./BriefingPanel";
import { ConfigFormState, PricingResult, DEFAULT_CONFIG } from "../types";
import { apiClient } from "../api/client";
import { useMarketMovers } from "../hooks/useMarketMovers";

type Mode = "pricer" | "copilot" | "esmm";

// Index tickers (^GSPC, ^IXIC, ...) aren't valid yfinance lookups for
// dividend yield and aren't what gets traded — desks price index options
// against the corresponding ETF. Map index → ETF so the form gets a real
// dividend yield, real strike granularity, and real options data.
const INDEX_TO_ETF: Record<string, string> = {
  "^GSPC": "SPY",
  "^IXIC": "QQQ",
  "^DJI": "DIA",
  "^RUT": "IWM",
};

function resolveTicker(raw: string): string {
  return INDEX_TO_ETF[raw] ?? raw;
}

export function Dashboard() {
  const [mode, setMode] = useState<Mode>("pricer");
  const [result, setResult] = useState<PricingResult | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [activeStep, setActiveStep] = useState(1);
  const [formData, setFormData] = useState<ConfigFormState>(DEFAULT_CONFIG);
  const configRef = useRef<HTMLDivElement | null>(null);

  const movers = useMarketMovers();

  const handlePickTicker = (ticker: string, price: number) => {
    const resolved = resolveTicker(ticker);
    // When we map an index to its ETF, the click-time spot is the index
    // level (e.g. ^GSPC=7230) and is meaningless for SPY (~720). Drop it
    // and let ConfigForm's useEffect fetch the real ETF spot.
    const useClickSpot = resolved === ticker;
    const spot = useClickSpot ? Math.round(price * 100) / 100 : 0;
    setMode("pricer");
    setActiveStep(1);
    setFormData((prev) => ({
      ...prev,
      underlying: resolved,
      spotPrice: spot,
      strikePrice: spot > 0 ? Math.round(spot) : 0,
    }));
    setTimeout(() => {
      configRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 50);
  };

  const handlePricingSubmit = async (config: ConfigFormState) => {
    setIsLoading(true);
    try {
      const isBarrier =
        config.optionType.startsWith("knockout_") ||
        config.optionType.startsWith("knockin_");
      const isAmerican = config.optionType.startsWith("american_");
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
        averaging_method: config.averagingMethod,
        averaging_frequency: config.averagingFrequency,
        lookback_type: config.lookbackType,
        // Only forward `monitoring` for barrier products; the backend
        // ignores it elsewhere but we keep payloads tight.
        monitoring: isBarrier ? config.monitoring : undefined,
        // Empty schedule is sent as `undefined` so the backend treats it as
        // "use continuous q" rather than "no dividends at all".
        dividend_schedule:
          isAmerican &&
          config.dividendSchedule &&
          config.dividendSchedule.length > 0
            ? config.dividendSchedule
            : undefined,
        engine: config.engine ?? "auto",
        use_vol_surface: config.useVolSurface,
        deep_risk: config.deepRisk,
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
        surfaceStatus: response.surface_status,
        surfaceFailureReason: response.surface_failure_reason,
        sigmaUsed: response.sigma_used,
        sigmaAtm: response.sigma_atm,
        sigmaBarrier: response.sigma_barrier,
        surfaceQuotesInverted: response.surface_quotes_inverted,
        surfaceQuotesTotal: response.surface_quotes_total,
        pinRisk: response.pin_risk,
        bridgeSigmaRule: response.bridge_sigma_rule,
        scenarioGrid: response.scenario_grid,
        gammaLadder: response.gamma_ladder,
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
      <Header asOf={movers.data?.as_of} source={movers.data?.source} />

      <div className="vd-market-bar">
        <div className="vd-market-bar-inner">
          <IndexTickerStrip
            indices={movers.data?.indices ?? []}
            isLoading={movers.isLoading}
            onPickTicker={handlePickTicker}
          />
        </div>
      </div>

      <BriefingPanel />

      <MoversGrid
        gainers={movers.data?.gainers ?? []}
        losers={movers.data?.losers ?? []}
        volatile={movers.data?.volatile ?? []}
        isLoading={movers.isLoading}
        isStale={movers.isStale}
        onPickTicker={handlePickTicker}
      />

      <section className="hero-section vd-hero-condensed">
        <div className="hero-content">
          <h1 className="hero-title">Derivatives Pricing Dashboard</h1>
          <p className="hero-subtitle">
            Professional options pricing with real-time Greeks and risk analysis
          </p>
          <div className="hero-divider"></div>
        </div>
      </section>

      <div className="main-content" ref={configRef}>
        {/* Mode Switcher: Quick Pricer (existing) vs Structuring Co-pilot vs eSMM Lab */}
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
          <button
            className={`mode-btn ${mode === "esmm" ? "active" : ""}`}
            onClick={() => setMode("esmm")}
          >
            eSMM Lab
          </button>
        </div>

        {mode === "copilot" ? (
          <CopilotPanel />
        ) : mode === "esmm" ? (
          <EsmmLabPanel />
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
                spot={formData.spotPrice}
                strike={formData.strikePrice}
                barrier={formData.barrierLevel}
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
