import { useState, useEffect } from "react";
import { ConfigFormState, OPTION_TYPES, VARIANCE_REDUCTION_METHODS } from "../types";
import { apiClient } from "../api/client";

interface ConfigFormProps {
  onSubmit: (config: ConfigFormState) => Promise<void>;
  isLoading: boolean;
}

export function ConfigForm({ onSubmit, isLoading }: ConfigFormProps) {
  const [expandedSections, setExpandedSections] = useState({
    underlier: true,
    optionType: true,
    basicParams: true,
    advancedParams: false,
    pricingConfig: false,
  });

  const [errors, setErrors] = useState<Record<string, string>>({});
  const [fetchingMarketData, setFetchingMarketData] = useState(false);
  const [formData, setFormData] = useState<ConfigFormState>({
    underlying: "SPY",
    spotPrice: 5415.23,
    optionType: "american_put",
    strikePrice: 5400,
    daysToExpiration: 90,
    riskFreeRate: 0.045,
    volatility: 0.1845,
    dividendYield: 0.015,
    nPaths: 10000,
    nSteps: 90,
    varianceReduction: "antithetic",
  });

  // Fetch market data when underlying or expiration changes
  useEffect(() => {
    const fetchMarketData = async () => {
      if (!formData.underlying || formData.underlying.length === 0) return;

      setFetchingMarketData(true);
      try {
        const [spotPrice, dividendYield, volatility, riskFreeRate, dividendInfo] =
          await Promise.all([
            apiClient.getSpotPrice(formData.underlying),
            apiClient.getDividendYield(formData.underlying),
            apiClient.getHistoricalVolatility(formData.underlying, 252),
            apiClient.getRiskFreeRate(formData.daysToExpiration),
            apiClient.getDividendInfo(formData.underlying),
          ]);

        setFormData((prev) => ({
          ...prev,
          spotPrice,
          dividendYield,
          volatility,
          riskFreeRate,
        }));
      } catch (error) {
        console.error("Error fetching market data:", error);
        // Keep existing values on error
      } finally {
        setFetchingMarketData(false);
      }
    };

    // Debounce: only fetch if user stops typing for 500ms
    const timer = setTimeout(() => {
      fetchMarketData();
    }, 500);

    return () => clearTimeout(timer);
  }, [formData.underlying, formData.daysToExpiration]);

  const toggleSection = (section: keyof typeof expandedSections) => {
    setExpandedSections((prev) => ({
      ...prev,
      [section]: !prev[section],
    }));
  };

  const handleChange = (
    field: keyof ConfigFormState,
    value: string | number
  ) => {
    setFormData((prev) => ({
      ...prev,
      [field]: value,
    }));
    // Clear error for this field
    if (errors[field]) {
      setErrors((prev) => {
        const newErrors = { ...prev };
        delete newErrors[field];
        return newErrors;
      });
    }
  };

  const validateForm = (): boolean => {
    const newErrors: Record<string, string> = {};

    if (formData.strikePrice <= 0) {
      newErrors.strikePrice = "Strike price must be positive";
    }
    if (formData.spotPrice <= 0) {
      newErrors.spotPrice = "Spot price must be positive";
    }
    if (formData.daysToExpiration <= 0) {
      newErrors.daysToExpiration = "Days to expiration must be positive";
    }
    if (formData.volatility <= 0 || formData.volatility > 1) {
      newErrors.volatility = "Volatility must be between 0 and 1";
    }
    if (formData.nPaths < 100) {
      newErrors.nPaths = "Number of paths must be at least 100";
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!validateForm()) return;

    try {
      await onSubmit(formData);
    } catch (error) {
      setErrors({
        submit: error instanceof Error ? error.message : "Pricing failed",
      });
    }
  };

  return (
    <form onSubmit={handleSubmit} className="config-form">
      {/* Underlier Section */}
      <div className="form-section">
        <button
          type="button"
          className="section-header"
          onClick={() => toggleSection("underlier")}
        >
          <span>📊 Underlier Selection</span>
          <span>{expandedSections.underlier ? "▼" : "▶"}</span>
        </button>
        {expandedSections.underlier && (
          <div className="section-content">
            <div className="form-group">
              <label>Ticker</label>
              <input
                type="text"
                value={formData.underlying}
                onChange={(e) =>
                  handleChange("underlying", e.target.value.toUpperCase())
                }
                placeholder="SPY"
                disabled={isLoading}
              />
            </div>
            <div className="form-group">
              <label>Spot Price ($) {fetchingMarketData && "⏳"}</label>
              <input
                type="number"
                value={formData.spotPrice}
                onChange={(e) =>
                  handleChange("spotPrice", parseFloat(e.target.value))
                }
                step="0.01"
                disabled={isLoading || fetchingMarketData}
              />
              {errors.spotPrice && (
                <span className="error">{errors.spotPrice}</span>
              )}
            </div>
            <div className="form-group">
              <label>Dividend Yield (%) {fetchingMarketData && "⏳"}</label>
              <input
                type="number"
                value={formData.dividendYield * 100}
                onChange={(e) =>
                  handleChange("dividendYield", parseFloat(e.target.value) / 100)
                }
                step="0.1"
                disabled={isLoading || fetchingMarketData}
                title="Auto-fetched from company info"
              />
              <span style={{ fontSize: "0.8rem", color: "#6b7280" }}>
                Auto-fetched (can override)
              </span>
            </div>
          </div>
        )}
      </div>

      {/* Option Type Section */}
      <div className="form-section">
        <button
          type="button"
          className="section-header"
          onClick={() => toggleSection("optionType")}
        >
          <span>⚙️ Option Type</span>
          <span>{expandedSections.optionType ? "▼" : "▶"}</span>
        </button>
        {expandedSections.optionType && (
          <div className="section-content">
            <div className="form-group">
              <label>Type</label>
              <select
                value={formData.optionType}
                onChange={(e) => handleChange("optionType", e.target.value)}
                disabled={isLoading}
              >
                {Object.entries(OPTION_TYPES).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
          </div>
        )}
      </div>

      {/* Basic Parameters Section */}
      <div className="form-section">
        <button
          type="button"
          className="section-header"
          onClick={() => toggleSection("basicParams")}
        >
          <span>📈 Basic Parameters</span>
          <span>{expandedSections.basicParams ? "▼" : "▶"}</span>
        </button>
        {expandedSections.basicParams && (
          <div className="section-content">
            <div className="form-group">
              <label>Strike Price ($)</label>
              <input
                type="number"
                value={formData.strikePrice}
                onChange={(e) =>
                  handleChange("strikePrice", parseFloat(e.target.value))
                }
                step="0.01"
                disabled={isLoading}
              />
              {errors.strikePrice && (
                <span className="error">{errors.strikePrice}</span>
              )}
            </div>
            <div className="form-group">
              <label>Days to Expiration</label>
              <input
                type="number"
                value={formData.daysToExpiration}
                onChange={(e) =>
                  handleChange("daysToExpiration", parseInt(e.target.value))
                }
                disabled={isLoading}
              />
              {errors.daysToExpiration && (
                <span className="error">{errors.daysToExpiration}</span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Advanced Parameters Section */}
      <div className="form-section">
        <button
          type="button"
          className="section-header"
          onClick={() => toggleSection("advancedParams")}
        >
          <span>🔧 Advanced Parameters</span>
          <span>{expandedSections.advancedParams ? "▼" : "▶"}</span>
        </button>
        {expandedSections.advancedParams && (
          <div className="section-content">
            <div className="form-group">
              <label>Risk-Free Rate (%) {fetchingMarketData && "⏳"}</label>
              <input
                type="number"
                value={formData.riskFreeRate * 100}
                onChange={(e) =>
                  handleChange("riskFreeRate", parseFloat(e.target.value) / 100)
                }
                step="0.1"
                disabled={isLoading || fetchingMarketData}
                title="Auto-fetched from US Treasury yields"
              />
              <span style={{ fontSize: "0.8rem", color: "#6b7280" }}>
                From US Treasury (auto-updated)
              </span>
            </div>
            <div className="form-group">
              <label>Volatility (%) {fetchingMarketData && "⏳"}</label>
              <input
                type="number"
                value={formData.volatility * 100}
                onChange={(e) =>
                  handleChange("volatility", parseFloat(e.target.value) / 100)
                }
                step="0.1"
                disabled={isLoading || fetchingMarketData}
                title="Auto-fetched historical volatility (252-day)"
              />
              {errors.volatility && (
                <span className="error">{errors.volatility}</span>
              )}
              <span style={{ fontSize: "0.8rem", color: "#6b7280" }}>
                Historical volatility (252-day) - can override
              </span>
            </div>
            {formData.optionType.includes("knockout") && (
              <div className="form-group">
                <label>Barrier Level ($)</label>
                <input
                  type="number"
                  value={formData.barrierLevel || ""}
                  onChange={(e) =>
                    handleChange("barrierLevel", parseFloat(e.target.value))
                  }
                  step="0.01"
                  disabled={isLoading}
                />
              </div>
            )}
          </div>
        )}
      </div>

      {/* Pricing Configuration Section */}
      <div className="form-section">
        <button
          type="button"
          className="section-header"
          onClick={() => toggleSection("pricingConfig")}
        >
          <span>⚡ Pricing Configuration</span>
          <span>{expandedSections.pricingConfig ? "▼" : "▶"}</span>
        </button>
        {expandedSections.pricingConfig && (
          <div className="section-content">
            <div className="form-group">
              <label>Number of Paths</label>
              <input
                type="number"
                value={formData.nPaths}
                onChange={(e) =>
                  handleChange("nPaths", parseInt(e.target.value))
                }
                disabled={isLoading}
              />
              {errors.nPaths && (
                <span className="error">{errors.nPaths}</span>
              )}
            </div>
            <div className="form-group">
              <label>Number of Steps</label>
              <input
                type="number"
                value={formData.nSteps}
                onChange={(e) =>
                  handleChange("nSteps", parseInt(e.target.value))
                }
                disabled={isLoading}
              />
            </div>
            <div className="form-group">
              <label>Variance Reduction</label>
              <select
                value={formData.varianceReduction}
                onChange={(e) =>
                  handleChange("varianceReduction", e.target.value)
                }
                disabled={isLoading}
              >
                {VARIANCE_REDUCTION_METHODS.map((method) => (
                  <option key={method.value} value={method.value}>
                    {method.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
        )}
      </div>

      {/* Submit Error */}
      {errors.submit && (
        <div className="error-box">{errors.submit}</div>
      )}

      {/* Submit Button */}
      <button
        type="submit"
        className="submit-button"
        disabled={isLoading || fetchingMarketData}
      >
        {isLoading ? "⏳ Running Pricing..." : fetchingMarketData ? "📊 Fetching Market Data..." : "▶️ Run Pricing"}
      </button>
    </form>
  );
}
