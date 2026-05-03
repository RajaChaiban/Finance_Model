"""Handler functions that wrap the existing Python pricing pipeline."""

import logging
from datetime import datetime, timezone
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
            "averaging_method": config.averaging_method,
            "averaging_frequency": config.averaging_frequency,
            "lookback_type": config.lookback_type,
        }

        # Live IV surface (opt-in). On any failure, fall back to the scalar
        # σ from the request — the user still gets a price, just without the
        # smile-aware path. ``surface_status`` is set on every branch so the
        # client can tell skipped-by-design from failed-silently.
        sigma_atm: Optional[float] = None
        sigma_barrier: Optional[float] = None
        surface_quotes_inverted: Optional[int] = None
        surface_quotes_total: Optional[int] = None
        surface_status: str = "skipped"
        surface_failure_reason: Optional[str] = None
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
                    # Sanity gate on the surface σ at the strike. A genuine
                    # equity vol > 200% is exceptional: post-event single-name
                    # (earnings, M&A), illiquid weeklies with stale quotes, or
                    # a ticker symbol collision. The build itself didn't
                    # error, so we still fed it to the engine — but the UI
                    # needs to warn the user rather than display
                    # surface_status="ok" with a 300% σ.
                    # Tightened from 2.0 to 1.5 after senior review found that
                    # SPY 30D ATM came back with sigma_atm=164% on a single
                    # poll labelled "ok" under the 2.0 bound — a 200% bound
                    # was wide enough to let a clearly-broken surface
                    # through. 1.5 still covers post-event single-name
                    # stress (AMC/GME-style) without false positives in
                    # normal regimes.
                    SIGMA_SANITY_BOUND = 1.5
                    # Check σ at strike AND at barrier — for KO/KI products
                    # the barrier σ is the dominant risk number (it drives
                    # the knock-probability term), and a busted barrier σ on
                    # an otherwise-clean strike σ is exactly the kind of
                    # asymmetry the smile-aware FD-LV path was added to
                    # exploit. Either being out-of-bounds flips the status.
                    suspect_reasons = []
                    if sigma_atm > SIGMA_SANITY_BOUND:
                        suspect_reasons.append(
                            f"sigma_atm={sigma_atm:.2%}"
                        )
                    if sigma_barrier is not None and sigma_barrier > SIGMA_SANITY_BOUND:
                        suspect_reasons.append(
                            f"sigma_barrier={sigma_barrier:.2%}"
                        )
                    if suspect_reasons:
                        surface_status = "suspect"
                        surface_failure_reason = (
                            f"{' and '.join(suspect_reasons)} exceeds sanity "
                            f"bound {SIGMA_SANITY_BOUND:.0%}; surface likely "
                            f"built from stale or pathological quotes"
                        )
                        logger.warning(
                            "Surface for %s flagged SUSPECT: %s > %.2f",
                            config.underlying, ", ".join(suspect_reasons),
                            SIGMA_SANITY_BOUND,
                        )
                    else:
                        surface_status = "ok"
                    logger.info(
                        f"Surface built: {grid.n_quotes_inverted}/{grid.n_quotes_total} "
                        f"quotes; σ@K={sigma_atm:.4f}"
                        f"{f' σ@B={sigma_barrier:.4f}' if sigma_barrier else ''}"
                        f"{' (FD local vol)' if pricing_params.get('use_local_vol_pde') else ''}"
                        f" [{surface_status}]"
                    )
                else:
                    surface_status = "empty_chain"
                    logger.warning(
                        f"Empty option chain for {config.underlying}; "
                        f"falling back to scalar σ from request."
                    )
            except Exception as exc:
                surface_status = "failed"
                surface_failure_reason = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    f"Surface build failed ({exc}); falling back to scalar σ."
                )

        # Route to appropriate pricing engine (honours explicit engine selector).
        pricer_func, greeks_func, method_description = router.route_with_engine(
            config.option_type, engine=request.engine
        )
        # When the FD-with-local-vol path is active for KO products, the routed
        # default label "Barrier, Analytical" is misleading — overwrite it.
        if pricing_params.get("use_local_vol_pde"):
            method_description = "QuantLib (Barrier, FD with Local Vol)"

        # Price option
        logger.info(f"Pricing {config.option_type} on {config.underlying}")
        price, std_error, paths = pricer_func(**pricing_params)

        # Calculate Greeks. vol_handle / use_local_vol_pde must pass through so
        # Greeks are surface-aware too (every bump-reprice keeps the same engine).
        # variance_reduction is also forwarded so the bump-base price returned
        # in greeks["price"] uses the same MC settings as the headline price.
        greeks_params = {
            k: v
            for k, v in pricing_params.items()
            if k in ["S", "K", "r", "sigma", "T", "q", "n_paths", "n_steps",
                     "variance_reduction",
                     "barrier_level", "vol_handle", "use_local_vol_pde",
                     "averaging_method", "averaging_frequency", "lookback_type"]
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
        result = PricingResult(
            price=price,
            std_error=std_error,
            greeks=greeks,
            method=method_description,
            report_html=report_html,
            underlying=config.underlying,
            option_type=config.option_type,
            pricing_timestamp=datetime.now(timezone.utc).isoformat(),
            surface_status=surface_status,
            surface_failure_reason=surface_failure_reason,
            sigma_used=float(pricing_params["sigma"]),
            sigma_atm=sigma_atm,
            sigma_barrier=sigma_barrier,
            surface_quotes_inverted=surface_quotes_inverted,
            surface_quotes_total=surface_quotes_total,
        )

        # Deep risk: scenario grid + gamma ladder (opt-in, computed after primary price).
        if request.deep_risk:
            from src.analysis.sensitivities import compute_scenario_grid, compute_gamma_ladder
            # Use the same core parameters as the primary pricing.
            S = pricing_params["S"]
            K = pricing_params["K"]
            r = pricing_params["r"]
            sigma = pricing_params["sigma"]
            T = pricing_params["T"]
            q = pricing_params["q"]
            # Forward only non-handle engine kwargs that the engine accepts.
            engine_kwargs = {
                k: v for k, v in pricing_params.items()
                if k not in ("S", "K", "r", "sigma", "T", "q",
                             "vol_handle", "use_local_vol_pde",
                             "n_paths", "n_steps", "variance_reduction")
                and v is not None
            }
            result.scenario_grid = compute_scenario_grid(
                config.option_type, S, K, r, sigma, T, q, **engine_kwargs
            )
            result.gamma_ladder = [
                {"spot": p.spot, "delta": p.delta, "gamma": p.gamma}
                for p in compute_gamma_ladder(
                    config.option_type, S, K, r, sigma, T, q, **engine_kwargs
                )
            ]

        return result

    except ValueError:
        # Validation errors keep their original message and class — they map
        # to 422 in FastAPI without our wrapper hiding the cause.
        raise
    except Exception as e:
        logger.error(f"Pricing failed: {e}", exc_info=True)
        # Preserve the cause chain (PEP 3134) so traceback shows the original
        # exception class — wrapping in plain ValueError used to lose that.
        raise ValueError(f"Pricing failed: {str(e)}") from e


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
        averaging_method=request.averaging_method,
        averaging_frequency=request.averaging_frequency,
        lookback_type=request.lookback_type,
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
