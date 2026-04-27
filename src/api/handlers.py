"""Handler functions that wrap the existing Python pricing pipeline."""

import logging
from datetime import datetime
from typing import Tuple, Dict, Any, Optional

from src.config.loader import PricingConfig
from src.engines import router
from src.report import generator
from src.analysis.structurer_agent import StructurerReview
from .models import PricingRequest, PricingResult

logger = logging.getLogger(__name__)


def price_option(request: PricingRequest) -> PricingResult:
    """
    Price an option using the existing pipeline.

    Args:
        request: PricingRequest with all parameters

    Returns:
        PricingResult with price, Greeks, and HTML report

    Raises:
        ValueError: If pricing fails
    """
    try:
        # Convert request to internal Config object
        config = _request_to_config(request)

        # Prepare pricing parameters
        pricing_params = {
            "S": config.spot_price,
            "K": config.strike_price,
            "r": config.risk_free_rate,
            "sigma": config.volatility,
            "T": config.days_to_expiration / 365.0,
            "q": config.dividend_yield,
            "n_paths": config.n_paths,
            "n_steps": config.n_steps,
            "variance_reduction": config.variance_reduction,
            "barrier_level": config.barrier_level,
        }

        # Live IV surface (opt-in). On any failure, fall back to the scalar
        # σ from the request — the user still gets a price, just without the
        # smile-aware path.
        sigma_atm: Optional[float] = None
        sigma_barrier: Optional[float] = None
        surface_quotes_inverted: Optional[int] = None
        surface_quotes_total: Optional[int] = None
        if request.use_vol_surface:
            try:
                import QuantLib as ql
                from src.api.market_data import fetch_option_chain
                from src.data.iv_grid import build_iv_grid
                from src.data.vol_surface import build_vol_surface, sample_sigma_for_closed_form

                logger.info(
                    f"Building live IV surface for {config.underlying} "
                    f"(max {request.vol_surface_max_expiries} expiries)..."
                )
                chain = fetch_option_chain(
                    config.underlying,
                    max_expiries=request.vol_surface_max_expiries,
                )
                if chain:
                    grid = build_iv_grid(
                        chain,
                        S=pricing_params["S"],
                        r=pricing_params["r"],
                        q=pricing_params["q"],
                        min_success_rate=0.4,  # live SPY wings are illiquid
                    )
                    surface = build_vol_surface(grid)
                    pricing_params["vol_handle"] = ql.BlackVolTermStructureHandle(surface)
                    pricing_params["sigma"] = sample_sigma_for_closed_form(
                        surface,
                        K=pricing_params["K"],
                        T=pricing_params["T"],
                        S=pricing_params["S"],
                        barrier=pricing_params.get("barrier_level"),
                    )
                    sigma_atm = float(surface.blackVol(
                        pricing_params["T"], pricing_params["K"], True
                    ))
                    if pricing_params.get("barrier_level") is not None:
                        sigma_barrier = float(surface.blackVol(
                            pricing_params["T"], pricing_params["barrier_level"], True
                        ))
                    # Knockouts under a smile need the FD-with-local-vol path —
                    # the analytic Reiner-Rubinstein engine cannot separate
                    # σ-on-payoff from σ-on-knock-probability and over-prices
                    # KO calls when the put-wing is steeper than ATM.
                    # Both KO and KI are barrier products and need FD-with-local-vol
                    # under a smile — the analytic engine mis-prices the
                    # knock-probability term identically for both kinds.
                    if "knockout" in config.option_type or "knockin" in config.option_type:
                        pricing_params["use_local_vol_pde"] = True
                    surface_quotes_inverted = grid.n_quotes_inverted
                    surface_quotes_total = grid.n_quotes_total
                    logger.info(
                        f"Surface built: {grid.n_quotes_inverted}/{grid.n_quotes_total} "
                        f"quotes; σ@K={sigma_atm:.4f}"
                        f"{f' σ@B={sigma_barrier:.4f}' if sigma_barrier else ''}"
                        f"{' (FD local vol)' if pricing_params.get('use_local_vol_pde') else ''}"
                    )
                else:
                    logger.warning(
                        f"Empty option chain for {config.underlying}; "
                        f"falling back to scalar σ from request."
                    )
            except Exception as exc:
                logger.warning(
                    f"Surface build failed ({exc}); falling back to scalar σ."
                )

        # Route to appropriate pricing engine
        pricer_func, greeks_func, method_description = router.route(config.option_type)
        # When the FD-with-local-vol path is active for KO products, the routed
        # default label "Barrier, Analytical" is misleading — overwrite it.
        if pricing_params.get("use_local_vol_pde"):
            method_description = "QuantLib (Barrier, FD with Local Vol)"

        # Price option
        logger.info(f"Pricing {config.option_type} on {config.underlying}")
        price, std_error, paths = pricer_func(**pricing_params)

        # Calculate Greeks. vol_handle / use_local_vol_pde must pass through so
        # Greeks are surface-aware too (every bump-reprice keeps the same engine).
        greeks_params = {
            k: v
            for k, v in pricing_params.items()
            if k in ["S", "K", "r", "sigma", "T", "q", "n_paths", "n_steps",
                     "barrier_level", "vol_handle", "use_local_vol_pde"]
        }
        greeks = greeks_func(**greeks_params)

        # Generate report. When surface is active, sync config.volatility to the
        # σ actually fed to the engine so the report body doesn't contradict
        # the banner (which shows σ_atm / σ_barrier from the surface).
        if sigma_atm is not None:
            config.volatility = float(pricing_params["sigma"])
        logger.info("Generating HTML report")
        results = {
            "price": price,
            "std_error": std_error,
            "greeks": greeks,
            "paths": paths,
            "method": method_description,
            "sigma_atm": sigma_atm,
            "sigma_barrier": sigma_barrier,
        }

        report_html = generate_report_html_string(results, config)

        # Run structurer review
        logger.info("Running structurer analysis")
        structurer = StructurerReview()
        opinion = structurer.analyze(results, config)

        # Embed structurer analysis in report
        report_html = _inject_structurer_analysis(report_html, opinion)

        # Create response
        return PricingResult(
            price=price,
            std_error=std_error,
            greeks=greeks,
            method=method_description,
            report_html=report_html,
            underlying=config.underlying,
            option_type=config.option_type,
            pricing_timestamp=datetime.now().isoformat(),
            sigma_used=float(pricing_params["sigma"]),
            sigma_atm=sigma_atm,
            sigma_barrier=sigma_barrier,
            surface_quotes_inverted=surface_quotes_inverted,
            surface_quotes_total=surface_quotes_total,
        )

    except Exception as e:
        logger.error(f"Pricing failed: {e}", exc_info=True)
        raise ValueError(f"Pricing failed: {str(e)}")


def _request_to_config(request: PricingRequest) -> PricingConfig:
    """Convert PricingRequest to internal PricingConfig object."""
    # Auto-set barrier_type when not provided. Convention:
    #   put     → barrier *below* spot (DnO / DnI)
    #   call    → barrier *above* spot (UpO / UpI)
    # Direction is independent of KO vs KI; the engine resolves the actual
    # ql.Barrier.* enum from barrier-vs-spot at pricing time, so this label is
    # only metadata for the report.
    barrier_type = request.barrier_type
    is_knockout = "knockout" in request.option_type
    is_knockin = "knockin" in request.option_type
    if (is_knockout or is_knockin) and not barrier_type:
        is_put = "put" in request.option_type
        prefix = "down_and" if is_put else "up_and"
        suffix = "out" if is_knockout else "in"
        barrier_type = f"{prefix}_{suffix}"

    return PricingConfig(
        option_type=request.option_type,
        underlying=request.underlying,
        spot_price=request.spot_price,
        strike_price=request.strike_price,
        days_to_expiration=request.days_to_expiration,
        risk_free_rate=request.risk_free_rate,
        volatility=request.volatility,
        dividend_yield=request.dividend_yield,
        n_paths=request.n_paths,
        n_steps=request.n_steps,
        variance_reduction=request.variance_reduction,
        barrier_level=request.barrier_level,
        barrier_type=barrier_type,
        save_to="./reports/",
    )


def _inject_structurer_analysis(report_html: str, opinion: Any) -> str:
    """Inject structurer analysis into the report HTML."""
    # For now, return as-is. In Phase 3, we'll enhance report styling.
    # TODO: Embed opinion data into report_html with nice styling
    return report_html


def generate_report_html_string(results: Dict[str, Any], config: PricingConfig) -> str:
    """
    Generate HTML report as a string (not saved to file).

    Uses the existing generator.generate_report but returns the HTML directly.
    """
    import tempfile
    import os

    # Create temporary directory
    with tempfile.TemporaryDirectory() as tmpdir:
        original_save_to = config.save_to
        config.save_to = tmpdir

        try:
            # Generate report (saves to tmpdir)
            report_path = generator.generate_report(results, config)

            # Read the HTML file
            with open(report_path, "r", encoding="utf-8") as f:
                html_content = f.read()

            return html_content
        finally:
            config.save_to = original_save_to
