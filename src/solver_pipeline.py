"""
Structuring pipeline: Use solver to design products meeting client objectives.

Workflow:
1. User specifies: option type, target price, which parameter to solve for
2. Solver finds: parameter value that achieves target price
3. Pipeline: Prices the resulting structure and generates reports
"""

import logging
from pathlib import Path
from typing import Dict, Any, Optional
from src.engines import solver
from src.report import generator
from src.analysis.structurer_agent import StructurerReview
from src.analysis.structurer_report import generate_structurer_report
from src.config.loader import PricingConfig

logger = logging.getLogger(__name__)


def solve_and_structure(config: PricingConfig, target_price: float,
                       solve_for: str = 'strike_price',
                       **solver_kwargs) -> Dict[str, Any]:
    """
    Solve for a parameter given a target price, then price and report the result.

    Args:
        config: PricingConfig object
        target_price: Target option price to achieve
        solve_for: Which parameter to solve for:
                   - 'strike_price' (default)
                   - 'barrier_level' (knockout options only)
                   - 'days_to_expiration'
                   - 'volatility'
        **solver_kwargs: Additional solver options (bounds, tolerance)

    Returns:
        Dictionary with:
        - solution: SolverResult object
        - pricing_result: Price and Greeks of designed structure
        - reports: Paths to generated reports

    Example:
        >>> config = load_config('american_put.yaml')
        >>> result = solve_and_structure(
        ...     config,
        ...     target_price=5.0,
        ...     solve_for='strike_price'
        ... )
        >>> print(f"Designed strike: ${result['solution'].value:.2f}")
    """

    print("=" * 80)
    print("STRUCTURING PIPELINE: SOLVE FOR PARAMETER")
    print("=" * 80)

    print(f"\nObjective: Design {config.option_type} that costs exactly ${target_price:.4f}")
    print(f"Solving for: {solve_for}")
    print(f"Option parameters:")
    print(f"  Spot Price: ${config.spot_price:.2f}")
    print(f"  Current Strike: ${config.strike_price:.2f}")
    print(f"  Days to Expiration: {config.days_to_expiration}")
    print(f"  Volatility: {config.volatility:.2%}")

    # Route to appropriate solver
    print(f"\nRunning solver...")

    try:
        if solve_for == 'strike_price':
            solution = solver.solve_for_strike(
                S=config.spot_price,
                target_price=target_price,
                r=config.risk_free_rate,
                sigma=config.volatility,
                T=config.days_to_expiration / 365.0,
                q=config.dividend_yield,
                option_type=config.option_type.split('_')[1],  # Extract 'put' or 'call'
                **solver_kwargs
            )

        elif solve_for == 'barrier_level':
            if not 'knockout' in config.option_type:
                raise ValueError("Barrier solver only works for knockout options")

            barrier_type = 'down_and_out' if 'put' in config.option_type else 'up_and_out'
            solution = solver.solve_for_barrier(
                S=config.spot_price,
                K=config.strike_price,
                target_price=target_price,
                r=config.risk_free_rate,
                sigma=config.volatility,
                T=config.days_to_expiration / 365.0,
                q=config.dividend_yield,
                option_type=config.option_type.split('_')[1],
                barrier_type=barrier_type,
                **solver_kwargs
            )

        elif solve_for == 'days_to_expiration':
            solution = solver.solve_for_expiration(
                S=config.spot_price,
                K=config.strike_price,
                target_price=target_price,
                r=config.risk_free_rate,
                sigma=config.volatility,
                q=config.dividend_yield,
                option_type=config.option_type.split('_')[1],
                **solver_kwargs
            )

        elif solve_for == 'volatility':
            solution = solver.solve_for_volatility(
                S=config.spot_price,
                K=config.strike_price,
                target_price=target_price,
                r=config.risk_free_rate,
                T=config.days_to_expiration / 365.0,
                q=config.dividend_yield,
                option_type=config.option_type.split('_')[1],
                **solver_kwargs
            )

        else:
            raise ValueError(f"Unknown solve_for parameter: {solve_for}")

    except Exception as e:
        logger.error(f"Solver failed: {e}")
        raise

    # Print solution
    print(f"\n{solution}")

    # Now price the designed structure
    print(f"\n" + "=" * 80)
    print("PRICING THE DESIGNED STRUCTURE")
    print("=" * 80)

    # Update config with solved parameter
    if solve_for == 'strike_price':
        config.strike_price = solution.value
    elif solve_for == 'barrier_level':
        config.barrier_level = solution.value
    elif solve_for == 'days_to_expiration':
        config.days_to_expiration = int(solution.value)
    elif solve_for == 'volatility':
        config.volatility = solution.value

    # Price the resulting structure
    from src.engines import router

    pricer_func, greeks_func, method_description = router.route(config.option_type)

    pricing_params = {
        "S": config.spot_price,
        "K": config.strike_price,
        "r": config.risk_free_rate,
        "sigma": config.volatility,
        "T": config.days_to_expiration / 365.0,
        "q": config.dividend_yield,
        "n_paths": config.n_paths,
        "n_steps": config.n_steps,
        "barrier_level": config.barrier_level,
    }

    price, std_error, paths = pricer_func(**pricing_params)
    print(f"Price: ${price:.4f}")
    if std_error:
        print(f"Standard Error: ${std_error:.4f}")

    # Calculate Greeks
    greeks_params = {k: v for k, v in pricing_params.items() if k in [
        "S", "K", "r", "sigma", "T", "q", "n_paths", "n_steps"
    ]}
    greeks = greeks_func(**greeks_params)

    print(f"Greeks:")
    for greek, value in greeks.items():
        if greek != "price" and isinstance(value, float):
            print(f"  {greek.upper():20s}: {value:12.6f}")

    # Generate reports
    print(f"\n" + "=" * 80)
    print("GENERATING REPORTS")
    print("=" * 80)

    results = {
        "price": price,
        "std_error": std_error,
        "greeks": greeks,
        "paths": paths,
        "method": method_description,
    }

    report_path = generator.generate_report(results, config)
    print(f"Pricing Report: {report_path}")

    # Structurer review
    structurer = StructurerReview()
    opinion = structurer.analyze(results, config)

    print(f"\nStructurer Recommendation: {opinion.recommendation.replace('_', ' ')}")
    print(f"  Fair Value: ${opinion.fair_value:.4f}")
    print(f"  Risk Score: {opinion.risk_score}/10")

    structurer_path = generate_structurer_report(opinion, config, config.save_to)
    print(f"Structurer Review: {structurer_path}")

    return {
        "solution": solution,
        "pricing_result": results,
        "config": config,
        "reports": {
            "pricing": report_path,
            "structurer": structurer_path
        }
    }
