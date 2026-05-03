"""Pydantic models for API requests and responses."""

from pydantic import BaseModel, Field
from typing import Literal, Optional, Dict, Any, List

from src.analysis.sensitivities import SensitivityBlock


class PricingRequest(BaseModel):
    """Request schema for /api/price endpoint."""

    option_type: str = Field(..., description="Option type: american_put, european_call, etc.")
    underlying: str = Field(..., description="Ticker symbol: SPY, QQQ, etc.")
    spot_price: float = Field(..., gt=0, description="Current spot price")
    strike_price: float = Field(..., gt=0, description="Strike price")
    days_to_expiration: int = Field(..., gt=0, description="Days until expiration")
    risk_free_rate: float = Field(default=0.045, ge=-0.05, le=0.50,
                                   description="Risk-free rate (annual, decimal). Allows negative for EUR/JPY.")
    # Upper bound 5.0 mirrors the IV solver's bracket (solver.py). Distressed
    # single-names, post-event vol, and crypto-linked products routinely run
    # above 100%; the prior gt=0/lt=1 cap rejected valid pricing requests.
    volatility: float = Field(..., gt=0.0, le=5.0,
                               description="Volatility (annual, decimal; 0 < σ ≤ 5.0)")
    dividend_yield: float = Field(default=0.015, ge=0.0, le=0.50,
                                   description="Dividend yield (annual, decimal)")

    # Pricing configuration
    n_paths: int = Field(default=10000, description="Number of Monte Carlo paths")
    n_steps: int = Field(default=90, description="Number of time steps")
    variance_reduction: str = Field(default="antithetic", description="Variance reduction method")

    # Optional barrier level for barrier options
    barrier_level: Optional[float] = Field(default=None, description="Barrier level for knockout options")
    barrier_type: Optional[str] = Field(default=None, description="Barrier type: 'down_and_out' or 'up_and_out'")

    # Optional Asian fields (only used when option_type starts with 'asian_')
    averaging_method: Optional[str] = Field(default=None, description="Asian averaging: 'geometric' or 'arithmetic'")
    averaging_frequency: Optional[str] = Field(default=None, description="Asian fixings: 'daily' | 'weekly' | 'monthly'")

    # Optional lookback field (only used when option_type starts with 'lookback_')
    lookback_type: Optional[str] = Field(default=None, description="Lookback variant: 'fixed' or 'floating'")

    # Engine selector: "auto" reproduces the existing route() dispatch; "mc" forces
    # Monte Carlo LSM for American options; "analytic"/"tree"/"fdm" collapse to the
    # QL default (phase 1 — reserved for future explicit routing).
    engine: Literal["auto", "analytic", "tree", "mc", "fdm"] = Field(
        default="auto", description="Pricing engine override: auto|analytic|tree|mc|fdm"
    )

    # Live IV surface (opt-in). When true, the handler fetches the option chain,
    # inverts each quote to BS implied vol, builds a BlackVarianceSurface, and
    # passes it to the engine. Adds ~3s for chain fetch + IV inversion.
    use_vol_surface: bool = Field(default=False, description="Calibrate live IV surface from option chain")
    vol_surface_max_expiries: int = Field(default=6, description="Front-N expiries for surface fit")

    # Deep risk: when True, compute scenario grid (S x sigma) and gamma ladder.
    deep_risk: bool = Field(default=False, description="Compute scenario grid + gamma ladder")


class PricingResult(BaseModel):
    """Response schema for /api/price endpoint."""

    price: float = Field(..., description="Option price")
    std_error: Optional[float] = Field(None, description="Standard error (Monte Carlo)")
    greeks: Dict[str, float] = Field(..., description="Greeks: delta, gamma, vega, theta, etc.")
    method: str = Field(..., description="Pricing method used")
    report_html: str = Field(..., description="Generated HTML report")
    underlying: str = Field(..., description="Underlying ticker")
    option_type: str = Field(..., description="Option type")
    pricing_timestamp: str = Field(..., description="ISO timestamp of pricing")

    # Surface diagnostics. ``surface_status`` is ALWAYS set so the client can
    # distinguish the five outcomes:
    #   "skipped"      - request.use_vol_surface was False; flat-σ used by design
    #   "ok"           - surface built and consumed by the engine
    #   "suspect"      - surface built but sigma_atm exceeds sanity bound
    #                    (typically post-event single-name with quote noise);
    #                    the price IS computed against this surface but the UI
    #                    should warn the user that the σ is implausible
    #   "empty_chain"  - chain fetched but had no usable rows (illiquid name)
    #   "failed"       - exception during surface build; fell back to flat-σ
    # Without this field, "failed" silently masquerades as "skipped" and the
    # trader sees no banner that the surface toggle was honored as a soft
    # preference rather than a contract.
    surface_status: Literal["skipped", "ok", "suspect", "failed", "empty_chain"] = Field(
        default="skipped", description="Vol-surface build outcome"
    )
    surface_failure_reason: Optional[str] = Field(
        None, description="Exception message when surface_status == 'failed'"
    )
    sigma_used: Optional[float] = Field(None, description="σ actually fed to the closed-form engine")
    sigma_atm: Optional[float] = Field(None, description="Surface σ at strike")
    sigma_barrier: Optional[float] = Field(None, description="Surface σ at barrier (KO only)")
    surface_quotes_inverted: Optional[int] = Field(None, description="IV grid quotes successfully inverted")
    surface_quotes_total: Optional[int] = Field(None, description="IV grid quotes attempted")

    # Deep risk (populated only when request.deep_risk=True).
    scenario_grid: Optional[SensitivityBlock] = Field(None, description="Price grid across spot x vol shifts")
    gamma_ladder: Optional[List[Dict[str, float]]] = Field(None, description="Delta and gamma across spot levels")


class ErrorResponse(BaseModel):
    """Error response schema."""

    error: str = Field(..., description="Error message")
    detail: Optional[str] = Field(None, description="Additional details")
