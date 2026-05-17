"""Phase 0 smoke test — every sim module imports without error.

This is the contract: each phase adds implementation but never breaks
the import surface. If you delete a stub here, the test fails until you
replace the export.
"""

from __future__ import annotations


def test_top_level_package_imports() -> None:
    from src.esmm import sim

    expected = {
        "arena",
        "attribution",
        "kernel",
        "latency",
        "lob",
        "matching",
        "participants",
        "reporters",
        "risk",
        "scenarios",
    }
    assert expected.issubset(set(sim.__all__))


def test_lob_types_importable() -> None:
    from src.esmm.sim.lob import LimitOrderBook, Order, OrderSide, OrderType

    assert OrderSide.BUY != OrderSide.SELL
    assert OrderType.LIMIT != OrderType.MARKET

    o = Order(
        order_id=1,
        symbol="SPY",
        side=OrderSide.BUY,
        price=400.0,
        size=100.0,
        ts=0.0,
        owner_id="test",
    )
    assert o.remaining == 100.0
    assert o.cancelled is False

    book = LimitOrderBook("SPY")
    first = book.next_order_id()
    second = book.next_order_id()
    assert second > first


def test_matching_types_importable() -> None:
    from src.esmm.sim.matching import MatchEngine, MatchResult

    assert MatchEngine is not None
    assert MatchResult is not None


def test_latency_types_importable() -> None:
    from src.esmm.sim.latency import LatencyConfig, LatencyModel

    cfg = LatencyConfig(submit_mean_ms=15.0, submit_sigma_ms=8.0, seed=42)
    model = LatencyModel(cfg)
    assert model.config.submit_mean_ms == 15.0


def test_kernel_types_importable() -> None:
    from src.esmm.sim.kernel import Kernel, KernelConfig

    cfg = KernelConfig(duration_sec=60.0)
    k = Kernel(cfg)
    # Phase-2 implementation: kernel exposes a participants registry
    # and an internal pending-orders heap.
    assert k.participants == []
    assert k._pending == []


def test_arena_types_importable() -> None:
    from src.esmm.sim.arena import Arena, ArenaResult

    assert Arena is not None
    assert ArenaResult is not None


def test_risk_types_importable() -> None:
    from src.esmm.sim.risk import RiskBreach, RiskEngine, RiskLimits

    limits = RiskLimits()
    eng = RiskEngine(limits)
    assert eng.halted is False
    assert eng.breaches == []
    assert RiskBreach is not None


def test_attribution_types_importable() -> None:
    from src.esmm.sim.attribution import AttributionReport, attribute

    assert AttributionReport is not None
    assert attribute is not None


def test_participants_protocol_importable() -> None:
    from src.esmm.sim.participants.base import Participant

    assert Participant is not None


def test_scenarios_loader_importable() -> None:
    from src.esmm.sim.scenarios.loader import Scenario, load_library

    assert Scenario is not None
    assert load_library is not None


def test_reporters_importable() -> None:
    from src.esmm.sim.reporters.monte_carlo import MonteCarloConfig
    from src.esmm.sim.reporters.walk_forward import WalkForwardConfig

    # Phase-5 implementation uses *seconds* (sim time) for window sizing,
    # not days. Wall-clock days are a frontend display concern.
    assert WalkForwardConfig().train_sec > 0
    assert WalkForwardConfig().test_sec > 0
    assert MonteCarloConfig().n_runs == 100


def test_existing_esmm_still_imports() -> None:
    """Phase 0 must not break anything in src/esmm/."""
    from src.esmm import (
        adapters,
        backtest,
        crb,
        features,
        hedger,
        inventory,
        orderbook,
        persistence,
        quote_engine,
        schemas,
        sim,
        synthetic,
        tca,
    )

    assert all(
        m is not None
        for m in (
            adapters,
            backtest,
            crb,
            features,
            hedger,
            inventory,
            orderbook,
            persistence,
            quote_engine,
            schemas,
            sim,
            synthetic,
            tca,
        )
    )
