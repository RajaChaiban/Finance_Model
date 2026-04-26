"""Handler functions that wrap the existing Python pricing pipeline."""

import logging
from datetime import datetime
from typing import Tuple, Dict, Any

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

        # Route to appropriate pricing engine
        pricer_func, greeks_func, method_description = router.route(config.option_type)

        # Price option
        logger.info(f"Pricing {config.option_type} on {config.underlying}")
        price, std_error, paths = pricer_func(**pricing_params)

        # Calculate Greeks
        greeks_params = {
            k: v
            for k, v in pricing_params.items()
            if k in ["S", "K", "r", "sigma", "T", "q", "n_paths", "n_steps"]
        }
        greeks = greeks_func(**greeks_params)

        # Generate report
        logger.info("Generating HTML report")
        results = {
            "price": price,
            "std_error": std_error,
            "greeks": greeks,
            "paths": paths,
            "method": method_description,
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
        )

    except Exception as e:
        logger.error(f"Pricing failed: {e}", exc_info=True)
        raise ValueError(f"Pricing failed: {str(e)}")


def _request_to_config(request: PricingRequest) -> PricingConfig:
    """Convert PricingRequest to internal PricingConfig object."""
    # Auto-set barrier_type for knockout options if not provided
    barrier_type = request.barrier_type
    if "knockout" in request.option_type and not barrier_type:
        barrier_type = "down_and_out" if "put" in request.option_type else "up_and_out"

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
