"""
Main entry point for the derivatives pricing pipeline.

Usage:
    python main.py --config configs/american_put_spy.yaml
"""

import argparse
import sys
from pathlib import Path

from src.config.loader import load_config
from src.engines import router
from src.data import market_data
from src.report import generator


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

    args = parser.parse_args()

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
            print(f"\nFetching market data for {config.underlying}...")
            market_params = market_data.fetch_market_params(config.underlying)
            if market_params["spot_price"]:
                pricing_params["S"] = market_params["spot_price"]
                print(f"  Spot Price: ${market_params['spot_price']:.2f}")
            if market_params["volatility_90d"]:
                pricing_params["sigma"] = market_params["volatility_90d"]
                print(f"  Volatility (90d): {market_params['volatility_90d']:.2%}")
            if market_params["dividend_yield"]:
                pricing_params["q"] = market_params["dividend_yield"]
                print(f"  Dividend Yield: {market_params['dividend_yield']:.2%}")

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
        # Filter params for Greeks function (don't pass barrier_level, etc.)
        greeks_params = {k: v for k, v in pricing_params.items() if k in [
            "S", "K", "r", "sigma", "T", "q", "n_paths", "n_steps"
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
            print(f"\nOpen the report in your browser to view detailed analysis and charts.")
        else:
            print(f"\nSkipping report generation (--no-report flag used).")

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
