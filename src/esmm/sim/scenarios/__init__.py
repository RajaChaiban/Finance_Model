"""Curated stress scenarios for the simulator.

The scenarios are stored as YAML in ``library.yaml`` so they can be
edited without a deploy. Each scenario specifies:

  * scripted events (gaps, halts, vol explosions, news prints)
  * participant mix overrides
  * latency overrides
  * regime label for attribution

Initial library (phase 4):
  flash_crash_2010, covid_mar_2020, hot_cpi, fomc_surprise,
  opex_pin, liquidity_drought
"""

from src.esmm.sim.scenarios import loader

__all__ = ["loader"]
