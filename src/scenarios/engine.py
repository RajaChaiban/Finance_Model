"""
Scenario analysis engine: Stress testing derivatives under extreme conditions.

Answers: "How would this product perform if [market shock]?"
Tests robustness to volatility spikes, crashes, and correlation breaks.
"""

import numpy as np
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
import logging
from src.engines import quantlib_engine

logger = logging.getLogger(__name__)


@dataclass
class Scenario:
    """Definition of a stress scenario."""
    name: str
    description: str
    spot_shock: float  # % change in spot
    vol_shock: float   # % change in volatility
    rate_shock: float  # Absolute change in rates (e.g., 0.02 for +200bps)
    div_shock: float   # % change in dividend yield


class ScenarioLibrary:
    """Pre-defined stress scenarios based on historical crises."""

    SCENARIOS = {
        "crisis_2008": Scenario(
            name="2008 Financial Crisis",
            description="Severe equity crash with vol spike and rate cuts",
            spot_shock=-0.37,      # 37% drop (worst case in crisis)
            vol_shock=2.5,         # Vol tripled
            rate_shock=-0.02,      # Rates cut 200bps
            div_shock=0.5,         # Dividends cut 50%
        ),
        "covid_crash": Scenario(
            name="COVID-19 Crash",
            description="Sharp market drawdown (Feb-Mar 2020)",
            spot_shock=-0.34,      # 34% drop
            vol_shock=3.0,         # Vol spiked 3x
            rate_shock=-0.015,     # Rates cut 150bps
            div_shock=0.3,         # Some dividend cuts
        ),
        "vol_spike": Scenario(
            name="Volatility Spike",
            description="Sudden volatility surge without big move",
            spot_shock=0.0,        # No price move
            vol_shock=1.0,         # Vol doubles
            rate_shock=0.0,        # No rate move
            div_shock=0.0,         # No div move
        ),
        "flash_crash": Scenario(
            name="Flash Crash",
            description="Sharp intraday decline (2010-style)",
            spot_shock=-0.20,      # 20% drop
            vol_shock=1.5,         # Vol spike 50%
            rate_shock=0.0,        # No sustained rate move
            div_shock=0.0,         # Divs stable
        ),
        "rate_shock": Scenario(
            name="Rate Spike",
            description="Sudden rate increase (taper tantrum-style)",
            spot_shock=-0.10,      # Small equity decline
            vol_shock=0.5,         # Vol up slightly
            rate_shock=0.02,       # Rates up 200bps
            div_shock=0.0,         # Divs stable
        ),
        "vol_collapse": Scenario(
            name="Volatility Collapse",
            description="Sharp vol drop (market relief)",
            spot_shock=0.05,       # Small rally
            vol_shock=-0.5,        # Vol cut in half
            rate_shock=0.005,      # Slight rate rise
            div_shock=0.0,         # Divs stable
        ),
    }

    @classmethod
    def get(cls, scenario_name: str) -> Scenario:
        """Get scenario by name."""
        if scenario_name not in cls.SCENARIOS:
            raise ValueError(f"Unknown scenario: {scenario_name}. Available: {list(cls.SCENARIOS.keys())}")
        return cls.SCENARIOS[scenario_name]

    @classmethod
    def list(cls) -> List[str]:
        """List all available scenarios."""
        return list(cls.SCENARIOS.keys())


class StressResult:
    """Container for stress test result."""

    def __init__(self, scenario: Scenario, original_price: float, stressed_price: float,
                 original_greeks: Dict, stressed_greeks: Dict, viable: bool):
        self.scenario = scenario
        self.original_price = original_price
        self.stressed_price = stressed_price
        self.original_greeks = original_greeks
        self.stressed_greeks = stressed_greeks
        self.viable = viable

        # Calculate impacts
        self.price_impact = stressed_price - original_price
        self.price_impact_pct = (self.price_impact / original_price * 100) if original_price > 0 else 0
        self.delta_shock = stressed_greeks.get('delta', 0) - original_greeks.get('delta', 0)
        self.vega_shock = stressed_greeks.get('vega', 0) - original_greeks.get('vega', 0)
        self.gamma_shock = stressed_greeks.get('gamma', 0) - original_greeks.get('gamma', 0)

    def to_dict(self) -> Dict:
        """Convert to dictionary for reporting."""
        return {
            "scenario": self.scenario.name,
            "description": self.scenario.description,
            "original_price": self.original_price,
            "stressed_price": self.stressed_price,
            "price_impact": self.price_impact,
            "price_impact_pct": self.price_impact_pct,
            "delta_shock": self.delta_shock,
            "vega_shock": self.vega_shock,
            "gamma_shock": self.gamma_shock,
            "viable": self.viable,
            "original_greeks": self.original_greeks,
            "stressed_greeks": self.stressed_greeks,
        }

    def __repr__(self):
        return f"""
{self.scenario.name} ({self.scenario.description})
  Original Price:  ${self.original_price:.4f}
  Stressed Price:  ${self.stressed_price:.4f}
  Price Impact:    {self.price_impact_pct:+.2f}% (${self.price_impact:+.4f})
  Delta Shock:     {self.delta_shock:+.4f}
  Vega Shock:      {self.vega_shock:+.4f}
  Viable:          {'YES' if self.viable else 'NO'}
"""


class StressEngine:
    """Stress testing engine for derivatives."""

    def __init__(self, S: float, K: float, r: float, sigma: float, T: float,
                 q: float = 0, option_type: str = 'put', B: float = None):
        """
        Initialize stress engine.

        Args:
            S: Initial spot price
            K: Strike price
            r: Risk-free rate
            sigma: Volatility
            T: Time to expiration (years)
            q: Dividend yield
            option_type: 'call' or 'put'
            B: Barrier level (for knockout options)
        """
        self.S = S
        self.K = K
        self.r = r
        self.sigma = sigma
        self.T = T
        self.q = q
        self.option_type = option_type
        self.B = B
        self.is_knockout = B is not None

    def price_option(self, S: float = None, K: float = None, r: float = None,
                    sigma: float = None, T: float = None, q: float = None) -> Tuple[float, Dict]:
        """
        Price option with optional parameter overrides.

        Returns:
            (price, greeks)
        """
        S = S or self.S
        K = K or self.K
        r = r or self.r
        sigma = sigma or self.sigma
        T = T or self.T
        q = q or self.q

        try:
            if self.is_knockout:
                price, _, _ = quantlib_engine.price_knockout_ql(
                    S, K, self.B, r, sigma, T, q, self.option_type
                )
            else:
                price, _, _ = quantlib_engine.price_american_ql(
                    S, K, r, sigma, T, q, int(T * 100), self.option_type
                )

            greeks = quantlib_engine.greeks_ql(
                S, K, r, sigma, T, q, self.option_type, is_american=not self.is_knockout
            )

            return price, greeks

        except Exception as e:
            logger.error(f"Pricing failed: {e}")
            raise

    def stress_scenario(self, scenario: Scenario) -> StressResult:
        """
        Stress test under a scenario.

        Args:
            scenario: Scenario to test

        Returns:
            StressResult with impacts
        """
        # Original pricing
        original_price, original_greeks = self.price_option()

        # Apply shocks
        stressed_S = self.S * (1 + scenario.spot_shock)
        stressed_r = self.r + scenario.rate_shock
        stressed_sigma = self.sigma * (1 + scenario.vol_shock)
        stressed_q = self.q * (1 + scenario.div_shock)

        # Ensure valid parameters
        stressed_r = max(0.001, stressed_r)  # Keep positive
        stressed_sigma = max(0.05, stressed_sigma)  # Keep above 5%
        stressed_q = max(0, stressed_q)  # Keep non-negative

        # Stressed pricing
        stressed_price, stressed_greeks = self.price_option(
            S=stressed_S,
            r=stressed_r,
            sigma=stressed_sigma,
            q=stressed_q
        )

        # Viability check: position should be hedgeable (not move too dramatically)
        viable = self._check_viability(original_greeks, stressed_greeks, stressed_price)

        return StressResult(
            scenario=scenario,
            original_price=original_price,
            stressed_price=stressed_price,
            original_greeks=original_greeks,
            stressed_greeks=stressed_greeks,
            viable=viable
        )

    def _check_viability(self, original_greeks: Dict, stressed_greeks: Dict,
                        stressed_price: float) -> bool:
        """
        Check if position remains hedgeable (viability check).

        Returns True if:
        - Greeks don't flip signs (direction consistent)
        - Price doesn't go negative (still has value)
        - Gamma not explosive
        """
        original_delta = original_greeks.get('delta', 0)
        stressed_delta = stressed_greeks.get('delta', 0)

        # Delta flip is very bad (means option behavior reversed)
        if (original_delta > 0 and stressed_delta < 0) or (original_delta < 0 and stressed_delta > 0):
            return False

        # Price should not go negative
        if stressed_price < 0:
            return False

        # Gamma shouldn't explode beyond reason
        stressed_gamma = abs(stressed_greeks.get('gamma', 0))
        if stressed_gamma > 0.5:  # Gamma > 0.5 is extreme
            return False

        return True

    def stress_all_scenarios(self) -> Dict[str, StressResult]:
        """
        Stress test against all scenarios.

        Returns:
            Dict mapping scenario names to StressResults
        """
        results = {}

        for scenario_name in ScenarioLibrary.list():
            scenario = ScenarioLibrary.get(scenario_name)
            logger.info(f"Stressing {scenario.name}...")

            try:
                result = self.stress_scenario(scenario)
                results[scenario_name] = result
                logger.info(f"  {scenario.name}: {result.price_impact_pct:+.2f}% price impact")
            except Exception as e:
                logger.error(f"  {scenario.name}: FAILED - {e}")

        return results

    def get_viability_matrix(self, results: Dict[str, StressResult]) -> Dict:
        """
        Generate viability matrix for reporting.

        Returns:
            Dict with scenario -> viability mapping
        """
        matrix = {}
        for scenario_name, result in results.items():
            matrix[scenario_name] = {
                "viable": result.viable,
                "price_impact": result.price_impact_pct,
                "delta_shock": result.delta_shock,
            }
        return matrix
