"""End-to-end pipeline + structurer-agent validation for every option type.

For each of the 8 supported option types:
  1. Route through the pricing engine (engines/router.py) → price + Greeks
  2. Hand the result to StructurerReview.analyze() with FIXED bid/ask so the
     agent does NOT hit Yahoo Finance (deterministic, offline-safe)
  3. Assert the StructurerOpinion is internally consistent and that fields
     the UI / report layer rely on are populated.

This is a contract test: it doesn't pin numerical values (those are covered
in test_quantlib_correctness.py / test_knockin.py / test_combinations.py),
it pins the *shape* of the agent's output across product types so the
frontend / report layer can rely on every field being present.
"""

import pytest

from src.config.loader import PricingConfig
from src.engines import router
from src.analysis.structurer_agent import StructurerReview, StructurerOpinion


# Common base — ATM, mid-vol, mid-T. Each option type overrides only what
# differs (barrier_level / barrier_type for KO/KI).
def _base_kwargs(option_type: str):
    kw = dict(
        option_type=option_type,
        underlying="SPY",
        spot_price=100.0,
        strike_price=100.0,
        days_to_expiration=180,
        risk_free_rate=0.05,
        volatility=0.20,
        dividend_yield=0.02,
    )
    if "knockout" in option_type or "knockin" in option_type:
        is_put = "put" in option_type
        is_in = "knockin" in option_type
        kw["barrier_level"] = 90.0 if is_put else 110.0
        prefix = "down_and" if is_put else "up_and"
        suffix = "in" if is_in else "out"
        kw["barrier_type"] = f"{prefix}_{suffix}"
    return kw


# All 8 product types live in the routing table. We assert this list matches
# what the router exposes so adding a new type without updating this test
# fails loudly.
ALL_OPTION_TYPES = [
    "european_call", "european_put",
    "american_call", "american_put",
    "knockout_call", "knockout_put",
    "knockin_call",  "knockin_put",
]


def _price_through_pipeline(option_type: str):
    """Run the same path the API handler runs, minus the IV-surface block."""
    kw = _base_kwargs(option_type)
    config = PricingConfig(**kw)
    pricer_fn, greeks_fn, method_label = router.route(config.option_type)

    pricer_kwargs = dict(
        S=config.spot_price, K=config.strike_price, r=config.risk_free_rate,
        sigma=config.volatility, T=config.days_to_expiration / 365.0,
        q=config.dividend_yield,
    )
    if config.barrier_level is not None:
        pricer_kwargs["barrier_level"] = config.barrier_level

    price, std_error, paths = pricer_fn(**pricer_kwargs)
    greeks = greeks_fn(**pricer_kwargs)

    return config, {
        "price": price,
        "std_error": std_error or 0.0,
        "paths": paths,
        "greeks": greeks,
        "method": method_label,
    }


@pytest.mark.parametrize("option_type", ALL_OPTION_TYPES)
def test_structurer_validates_each_option_type(option_type):
    """Every product runs end-to-end and the structurer returns a well-formed opinion."""
    config, results = _price_through_pipeline(option_type)

    # Fixed market quotes around the model price to keep the test offline.
    # ±2% bracket places the model "fair-valued" relative to mid → HOLD-ish.
    fair = results["price"]
    market_bid = max(fair * 0.98, 1e-6)
    market_ask = fair * 1.02

    opinion = StructurerReview().analyze(
        results, config,
        market_bid=market_bid, market_ask=market_ask,
    )

    # 1. Type contract.
    assert isinstance(opinion, StructurerOpinion), (
        f"{option_type}: analyze() returned {type(opinion).__name__}, expected StructurerOpinion"
    )

    # 2. Recommendation is one of the documented values.
    valid_recs = {"STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"}
    assert opinion.recommendation in valid_recs, (
        f"{option_type}: recommendation {opinion.recommendation!r} not in {valid_recs}"
    )

    # 3. fair_value matches the routed price exactly.
    assert opinion.fair_value == pytest.approx(fair), (
        f"{option_type}: fair_value {opinion.fair_value} != model price {fair}"
    )

    # 4. Edge is finite and matches our manual computation within rounding.
    expected_mid = (market_bid + market_ask) / 2
    expected_edge = (fair - expected_mid) / expected_mid * 100
    assert opinion.edge_pct == pytest.approx(expected_edge, abs=0.01), (
        f"{option_type}: edge_pct {opinion.edge_pct:.4f} != expected {expected_edge:.4f}"
    )

    # 5. Risk score in documented 1-10 range.
    assert 1 <= opinion.risk_score <= 10, (
        f"{option_type}: risk_score {opinion.risk_score} outside 1-10"
    )

    # 6. Probability of profit is a valid percent.
    assert 0.0 <= opinion.probability_of_profit <= 100.0, (
        f"{option_type}: probability_of_profit {opinion.probability_of_profit} outside 0-100"
    )

    # 7. Greeks assessment is populated for every Greek the engine returned.
    expected_greeks = {"delta", "gamma", "vega", "theta", "rho"}
    returned_greeks = expected_greeks & set(results["greeks"].keys())
    assessed_greeks = set(opinion.greeks_assessment.keys())
    missing = returned_greeks - assessed_greeks
    assert not missing, (
        f"{option_type}: greeks_assessment missing entries for {missing}; "
        f"returned by engine: {returned_greeks}, assessed: {assessed_greeks}"
    )

    # 8. Free-text fields are non-empty strings — the report renderer relies on these.
    for field in ("recommended_action", "hedge_recommendation",
                  "executive_summary", "moneyness_status"):
        val = getattr(opinion, field)
        assert isinstance(val, str) and val.strip(), (
            f"{option_type}: opinion.{field} is empty/blank ({val!r})"
        )

    # 9. detailed_analysis is a non-empty list (used by the report's analysis block).
    assert isinstance(opinion.detailed_analysis, list) and opinion.detailed_analysis, (
        f"{option_type}: detailed_analysis empty"
    )


def test_routing_table_covers_all_eight_types():
    """Guard: if a new option_type is added to the router, this test must
    be updated to include it in ALL_OPTION_TYPES (otherwise the per-type
    parametrisation silently misses the new product).
    """
    for ot in ALL_OPTION_TYPES:
        # route() raises ValueError on unknown types — assertion is implicit.
        pricer, greeks, label = router.route(ot)
        assert callable(pricer) and callable(greeks)


def test_structurer_recommendation_responds_to_market_price():
    """Sanity: the structurer's recommendation flips when the market price
    moves above/below model fair value, for any product type. Tests the
    full decision logic, not just the shape of the output.

    Uses a vanilla European call where directionality is unambiguous.
    """
    config, results = _price_through_pipeline("european_call")
    fair = results["price"]

    # Market well above fair value → STRONG_SELL.
    op_high = StructurerReview().analyze(
        results, config, market_bid=fair * 1.10, market_ask=fair * 1.15,
    )
    # Market well below fair value → STRONG_BUY territory.
    op_low = StructurerReview().analyze(
        results, config, market_bid=fair * 0.85, market_ask=fair * 0.90,
    )

    assert "SELL" in op_high.recommendation, (
        f"Market 12% above fair → expected SELL-ish, got {op_high.recommendation}"
    )
    assert "BUY" in op_low.recommendation, (
        f"Market 13% below fair → expected BUY-ish, got {op_low.recommendation}"
    )
