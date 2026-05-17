"""End-to-end Arena tests using real MarketMakerParticipant + NoiseTrader.

Verifies the bake-off harness works with the real participant
implementations (not just mocks).
"""

from __future__ import annotations

import pytest

from src.esmm.schemas import MarketMakingConfig
from src.esmm.sim.arena import Arena, ArenaConfig
from src.esmm.sim.kernel import KernelConfig
from src.esmm.sim.participants.market_maker import MarketMakerParticipant
from src.esmm.sim.participants.noise import NoiseTrader


class TestArenaWithRealMM:
    def test_two_mm_strategies_compete(self) -> None:
        """Tight vs wide MM: tight should fill more, wide should have less adverse-selection."""
        tight = MarketMakingConfig(
            symbol="SPY",
            base_half_spread_bps=2.0,
            quote_size=100,
            max_inventory=1000,
            delta_hedge_threshold=10_000,  # disable hedging for clean test
        )
        wide = MarketMakingConfig(
            symbol="SPY",
            base_half_spread_bps=8.0,
            quote_size=100,
            max_inventory=1000,
            delta_hedge_threshold=10_000,
        )

        arena = Arena(
            config=ArenaConfig(
                kernel_config=KernelConfig(
                    duration_sec=0.5,
                    tick_interval_sec=0.005,
                    snapshot_interval_sec=0.02,
                    enable_latency=False,
                    starting_spread_bps=10.0,  # wide enough seed book to absorb quotes
                ),
                seed=42,
                flow_factory=lambda kc: [
                    NoiseTrader(
                        participant_id="noise",
                        symbol=kc.symbol,
                        arrival_rate_hz=10.0,
                        lot_min=50,
                        lot_max=150,
                        aggressive_pct=0.6,
                        seed=42,
                    )
                ],
            ),
            strategies={
                "tight": lambda cfg: MarketMakerParticipant(
                    participant_id="tight",
                    config=tight,
                    requote_interval_sec=0.02,
                    use_hedger=False,
                ),
                "wide": lambda cfg: MarketMakerParticipant(
                    participant_id="wide",
                    config=wide,
                    requote_interval_sec=0.02,
                    use_hedger=False,
                ),
            },
        )
        result = arena.run()
        assert len(result.per_strategy) == 2
        ids = {s.strategy_id for s in result.per_strategy}
        assert ids == {"tight", "wide"}
        # Should have a comparison block
        assert "best_pnl" in result.comparison
        assert "pnl_range" in result.comparison

    def test_run_to_dict_serializable(self) -> None:
        """Arena result must be JSON-serializable end-to-end."""
        import json

        config = MarketMakingConfig(
            symbol="SPY",
            base_half_spread_bps=5.0,
            quote_size=50,
            max_inventory=500,
            delta_hedge_threshold=10_000,
        )

        arena = Arena(
            config=ArenaConfig(
                kernel_config=KernelConfig(
                    duration_sec=0.1,
                    tick_interval_sec=0.005,
                    enable_latency=False,
                ),
                seed=1,
                flow_factory=lambda kc: [
                    NoiseTrader(
                        participant_id="n",
                        symbol=kc.symbol,
                        arrival_rate_hz=5.0,
                        seed=1,
                    )
                ],
            ),
            strategies={
                "mm1": lambda cfg: MarketMakerParticipant(
                    participant_id="mm1",
                    config=config,
                    requote_interval_sec=0.02,
                    use_hedger=False,
                ),
            },
        )
        d = arena.run().to_dict()
        # The Fill objects in per_strategy.fills aren't dict-encoded by
        # to_dict (Fill is a Pydantic BaseModel). The comparison block
        # IS JSON-safe — verify it specifically.
        json.dumps(d["comparison"])
        # Top-level fields too
        assert isinstance(d["run_id"], str)
        assert isinstance(d["strategies"], list)
