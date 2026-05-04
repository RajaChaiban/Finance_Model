import { useState, useEffect } from "react";
import {
  ConfigFormState,
  OPTION_TYPES,
  OPTION_TYPE_GROUPS,
  ENGINE_OPTIONS,
  VARIANCE_REDUCTION_METHODS,
} from "../types";
import { apiClient } from "../api/client";

interface ConfigFormProps {
  onSubmit: (config: ConfigFormState) => Promise<void>;
  isLoading: boolean;
  formData: ConfigFormState;
  setFormData: React.Dispatch<React.SetStateAction<ConfigFormState>>;
}

export function ConfigForm({ onSubmit, isLoading, formData, setFormData }: ConfigFormProps) {
  const [expandedSections, setExpandedSections] = useState({
    underlier: true,
    optionType: true,
    basicParams: true,
    advancedParams: false,
    pricingConfig: false,
  });

  const [errors, setErrors] = useState<Record<string, string>>({});
  const [fetchingMarketData, setFetchingMarketData] = useState(false);

  // Ticker-driven fetch: spot, dividend yield, vol, dividend info. Fires only
  // when the underlying changes — re-typing a ticker doesn't re-pull until
  // the user stops for 500 ms. Strike is reset to ATM only when it's empty
  // or stale relative to the new spot.
  useEffect(() => {
    if (!formData.underlying || formData.underlying.length === 0) return;

    let cancelled = false;
    const timer = setTimeout(async () => {
      setFetchingMarketData(true);
      try {
        // allSettled (not all): if one endpoint flakes, the others still
        // populate. Promise.all would discard a good dividendYield because
        // historical-vol returned 500.
        const [spotRes, divRes, volRes] = await Promise.allSettled([
          apiClient.getSpotPrice(formData.underlying),
          apiClient.getDividendYield(formData.underlying),
          apiClient.getHistoricalVolatility(formData.underlying, 252),
        ]);
        if (cancelled) return;

        if (spotRes.status === "rejected") console.error("spot fetch failed:", spotRes.reason);
        if (divRes.status === "rejected") console.error("dividend fetch failed:", divRes.reason);
        if (volRes.status === "rejected") console.error("vol fetch failed:", volRes.reason);

        setFormData((prev) => {
          const spotPrice = spotRes.status === "fulfilled" ? spotRes.value : prev.spotPrice;
          const dividendYield = divRes.status === "fulfilled" ? divRes.value : prev.dividendYield;
          const volatility = volRes.status === "fulfilled" ? volRes.value : prev.volatility;
          return {
            ...prev,
            spotPrice,
            dividendYield,
            volatility,
            strikePrice:
              prev.strikePrice <= 0 ||
              Math.abs(prev.strikePrice - spotPrice) / Math.max(spotPrice, 1) > 0.5
                ? Math.round(spotPrice)
                : prev.strikePrice,
          };
        });
      } finally {
        if (!cancelled) setFetchingMarketData(false);
      }
    }, 500);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [formData.underlying, setFormData]);

  // DTE-driven fetch: risk-free rate only. Cheap, just one endpoint.
  useEffect(() => {
    if (!formData.daysToExpiration || formData.daysToExpiration <= 0) return;

    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const riskFreeRate = await apiClient.getRiskFreeRate(formData.daysToExpiration);
        if (cancelled) return;
        setFormData((prev) => ({ ...prev, riskFreeRate }));
      } catch (error) {
        console.error("Error fetching risk-free rate:", error);
      }
    }, 500);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [formData.daysToExpiration, setFormData]);

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
    // Backend bound is 0 < σ ≤ 5.0 (was lt=1.0, raised to allow distressed
    // single-names / post-event vol / crypto-linked products). Form input
    // is in PERCENT (multiplied by 100 for display), so the user-visible
    // bound is 0 < % ≤ 500. Keep this in lockstep with src/api/models.py
    // PricingRequest.volatility.
    if (formData.volatility <= 0 || formData.volatility > 5) {
      newErrors.volatility =
        "Volatility (%) must be between 0 and 500 — values above 200% are unusual outside post-event distressed names";
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
                data-testid="option-type-select"
              >
                {OPTION_TYPE_GROUPS.map((group) => (
                  <optgroup key={group.label} label={group.label}>
                    {group.types.map((value) => (
                      <option key={value} value={value}>
                        {OPTION_TYPES[value]}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </div>
            <div className="form-group">
              <label>Engine</label>
              <select
                value={formData.engine ?? "auto"}
                onChange={(e) =>
                  setFormData((prev) => ({
                    ...prev,
                    engine: e.target.value as ConfigFormState["engine"],
                  }))
                }
                disabled={isLoading}
                data-testid="engine-select"
              >
                {ENGINE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
              <span style={{ fontSize: "0.8rem", color: "#6b7280" }}>
                {ENGINE_OPTIONS.find((o) => o.value === (formData.engine ?? "auto"))?.hint}
              </span>
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
                min="0"
                max="500"
                disabled={isLoading || fetchingMarketData}
                title="Volatility in percent (e.g., 20 = 20% = decimal 0.20). Backend cap is 500%."
              />
              {errors.volatility && (
                <span className="error">{errors.volatility}</span>
              )}
              <span style={{ fontSize: "0.8rem", color: "#6b7280" }}>
                Historical volatility (252-day) - can override
              </span>
            </div>
            {(formData.optionType.includes("knockout") ||
              formData.optionType.includes("knockin")) && (
              <>
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
                <div className="form-group">
                  <label>Barrier Monitoring</label>
                  <select
                    value={formData.monitoring || "continuous"}
                    onChange={(e) =>
                      setFormData((prev) => ({
                        ...prev,
                        monitoring: e.target.value as
                          | "continuous"
                          | "daily"
                          | "weekly"
                          | "monthly",
                      }))
                    }
                    disabled={isLoading}
                    data-testid="monitoring-select"
                  >
                    <option value="continuous">Continuous</option>
                    <option value="daily">Daily</option>
                    <option value="weekly">Weekly</option>
                    <option value="monthly">Monthly</option>
                  </select>
                  <span style={{ fontSize: "0.8rem", color: "#6b7280" }}>
                    Discrete monitoring lifts barrier value vs continuous —
                    daily/weekly/monthly fixings are how exotics actually trade.
                  </span>
                </div>
              </>
            )}
            {(formData.optionType === "american_call" ||
              formData.optionType === "american_put") && (
              <div className="form-group" data-testid="dividend-schedule-group">
                <label>Dividend Schedule</label>
                <span style={{ fontSize: "0.8rem", color: "#6b7280", display: "block", marginBottom: "0.4rem" }}>
                  Discrete dividends for American exercise. Leave empty to use
                  the continuous yield above.
                </span>
                {(formData.dividendSchedule ?? []).map((row, idx) => (
                  <div
                    key={idx}
                    style={{
                      display: "flex",
                      gap: "0.4rem",
                      alignItems: "center",
                      marginBottom: "0.35rem",
                    }}
                    data-testid={`dividend-row-${idx}`}
                  >
                    <input
                      type="date"
                      value={row[0]}
                      onChange={(e) => {
                        const next = [...(formData.dividendSchedule ?? [])];
                        next[idx] = [e.target.value, next[idx][1]];
                        setFormData((prev) => ({ ...prev, dividendSchedule: next }));
                      }}
                      disabled={isLoading}
                      style={{ flex: "2 1 0%" }}
                    />
                    <input
                      type="number"
                      value={Number.isFinite(row[1]) ? row[1] : 0}
                      onChange={(e) => {
                        const next = [...(formData.dividendSchedule ?? [])];
                        const amt = parseFloat(e.target.value);
                        next[idx] = [next[idx][0], Number.isFinite(amt) ? amt : 0];
                        setFormData((prev) => ({ ...prev, dividendSchedule: next }));
                      }}
                      placeholder="amount $"
                      step="0.01"
                      min="0"
                      disabled={isLoading}
                      style={{ flex: "1 1 0%" }}
                    />
                    <button
                      type="button"
                      onClick={() => {
                        const next = [...(formData.dividendSchedule ?? [])];
                        next.splice(idx, 1);
                        setFormData((prev) => ({
                          ...prev,
                          dividendSchedule: next.length > 0 ? next : undefined,
                        }));
                      }}
                      disabled={isLoading}
                      title="Remove dividend"
                      aria-label="Remove dividend"
                      style={{
                        background: "transparent",
                        border: "1px solid var(--border)",
                        color: "var(--text-secondary)",
                        borderRadius: "var(--radius-sm)",
                        padding: "0.25rem 0.55rem",
                        cursor: "pointer",
                      }}
                    >
                      ×
                    </button>
                  </div>
                ))}
                <button
                  type="button"
                  onClick={() => {
                    const today = new Date().toISOString().split("T")[0];
                    const next = [
                      ...(formData.dividendSchedule ?? []),
                      [today, 0] as [string, number],
                    ];
                    setFormData((prev) => ({ ...prev, dividendSchedule: next }));
                  }}
                  disabled={isLoading}
                  data-testid="add-dividend-btn"
                  style={{
                    background: "var(--accent-soft)",
                    color: "var(--accent)",
                    border: "1px solid var(--accent)",
                    borderRadius: "var(--radius-sm)",
                    padding: "0.35rem 0.75rem",
                    cursor: "pointer",
                    fontSize: "0.85rem",
                    marginTop: "0.25rem",
                  }}
                >
                  + Add dividend
                </button>
              </div>
            )}
            {formData.optionType.startsWith("asian_") && (
              <>
                <div className="form-group">
                  <label>Averaging Method</label>
                  <select
                    value={formData.averagingMethod || "geometric"}
                    onChange={(e) =>
                      setFormData((prev) => ({
                        ...prev,
                        averagingMethod: e.target.value as "geometric" | "arithmetic",
                      }))
                    }
                    disabled={isLoading}
                  >
                    <option value="geometric">Geometric (Closed-form)</option>
                    <option value="arithmetic">Arithmetic (MC + Control Variate)</option>
                  </select>
                  <span style={{ fontSize: "0.8rem", color: "#6b7280" }}>
                    Geometric has an exact closed form (Kemna-Vorst). Arithmetic
                    is the market-traded payoff and uses MC with the geometric
                    Asian as a control variate.
                  </span>
                </div>
                <div className="form-group">
                  <label>Averaging Frequency</label>
                  <select
                    value={formData.averagingFrequency || "daily"}
                    onChange={(e) =>
                      setFormData((prev) => ({
                        ...prev,
                        averagingFrequency: e.target.value as
                          | "daily"
                          | "weekly"
                          | "monthly",
                      }))
                    }
                    disabled={isLoading}
                  >
                    <option value="daily">Daily</option>
                    <option value="weekly">Weekly</option>
                    <option value="monthly">Monthly</option>
                  </select>
                </div>
              </>
            )}
            {formData.optionType.startsWith("lookback_") && (
              <div className="form-group">
                <label>Lookback Type</label>
                <select
                  value={formData.lookbackType || "fixed"}
                  onChange={(e) =>
                    setFormData((prev) => ({
                      ...prev,
                      lookbackType: e.target.value as "fixed" | "floating",
                    }))
                  }
                  disabled={isLoading}
                >
                  <option value="fixed">Fixed-Strike (Conze-Viswanathan)</option>
                  <option value="floating">Floating-Strike (Goldman-Sosin-Gatto)</option>
                </select>
                <span style={{ fontSize: "0.8rem", color: "#6b7280" }}>
                  Fixed-strike pays max(S_max − K, 0) (call). Floating-strike
                  pays S_T − S_min (call) — strike is the path's running min/max.
                </span>
              </div>
            )}
            <div className="form-group">
              <label>
                <input
                  type="checkbox"
                  checked={formData.useVolSurface}
                  onChange={(e) =>
                    setFormData((prev) => ({
                      ...prev,
                      useVolSurface: e.target.checked,
                    }))
                  }
                  disabled={isLoading}
                  style={{ marginRight: "0.5rem" }}
                />
                Use live IV surface (calibrate from option chain)
              </label>
              <span style={{ fontSize: "0.8rem", color: "#6b7280" }}>
                Slower (~3 s extra) but smile-aware. Recommended for barrier
                products (knockouts and knockins) — sees the barrier-side vol
                the flat-σ calculator misses.
              </span>
            </div>
            <div className="form-group">
              <label data-testid="deep-risk-label">
                <input
                  type="checkbox"
                  data-testid="deep-risk-checkbox"
                  checked={formData.deepRisk ?? false}
                  onChange={(e) =>
                    setFormData((prev) => ({
                      ...prev,
                      deepRisk: e.target.checked,
                    }))
                  }
                  disabled={isLoading}
                  style={{ marginRight: "0.5rem" }}
                />
                Deep risk (scenario grid + gamma ladder)
              </label>
              <span style={{ fontSize: "0.8rem", color: "#6b7280" }}>
                Computes a spot × vol scenario grid and a gamma/delta ladder.
                Adds a sensitivity heatmap to the results view.
              </span>
            </div>
          </div>
        )}
      </div>

      {/* Pricing Configuration Section — only relevant when Monte Carlo
          will actually run. We hide it entirely otherwise so European /
          tree-priced American users don't see phantom controls. */}
      {(formData.engine === "mc" ||
        (formData.optionType.startsWith("asian_") &&
          formData.averagingMethod === "arithmetic")) && (
      <div className="form-section" data-testid="pricing-config-section">
        <button
          type="button"
          className="section-header"
          onClick={() => toggleSection("pricingConfig")}
        >
          <span>⚡ Monte Carlo Configuration</span>
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
      )}

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
