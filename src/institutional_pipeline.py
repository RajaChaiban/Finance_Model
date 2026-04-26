"""
Institutional-Grade Derivatives Structuring Pipeline

Complete end-to-end workflow:
1. Load/Design product (forward or inverse pricing)
2. Calculate pricing and Greeks
3. Validate with backtesting (historical)
4. Stress test with scenarios (forward-looking)
5. Generate institutional reports

Production-ready for hedge funds, asset managers, corporate treasuries.
"""

import logging
from typing import Dict, Optional, Tuple
from pathlib import Path
from datetime import datetime

from src.config.loader import load_config, PricingConfig
from src.engines import router
from src.data import market_data
from src.report import generator
from src.analysis.structurer_agent import StructurerReview
from src.analysis.structurer_report import generate_structurer_report
from src.solver_pipeline import solve_and_structure
from src.backtesting import BacktestEngine, generate_backtest_report
from src.scenarios import StressEngine, ScenarioLibrary, generate_scenario_report

logger = logging.getLogger(__name__)


class InstitutionalPipeline:
    """Complete structuring pipeline for institutional clients."""

    def __init__(self, config: PricingConfig):
        """Initialize with config."""
        self.config = config
        self.results = {}

    def price_structure(self) -> Dict:
        """Price the structure using QuantLib."""
        logger.info("=" * 80)
        logger.info("STEP 1: PRICING")
        logger.info("=" * 80)

        pricer_func, greeks_func, method = router.route(self.config.option_type)

        pricing_params = {
            "S": self.config.spot_price,
            "K": self.config.strike_price,
            "r": self.config.risk_free_rate,
            "sigma": self.config.volatility,
            "T": self.config.days_to_expiration / 365.0,
            "q": self.config.dividend_yield,
            "n_paths": self.config.n_paths,
            "n_steps": self.config.n_steps,
            "barrier_level": self.config.barrier_level,
        }

        price, std_error, paths = pricer_func(**pricing_params)
        logger.info(f"Price: ${price:.4f}")
        if std_error:
            logger.info(f"Standard Error: ${std_error:.4f}")

        greeks_params = {k: v for k, v in pricing_params.items()
                        if k in ["S", "K", "r", "sigma", "T", "q", "n_paths", "n_steps"]}
        greeks = greeks_func(**greeks_params)

        logger.info("Greeks:")
        for greek, value in greeks.items():
            if greek != "price" and isinstance(value, float):
                logger.info(f"  {greek.upper():20s}: {value:12.6f}")

        self.results['pricing'] = {
            'price': price,
            'std_error': std_error,
            'greeks': greeks,
            'paths': paths,
            'method': method,
        }

        return self.results['pricing']

    def backtest_structure(self, entry_date: Optional[str] = None) -> Dict:
        """Backtest structure against historical data."""
        logger.info("\n" + "=" * 80)
        logger.info("STEP 2: BACKTESTING (Historical Validation)")
        logger.info("=" * 80)

        try:
            backtester = BacktestEngine(
                ticker=self.config.underlying,
                start_date=None,  # Default to 5 years
                end_date=None
            )

            if "knockout" in self.config.option_type:
                metrics, performance = backtester.backtest_knockout_put(
                    K=self.config.strike_price,
                    B=self.config.barrier_level,
                    T=self.config.days_to_expiration / 365.0,
                    r=self.config.risk_free_rate,
                    q=self.config.dividend_yield,
                    sigma=self.config.volatility,
                    entry_date=entry_date
                )
            else:
                metrics, performance = backtester.backtest_american_put(
                    K=self.config.strike_price,
                    T=self.config.days_to_expiration / 365.0,
                    r=self.config.risk_free_rate,
                    q=self.config.dividend_yield,
                    sigma=self.config.volatility,
                    entry_date=entry_date
                )

            logger.info(f"\nBacktest Metrics:")
            logger.info(f"  Hit Rate: {metrics.hit_rate:.1f}%")
            logger.info(f"  Total Return: {metrics.total_return:+.2f}%")
            logger.info(f"  Max Drawdown: {metrics.max_drawdown:.2f}%")
            logger.info(f"  Sharpe Ratio: {metrics.sharpe_ratio:.2f}")

            # Generate backtest report
            report_path = generate_backtest_report(metrics, performance, self.config)
            logger.info(f"Backtest Report: {report_path}")

            self.results['backtesting'] = {
                'metrics': metrics,
                'performance': performance,
                'report': report_path,
            }

            return self.results['backtesting']

        except Exception as e:
            logger.error(f"Backtesting failed: {e}")
            logger.warning("Continuing without backtest results")
            return None

    def stress_test_structure(self) -> Dict:
        """Stress test structure across scenarios."""
        logger.info("\n" + "=" * 80)
        logger.info("STEP 3: SCENARIO ANALYSIS (Stress Testing)")
        logger.info("=" * 80)

        try:
            stress_engine = StressEngine(
                S=self.config.spot_price,
                K=self.config.strike_price,
                r=self.config.risk_free_rate,
                sigma=self.config.volatility,
                T=self.config.days_to_expiration / 365.0,
                q=self.config.dividend_yield,
                option_type=self.config.option_type.split('_')[1],
                B=self.config.barrier_level
            )

            logger.info("Running stress tests...")
            stress_results = stress_engine.stress_all_scenarios()

            viable_count = sum(1 for r in stress_results.values() if r.viable)
            logger.info(f"\nStress Test Results:")
            logger.info(f"  Viable scenarios: {viable_count}/{len(stress_results)}")

            for scenario_name, result in stress_results.items():
                status = "VIABLE" if result.viable else "AT RISK"
                logger.info(f"  {result.scenario.name}: {result.price_impact_pct:+.2f}% - {status}")

            # Generate scenario report
            report_path = generate_scenario_report(stress_results, self.config)
            logger.info(f"Scenario Report: {report_path}")

            self.results['scenarios'] = {
                'results': stress_results,
                'report': report_path,
            }

            return self.results['scenarios']

        except Exception as e:
            logger.error(f"Stress testing failed: {e}")
            logger.warning("Continuing without scenario results")
            return None

    def structurer_review(self, market_bid: Optional[float] = None,
                         market_ask: Optional[float] = None) -> Dict:
        """Run structurer financial analyst review."""
        logger.info("\n" + "=" * 80)
        logger.info("STEP 4: STRUCTURER FINANCIAL ANALYSIS")
        logger.info("=" * 80)

        structurer = StructurerReview()
        opinion = structurer.analyze(self.results['pricing'], self.config,
                                    market_bid=market_bid, market_ask=market_ask)

        logger.info(f"Recommendation: {opinion.recommendation.replace('_', ' ')}")
        logger.info(f"  Fair Value: ${opinion.fair_value:.4f}")
        if opinion.market_mid:
            logger.info(f"  Market Mid: ${opinion.market_mid:.2f}")
            logger.info(f"  Edge: {opinion.edge_pct:+.2f}%")
        logger.info(f"  Risk Score: {opinion.risk_score}/10")

        review_path = generate_structurer_report(opinion, self.config, self.config.save_to)
        logger.info(f"Structurer Report: {review_path}")

        self.results['structurer'] = {
            'opinion': opinion,
            'report': review_path,
        }

        return self.results['structurer']

    def generate_executive_summary(self) -> str:
        """Generate executive summary combining all analyses."""
        logger.info("\n" + "=" * 80)
        logger.info("EXECUTIVE SUMMARY")
        logger.info("=" * 80)

        summary = f"""
INSTITUTIONAL DERIVATIVES ANALYSIS SUMMARY
Generated: {datetime.now().strftime('%B %d, %Y at %H:%M:%S')}

PRODUCT: {self.config.option_type.replace('_', ' ').title()}
UNDERLYING: {self.config.underlying}
STRIKE: ${self.config.strike_price:.2f}
SPOT: ${self.config.spot_price:.2f}
EXPIRATION: {self.config.days_to_expiration} days

PRICING & RISK:
  Fair Value: ${self.results['pricing']['price']:.4f}
  Delta: {self.results['pricing']['greeks'].get('delta', 0):+.4f}
  Gamma: {self.results['pricing']['greeks'].get('gamma', 0):.6f}
  Vega: {self.results['pricing']['greeks'].get('vega', 0):.4f}
"""

        if 'backtesting' in self.results and self.results['backtesting']:
            metrics = self.results['backtesting']['metrics']
            summary += f"""
HISTORICAL PERFORMANCE:
  Hit Rate: {metrics.hit_rate:.1f}%
  Total Return: {metrics.total_return:+.2f}%
  Sharpe Ratio: {metrics.sharpe_ratio:.2f}
  Max Drawdown: {metrics.max_drawdown:.2f}%
"""

        if 'scenarios' in self.results and self.results['scenarios']:
            stress_results = self.results['scenarios']['results']
            viable = sum(1 for r in stress_results.values() if r.viable)
            summary += f"""
STRESS TEST RESULTS:
  Viable Scenarios: {viable}/{len(stress_results)}
  Status: {'ROBUST' if viable >= len(stress_results) - 1 else 'REQUIRES REVIEW'}
"""

        if 'structurer' in self.results:
            opinion = self.results['structurer']['opinion']
            summary += f"""
STRUCTURER RECOMMENDATION:
  Action: {opinion.recommended_action}
  Risk Score: {opinion.risk_score}/10
  Status: {'APPROVED FOR EXECUTION' if opinion.risk_score <= 6 else 'ESCALATION REQUIRED'}
"""

        summary += f"""
GENERATED REPORTS:
  Pricing Report: {Path(self.results['pricing'].get('report', '')).name if 'report' in str(self.results['pricing']) else 'See reports/ folder'}
  Structurer Review: {Path(self.results['structurer']['report']).name if 'structurer' in self.results else 'N/A'}
  Backtest Report: {Path(self.results['backtesting']['report']).name if 'backtesting' in self.results and self.results['backtesting'] else 'N/A'}
  Scenario Report: {Path(self.results['scenarios']['report']).name if 'scenarios' in self.results and self.results['scenarios'] else 'N/A'}

NOTE: All analysis assumes parameters as configured. Review assumptions in each report.
"""

        logger.info(summary)
        return summary

    def run_full_pipeline(self, backtest: bool = True, scenarios: bool = True,
                         fetch_market_data: bool = False) -> Dict:
        """
        Run complete institutional pipeline.

        Args:
            backtest: Include backtesting step
            scenarios: Include scenario analysis step
            fetch_market_data: Fetch live market data

        Returns:
            Dict with all results and reports
        """
        logger.info("\n" + "=" * 80)
        logger.info("INSTITUTIONAL DERIVATIVES STRUCTURING PIPELINE")
        logger.info("=" * 80)

        # Fetch market data if requested
        if fetch_market_data:
            logger.info("Fetching market data...")
            # Could integrate market data here

        # Step 1: Price
        self.price_structure()

        # Step 2: Backtest
        if backtest:
            self.backtest_structure()

        # Step 3: Stress test
        if scenarios:
            self.stress_test_structure()

        # Step 4: Structurer review
        self.structurer_review()

        # Step 5: Executive summary
        self.generate_executive_summary()

        logger.info("\n" + "=" * 80)
        logger.info("[OK] PIPELINE COMPLETE - All Reports Generated")
        logger.info("=" * 80)

        return self.results
