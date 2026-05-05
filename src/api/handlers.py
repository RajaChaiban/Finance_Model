"""Handler functions that wrap the existing Python pricing pipeline."""

import logging
import time
from datetime import datetime, date, timezone
from typing import Tuple, Dict, Any, Optional, List

from src.config.loader import PricingConfig
from src.engines import router
from src.report import generator
from src.analysis.structurer_agent import StructurerReview
from src.analysis.xva import XVAInputs, compute_xva
from src.analysis.vanna_volga import compute_vanna_volga
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

        # Convert ISO-string dividend schedule (wire format) to ql.Date tuples
        # before handing it to the engine. Dates after expiry are silently
        # dropped by QL; we don't filter here so the user can see the full
        # schedule in the report.
        ql_dividend_schedule = _convert_dividend_schedule(config.dividend_schedule)

        # Discrete dividend schedules are only consumed by the American
        # engines; the router's ``_ql_kwargs`` filter forwards only surface
        # kwargs (vol_handle, use_local_vol_pde) and would otherwise drop
        # ``dividend_schedule`` for European/Asian/Lookback/Barrier products
        # without a peep. Warn loudly so the user does not believe discrete
        # divs were applied when they were not.
        if ql_dividend_schedule and not config.option_type.startswith("american_"):
            logger.warning(
                "dividend_schedule supplied for %s but only American options "
                "consume discrete divs; ignoring. Use dividend_yield for "
                "continuous q on this product.",
                config.option_type,
            )

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
            # Barrier monitoring frequency — defaults to "continuous" so the
            # analytic engine result is unchanged for callers that don't set
            # it; daily/weekly/monthly trigger the BGK shift in the engine.
            "monitoring": config.monitoring,
            # Discrete cash dividends (American only). Empty/None routes to
            # the continuous-yield path; non-empty routes to FDM with QL
            # DividendSchedule.
            "dividend_schedule": ql_dividend_schedule,
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
        # Phase 7 — surface staleness tracking. Captures the wall-clock time
        # of the surface build so the response can carry an age in seconds;
        # UI surfaces this as a "stale" badge when > 60s.
        surface_built_at: Optional[float] = None
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
                    # Stamp the build wall-clock so the response can advertise
                    # surface age. Captured here whether status is "ok" or
                    # "suspect" — a suspect surface still has a meaningful age.
                    surface_built_at = time.time()
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
                     "barrier_level", "monitoring", "dividend_schedule",
                     "vol_handle", "use_local_vol_pde",
                     "averaging_method", "averaging_frequency", "lookback_type"]
        }
        greeks = greeks_func(**greeks_params)
        # Pin-risk flag is meta, not a Greek — pop it out so the dict the
        # report sees only has numeric values, and lift it onto the response
        # object below. ``False`` is the default so non-barrier products
        # don't accidentally inherit a pin_risk=True from a stale state.
        pin_risk = bool(greeks.pop("pin_risk", False))

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

        # Bridge σ rule: only meaningful when the surface was successfully
        # consumed AND the product is a barrier (KO/KI). Anything else (flat
        # σ, surface failed, vanilla product) leaves it None.
        is_barrier = ("knockout" in config.option_type or "knockin" in config.option_type)
        bridge_sigma_rule: Optional[str] = (
            "max(sigma_K, sigma_B)"
            if (surface_status == "ok" and is_barrier)
            else None
        )

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
            pin_risk=pin_risk,
            bridge_sigma_rule=bridge_sigma_rule,
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

        # ------------------------------------------------------------------
        # Phase 7 — XVA overlay, bid/offer quote, cross-Greeks, surface age
        # ------------------------------------------------------------------
        try:
            xva_kwargs = (request.xva_inputs or {}).copy()
            xva_inputs_obj = XVAInputs(
                funding_spread_bps=float(xva_kwargs.get("funding_spread_bps", 50.0)),
                cds_spread_bps=float(xva_kwargs.get("cds_spread_bps", 100.0)),
                recovery=float(xva_kwargs.get("recovery", 0.40)),
                direction=xva_kwargs.get("direction", "buy"),
                csa=bool(xva_kwargs.get("csa", False)),
            )
            xva = compute_xva(
                mid_price=float(price),
                maturity_years=pricing_params["T"],
                inputs=xva_inputs_obj,
            )
            result.xva_overlay = xva.to_dict()
            # Bid/offer derived from mid + xva. Spread expressed in bps of mid
            # when mid is non-trivial, else bps of the strike (a stable
            # reference for digital / variance products whose "price" is a
            # rate, not a USD value).
            mid = float(price)
            ref = mid if abs(mid) > 1e-3 else float(pricing_params["K"])
            if ref > 0:
                spread_bps = (xva.ask_price - xva.bid_price) / ref * 10_000.0
            else:
                spread_bps = 0.0
            result.quote_bid = float(xva.bid_price)
            result.quote_offer = float(xva.ask_price)
            result.quote_spread_bps = float(spread_bps)
        except Exception as exc:  # noqa: BLE001
            logger.warning("XVA overlay failed: %s", exc)

        # Cross-Greeks (vanna/volga). Auto-on for KO/KI; explicit for others.
        is_barrier_product = (
            "knockout" in config.option_type or "knockin" in config.option_type
        )
        if request.compute_vanna_volga or is_barrier_product:
            try:
                S = pricing_params["S"]
                _sigma = float(pricing_params["sigma"])
                _r = float(pricing_params["r"])
                _T = float(pricing_params["T"])
                _q = float(pricing_params["q"])
                _K = float(pricing_params["K"])
                _engine_kw = {
                    k: v for k, v in pricing_params.items()
                    if k in ("barrier_level", "monitoring",
                             "averaging_method", "averaging_frequency", "lookback_type")
                    and v is not None
                }

                def _price_at(S_b, sigma_b, _pf=pricer_func, _kw=_engine_kw):
                    p, _, _ = _pf(S_b, _K, _r, sigma_b, _T, _q, **_kw)
                    return float(p)

                cg = compute_vanna_volga(
                    price_fn=_price_at, spot=float(S), sigma=_sigma,
                )
                result.vanna = cg["vanna"]
                result.volga = cg["volga"]
            except Exception as exc:  # noqa: BLE001
                logger.warning("Vanna/volga compute failed: %s", exc)

        # Surface age — only set when a surface was actually built.
        if surface_built_at is not None:
            result.surface_age_seconds = max(time.time() - surface_built_at, 0.0)

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
        monitoring=request.monitoring,
        dividend_schedule=request.dividend_schedule,
        averaging_method=request.averaging_method,
        averaging_frequency=request.averaging_frequency,
        lookback_type=request.lookback_type,
        save_to="./reports/",
    )


def _convert_dividend_schedule(schedule: Optional[List]) -> Optional[List]:
    """Convert a list of [iso_date_str, amount] pairs to [(ql.Date, amount)]
    tuples ready for ``price_american_discrete_div_ql``.

    Returns ``None`` when the input is None/empty so downstream router code
    can use a simple truthiness check to decide between the discrete-div FDM
    engine and the continuous-yield path.

    Raises:
        ValueError: malformed entry (bad ISO string, non-numeric amount, etc).
    """
    if not schedule:
        return None
    try:
        import QuantLib as ql
    except ImportError as exc:
        raise ValueError(
            "QuantLib not available; dividend_schedule requires QL"
        ) from exc

    converted = []
    for i, entry in enumerate(schedule):
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise ValueError(
                f"dividend_schedule[{i}] must be [iso_date_str, amount], got {entry!r}"
            )
        iso, amount = entry
        try:
            d = date.fromisoformat(str(iso))
        except ValueError as exc:
            raise ValueError(
                f"dividend_schedule[{i}] date {iso!r} is not ISO YYYY-MM-DD: {exc}"
            ) from exc
        try:
            amt = float(amount)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"dividend_schedule[{i}] amount {amount!r} is not numeric: {exc}"
            ) from exc
        converted.append((ql.Date(d.day, d.month, d.year), amt))
    return converted


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
