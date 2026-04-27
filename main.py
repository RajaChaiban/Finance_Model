"""
Main entry point for the derivatives pricing pipeline.

Usage:
    python main.py --config configs/american_put_spy.yaml
"""

import argparse
import logging
import sys
from pathlib import Path

from src.config.loader import load_config
from src.engines import router
from src.data import market_data
from src.report import generator
from src.analysis.structurer_agent import StructurerReview
from src.analysis.structurer_report import generate_structurer_report

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Run the pricing pipeline."""
    parser = argparse.ArgumentParser(
        description="Derivatives Pricing Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --config configs/american_put_spy.yaml
  python main.py --config configs/european_call_spy.yaml --fetch-market-data
        """
    )

    parser.add_argument(
        "--config",
        required=True,
        help="Path to YAML config file (e.g., configs/american_put_spy.yaml)"
    )
    parser.add_argument(
        "--fetch-market-data",
        action="store_true",
        help="Fetch real market data from Yahoo Finance (requires yfinance)"
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="Skip report generation, just print results to console"
    )
    parser.add_argument(
        "--no-structurer-review",
        action="store_true",
        help="Skip structurer financial analyst review"
    )
    parser.add_argument(
        "--use-vol-surface",
        action="store_true",
        help="Calibrate a live SPY implied-vol surface from the option chain "
             "and price against it (requires --fetch-market-data)."
    )

    args = parser.parse_args()
    if args.use_vol_surface and not args.fetch_market_data:
        # The surface needs a live chain. Auto-enable rather than error.
        logger.info("--use-vol-surface implies --fetch-market-data; enabling.")
        args.fetch_market_data = True

    try:
        # Load config
        print(f"Loading config from: {args.config}")
        config = load_config(args.config)
        print(f"  Option Type: {config.option_type}")
        print(f"  Underlying: {config.underlying}")
        print(f"  Strike: ${config.strike_price:.2f}")
        print(f"  Expiration: {config.days_to_expiration} days")

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

        # Fetch market data if requested
        if args.fetch_market_data:
            logger.info(f"Fetching live market data for {config.underlying}...")
            market_params = market_data.fetch_market_params(
                config.underlying,
                max_retries=3,
                timeout=10
            )

            source = market_params.get("source", "fallback")
            if source == "cache":
                logger.info(f"Using cached market data for {config.underlying}")
            elif source == "api":
                logger.info(f"Successfully fetched live market data for {config.underlying}")
            else:
                logger.warning(
                    f"Could not fetch live market data for {config.underlying}, "
                    f"using config values"
                )

            if market_params["spot_price"]:
                pricing_params["S"] = market_params["spot_price"]
                logger.info(f"  Spot Price: ${market_params['spot_price']:.2f}")
            else:
                logger.info(f"  Spot Price: ${config.spot_price:.2f} (from config)")

            if market_params["volatility_90d"]:
                pricing_params["sigma"] = market_params["volatility_90d"]
                logger.info(f"  Volatility (90d): {market_params['volatility_90d']:.2%}")
            else:
                logger.info(f"  Volatility: {config.volatility:.2%} (from config)")

            if market_params["dividend_yield"]:
                pricing_params["q"] = market_params["dividend_yield"]
                logger.info(f"  Dividend Yield: {market_params['dividend_yield']:.2%}")
            else:
                logger.info(f"  Dividend Yield: {config.dividend_yield:.2%} (from config)")

        # Build live IV surface if requested
        if args.use_vol_surface or config.use_vol_surface:
            try:
                import QuantLib as ql
                from src.api.market_data import fetch_option_chain
                from src.data.iv_grid import build_iv_grid
                from src.data.vol_surface import build_vol_surface, sample_sigma_for_closed_form

                logger.info(
                    f"Calibrating live IV surface for {config.underlying} "
                    f"(max {config.vol_surface_max_expiries} expiries)..."
                )
                chain = fetch_option_chain(
                    config.underlying,
                    max_expiries=config.vol_surface_max_expiries,
                )
                if not chain:
                    logger.warning(
                        "Empty option chain — falling back to flat-vol pricing."
                    )
                else:
                    grid = build_iv_grid(
                        chain,
                        S=pricing_params["S"],
                        r=pricing_params["r"],
                        q=pricing_params["q"],
                    )
                    surface = build_vol_surface(grid)
                    vol_handle = ql.BlackVolTermStructureHandle(surface)
                    pricing_params["vol_handle"] = vol_handle

                    # Closed-form bridge: also override scalar σ with the
                    # smile-aware sample so the hand-coded BS / Reiner-Rubinstein
                    # paths see a smile-relevant σ when QL isn't available.
                    pricing_params["sigma"] = sample_sigma_for_closed_form(
                        surface,
                        K=pricing_params["K"],
                        T=pricing_params["T"],
                        S=pricing_params["S"],
                        barrier=pricing_params.get("barrier_level"),
                    )
                    logger.info(
                        f"  Surface σ at (K={pricing_params['K']:.2f}, "
                        f"T={pricing_params['T']:.4f}): "
                        f"{pricing_params['sigma']:.2%} "
                        f"(grid {grid.n_quotes_inverted}/{grid.n_quotes_total} quotes)."
                    )
            except Exception as exc:
                logger.warning(
                    f"Vol surface build failed ({exc}); falling back to flat vol."
                )

        # Route to appropriate pricing engine
        print(f"\nRouting to pricing engine...")
        pricer_func, greeks_func, method_description = router.route(config.option_type)
        print(f"  Method: {method_description}")

        # Price option
        print(f"\nPricing option...")
        price, std_error, paths = pricer_func(**pricing_params)
        print(f"  Price: ${price:.4f}")
        if std_error:
            print(f"  Standard Error: ${std_error:.4f}")

        # Calculate Greeks
        print(f"Calculating Greeks...")
        greeks_params = {k: v for k, v in pricing_params.items() if k in [
            "S", "K", "r", "sigma", "T", "q", "n_paths", "n_steps",
            "barrier_level", "vol_handle",
        ]}
        greeks = greeks_func(**greeks_params)

        print(f"\nGreeks:")
        for greek, value in greeks.items():
            if greek == "price":
                continue
            if isinstance(value, float):
                print(f"  {greek.upper():20s}: {value:12.6f}")

        # Prepare results
        results = {
            "price": price,
            "std_error": std_error,
            "greeks": greeks,
            "paths": paths,
            "method": method_description,
        }

        # Generate report
        if not args.no_report:
            print(f"\nGenerating HTML report...")
            report_path = generator.generate_report(results, config)
            print(f"  Report saved: {report_path}")
        else:
            print(f"\nSkipping report generation (--no-report flag used).")

        # Generate structurer review
        if not args.no_structurer_review:
            print(f"\nRunning Structurer Financial Analyst Review...")
            structurer = StructurerReview()
            opinion = structurer.analyze(results, config)

            print(f"\nStructurer Recommendation: {opinion.recommendation.replace('_', ' ')}")
            print(f"  Fair Value: ${opinion.fair_value:.4f}")
            if opinion.market_mid:
                print(f"  Market Mid: ${opinion.market_mid:.2f}")
                print(f"  Edge: {opinion.edge_pct:+.2f}%")
            print(f"  Risk Score: {opinion.risk_score}/10")
            print(f"  Probability of Profit: {opinion.probability_of_profit:.1f}%")
            print(f"\n  Action: {opinion.recommended_action}")

            review_path = generate_structurer_report(opinion, config, config.save_to)
            print(f"\n  Structurer Review saved: {review_path}")
        else:
            print(f"\nSkipping structurer review (--no-structurer-review flag used).")

        print(f"\n[OK] Pricing pipeline complete!")

        return 0

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1

    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
