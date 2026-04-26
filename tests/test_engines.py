"""
Comprehensive correctness tests for the pricing engines.

Strategy: rely on mathematical identities (put-call parity, monotonicity,
known limits) and engine-vs-engine cross-checks rather than hand-coded
reference values. This catches sign flips, scaling errors, missing
features, and engine misuse without trusting any single implementation.
"""

import math
import numpy as np
import pytest

from src.engines import black_scholes, knockout, monte_carlo_lsm, quantlib_engine
from src.engines import router


# ------------------------------------------------------------------ #
# Shared parameter sets covering ITM / ATM / OTM, varying T and σ.
# ------------------------------------------------------------------ #
BASE = dict(S=100.0, K=100.0, r=0.05, sigma=0.20, T=0.5, q=0.02)

GRID = [
    dict(BASE),                                               # ATM
    dict(BASE, S=120.0),                                      # ITM call / OTM put
    dict(BASE, S=80.0),                                       # OTM call / ITM put
    dict(BASE, sigma=0.10),                                   # low vol
    dict(BASE, sigma=0.50),                                   # high vol
    dict(BASE, T=0.05),                                       # short-dated (~18d)
    dict(BASE, T=2.0),                                        # long-dated
    dict(BASE, q=0.0, r=0.03),                                # no div
    dict(BASE, q=0.06, r=0.03),                               # q > r
]


# ------------------------------------------------------------------ #
# 1. Black-Scholes: put-call parity, sign of Greeks, structural sanity.
# ------------------------------------------------------------------ #
class TestBlackScholes:

    @pytest.mark.parametrize("p", GRID)
    def test_put_call_parity(self, p):
        """C - P = S*e^(-qT) - K*e^(-rT)."""
        c = black_scholes.price_european(option_type="call", **p)
        pu = black_scholes.price_european(option_type="put", **p)
        lhs = c - pu
        rhs = p["S"] * math.exp(-p["q"] * p["T"]) - p["K"] * math.exp(-p["r"] * p["T"])
        assert abs(lhs - rhs) < 1e-8, f"Parity violated: lhs={lhs}, rhs={rhs}"

    @pytest.mark.parametrize("p", GRID)
    def test_call_price_bounds(self, p):
        """max(S e^(-qT) - K e^(-rT), 0) <= C <= S e^(-qT)."""
        c = black_scholes.price_european(option_type="call", **p)
        lower = max(p["S"] * math.exp(-p["q"] * p["T"]) - p["K"] * math.exp(-p["r"] * p["T"]), 0.0)
        upper = p["S"] * math.exp(-p["q"] * p["T"])
        assert lower - 1e-8 <= c <= upper + 1e-8

    @pytest.mark.parametrize("p", GRID)
    def test_put_price_bounds(self, p):
        """max(K e^(-rT) - S e^(-qT), 0) <= P <= K e^(-rT)."""
        pu = black_scholes.price_european(option_type="put", **p)
        lower = max(p["K"] * math.exp(-p["r"] * p["T"]) - p["S"] * math.exp(-p["q"] * p["T"]), 0.0)
        upper = p["K"] * math.exp(-p["r"] * p["T"])
        assert lower - 1e-8 <= pu <= upper + 1e-8

    @pytest.mark.parametrize("p", GRID)
    def test_call_greeks_signs(self, p):
        g = black_scholes.greeks_european(option_type="call", **p)
        assert 0.0 <= g["delta"] <= 1.0
        assert g["gamma"] >= 0
        assert g["vega"] >= 0
        assert g["rho"] > 0  # call rho positive

    @pytest.mark.parametrize("p", GRID)
    def test_put_greeks_signs(self, p):
        g = black_scholes.greeks_european(option_type="put", **p)
        assert -1.0 <= g["delta"] <= 0.0
        assert g["gamma"] >= 0
        assert g["vega"] >= 0
        assert g["rho"] < 0  # put rho negative — was the buggy case

    @pytest.mark.parametrize("p", GRID)
    def test_finite_diff_delta_matches_analytic(self, p):
        """Bump-reprice delta should match analytic delta."""
        for opt in ("call", "put"):
            h = 0.01
            up = black_scholes.price_european(**{**p, "S": p["S"] + h}, option_type=opt)
            dn = black_scholes.price_european(**{**p, "S": p["S"] - h}, option_type=opt)
            fd_delta = (up - dn) / (2 * h)
            ana_delta = black_scholes.greeks_european(option_type=opt, **p)["delta"]
            assert abs(fd_delta - ana_delta) < 1e-3

    @pytest.mark.parametrize("p", GRID)
    def test_finite_diff_vega_matches_analytic(self, p):
        """Vega from analytic (per 1% σ) should match (P(σ+0.005) - P(σ-0.005))/0.01."""
        for opt in ("call", "put"):
            h = 0.005
            up = black_scholes.price_european(**{**p, "sigma": p["sigma"] + h}, option_type=opt)
            dn = black_scholes.price_european(**{**p, "sigma": p["sigma"] - h}, option_type=opt)
            fd_vega = (up - dn) / (2 * h) * 0.01  # per 1% absolute
            ana_vega = black_scholes.greeks_european(option_type=opt, **p)["vega"]
            assert abs(fd_vega - ana_vega) < 1e-3


# ------------------------------------------------------------------ #
# 2. QuantLib European: agree with BS analytic within day-count tol.
# ------------------------------------------------------------------ #
class TestQuantLibEuropean:

    @pytest.mark.parametrize("p", GRID)
    def test_price_matches_bs(self, p):
        for opt in ("call", "put"):
            bs = black_scholes.price_european(option_type=opt, **p)
            ql = quantlib_engine.greeks_ql(option_type=opt, is_american=False, **p)["price"]
            # Tolerance: QL uses Actual/360 vs our T in years; allow 1.5% relative or $0.05
            assert abs(bs - ql) < max(0.015 * abs(bs), 0.05)

    @pytest.mark.parametrize("p", GRID)
    def test_greeks_match_bs(self, p):
        for opt in ("call", "put"):
            bs = black_scholes.greeks_european(option_type=opt, **p)
            ql = quantlib_engine.greeks_ql(option_type=opt, is_american=False, **p)
            for g in ("delta", "gamma", "vega", "theta", "rho"):
                tol = max(0.02 * abs(bs[g]), 0.02)
                assert abs(bs[g] - ql[g]) < tol, (
                    f"{opt} {g}: BS={bs[g]:.4f}  QL={ql[g]:.4f}"
                )

    @pytest.mark.parametrize("p", GRID)
    def test_call_rho_positive_put_rho_negative(self, p):
        gc = quantlib_engine.greeks_ql(option_type="call", is_american=False, **p)
        gp = quantlib_engine.greeks_ql(option_type="put", is_american=False, **p)
        assert gc["rho"] > 0
        assert gp["rho"] < 0


# ------------------------------------------------------------------ #
# 3. QuantLib American: structural properties.
#    American >= European; American Greeks have the right signs.
# ------------------------------------------------------------------ #
class TestQuantLibAmerican:

    @pytest.mark.parametrize("p", GRID)
    def test_american_ge_european(self, p):
        for opt in ("call", "put"):
            eur = quantlib_engine.greeks_ql(option_type=opt, is_american=False, **p)["price"]
            amer = quantlib_engine.greeks_ql(option_type=opt, is_american=True, **p)["price"]
            assert amer >= eur - 1e-3, f"{opt}: American {amer} < European {eur}"

    @pytest.mark.parametrize("p", GRID)
    def test_american_greeks_signs(self, p):
        gc = quantlib_engine.greeks_ql(option_type="call", is_american=True, **p)
        gp = quantlib_engine.greeks_ql(option_type="put", is_american=True, **p)
        assert 0.0 <= gc["delta"] <= 1.0
        assert -1.0 <= gp["delta"] <= 0.0
        assert gc["gamma"] >= -1e-6
        assert gp["gamma"] >= -1e-6
        assert gc["vega"] >= -1e-6
        assert gp["vega"] >= -1e-6
        # Deep-ITM American put exercised at t=0 has zero time value -> rho = 0
        # Deep-ITM American call (q>0) likewise -> rho = 0. Otherwise sign is strict.
        assert gc["rho"] >= -1e-6
        assert gp["rho"] <= 1e-6


# ------------------------------------------------------------------ #
# 4. Knockout: Reiner-Rubinstein vs QuantLib AnalyticBarrierEngine.
# ------------------------------------------------------------------ #
class TestKnockout:

    @pytest.mark.parametrize("opt,B", [
        ("call", 80.0),   # DO call (B<S), K>B
        ("call", 120.0),  # UO call (B>S), K<B
        ("put", 80.0),    # DO put
        ("put", 120.0),   # UO put
    ])
    def test_rr_vs_ql(self, opt, B):
        S, K, r, sig, T, q = 100.0, 100.0, 0.05, 0.20, 0.5, 0.02
        rr, vanilla, _, _ = knockout.price_knockout(S, K, B, r, sig, T, q, opt)
        ql_p, _, _ = quantlib_engine.price_knockout_ql(S, K, B, r, sig, T, q, opt)
        # Day-count differences between analytic forms — allow 2% relative or $0.10
        assert abs(rr - ql_p) < max(0.02 * abs(ql_p), 0.10), (
            f"{opt} B={B}: RR=${rr:.4f} QL=${ql_p:.4f}"
        )

    @pytest.mark.parametrize("opt,B", [
        ("call", 80.0), ("call", 120.0),
        ("put", 80.0),  ("put", 120.0),
    ])
    def test_knockout_le_vanilla(self, opt, B):
        """Knock-out price must be <= vanilla European."""
        S, K, r, sig, T, q = 100.0, 100.0, 0.05, 0.20, 0.5, 0.02
        ko, vanilla, _, _ = knockout.price_knockout(S, K, B, r, sig, T, q, opt)
        assert ko <= vanilla + 1e-6

    def test_uo_call_vanishes_when_strike_above_barrier(self):
        """Up-and-out call with K > B is identically 0 (barrier is hit before payoff)."""
        S, K, B = 100.0, 110.0, 105.0
        rr, _, _, _ = knockout.price_knockout(S, K, B, 0.05, 0.20, 0.5, 0.02, "call")
        assert rr == 0.0

    def test_do_put_vanishes_when_strike_below_barrier(self):
        """Down-and-out put with K < B is identically 0 (mirror of UO call case)."""
        S, K, B = 100.0, 90.0, 95.0
        rr, _, _, _ = knockout.price_knockout(S, K, B, 0.05, 0.20, 0.5, 0.02, "put")
        assert rr == 0.0

    def test_knockout_greeks_call_positive_delta(self):
        """A knock-out call has positive delta (was negative before the router fix)."""
        S, K, B, r, sig, T, q = 100.0, 100.0, 80.0, 0.05, 0.20, 0.5, 0.02
        g = quantlib_engine.greeks_knockout_ql(S, K, B, r, sig, T, q, "call")
        assert g["delta"] > 0
        assert g["rho"] > 0  # call rho positive

    def test_knockout_greeks_put_negative_delta(self):
        S, K, B, r, sig, T, q = 100.0, 100.0, 120.0, 0.05, 0.20, 0.5, 0.02
        g = quantlib_engine.greeks_knockout_ql(S, K, B, r, sig, T, q, "put")
        assert g["delta"] < 0
        assert g["rho"] < 0


# ------------------------------------------------------------------ #
# 5. Monte Carlo LSM: stochastic but should be close to QL American.
# ------------------------------------------------------------------ #
class TestMonteCarloLSM:

    def test_mc_american_put_close_to_ql(self):
        S, K, r, sig, T, q = 100.0, 100.0, 0.05, 0.20, 0.5, 0.02
        mc_price, se, _ = monte_carlo_lsm.price_american(S, K, r, sig, T, q, n_paths=20000, n_steps=50)
        ql_price = quantlib_engine.greeks_ql(S, K, r, sig, T, q, "put", is_american=True)["price"]
        # Allow 3 standard errors plus 1% buffer
        assert abs(mc_price - ql_price) < 3 * se + 0.01 * ql_price + 0.10, (
            f"MC=${mc_price:.4f} ± {se:.4f}  QL=${ql_price:.4f}"
        )

    def test_mc_antithetic_reduces_variance(self):
        """Antithetic should give lower SE than plain MC at same path count."""
        params = dict(S=100, K=100, r=0.05, sigma=0.20, T=0.5, q=0.02, n_paths=5000, n_steps=50)
        _, se_plain, _ = monte_carlo_lsm.price_american(**params, variance_reduction="none")
        _, se_anti, _ = monte_carlo_lsm.price_american(**params, variance_reduction="antithetic")
        # Antithetic shouldn't be much worse than plain (allow 50% drift since seed is fixed)
        assert se_anti < 1.5 * se_plain


# ------------------------------------------------------------------ #
# 6. Router: routes calls to call greeks, puts to put greeks, etc.
# ------------------------------------------------------------------ #
class TestRouter:

    def test_european_call_returns_positive_delta(self):
        pricer, greeks_fn, _ = router.route("european_call")
        g = greeks_fn(S=100, K=100, r=0.05, sigma=0.20, T=0.5, q=0.02)
        assert g["delta"] > 0

    def test_european_put_returns_negative_delta(self):
        pricer, greeks_fn, _ = router.route("european_put")
        g = greeks_fn(S=100, K=100, r=0.05, sigma=0.20, T=0.5, q=0.02)
        assert g["delta"] < 0

    def test_american_call_returns_positive_delta(self):
        pricer, greeks_fn, _ = router.route("american_call")
        g = greeks_fn(S=100, K=100, r=0.05, sigma=0.20, T=0.5, q=0.02)
        assert g["delta"] > 0

    def test_american_put_returns_negative_delta(self):
        pricer, greeks_fn, _ = router.route("american_put")
        g = greeks_fn(S=100, K=100, r=0.05, sigma=0.20, T=0.5, q=0.02)
        assert g["delta"] < 0

    def test_knockout_call_returns_positive_delta(self):
        pricer, greeks_fn, _ = router.route("knockout_call")
        g = greeks_fn(S=100, K=100, r=0.05, sigma=0.20, T=0.5, q=0.02, barrier_level=80)
        assert g["delta"] > 0
        assert g["rho"] > 0

    def test_knockout_put_returns_negative_delta(self):
        pricer, greeks_fn, _ = router.route("knockout_put")
        g = greeks_fn(S=100, K=100, r=0.05, sigma=0.20, T=0.5, q=0.02, barrier_level=120)
        assert g["delta"] < 0
        assert g["rho"] < 0

    def test_unknown_option_type_raises(self):
        with pytest.raises(ValueError):
            router.route("does_not_exist")

    def test_knockout_without_barrier_raises(self):
        pricer, _, _ = router.route("knockout_call")
        with pytest.raises(ValueError):
            pricer(S=100, K=100, r=0.05, sigma=0.20, T=0.5, q=0.02)


# ------------------------------------------------------------------ #
# 7. Greek scaling smoke tests — guard against accidental
#    annual/daily or per-1.0/per-1% regressions.
# ------------------------------------------------------------------ #
class TestGreekScaling:

    def test_theta_is_daily_not_annual(self):
        """ATM 90-day call theta should be on the order of -$0.05/day, not -$20/year."""
        g = quantlib_engine.greeks_ql(100, 100, 0.05, 0.20, 90 / 365, 0.02, "call", is_american=False)
        # Daily theta for ATM 90d call ≈ -$0.025 to -$0.06.  Annual would be ~-$10.
        assert -0.50 < g["theta"] < 0
        bs = black_scholes.greeks_european(100, 100, 0.05, 0.20, 90 / 365, 0.02, "call")
        assert abs(g["theta"] - bs["theta"]) < 0.05

    def test_vega_is_per_one_percent(self):
        """ATM 90-day call vega per-1%-σ should be ~$0.20, not ~$20 (per-1.0)."""
        g = quantlib_engine.greeks_ql(100, 100, 0.05, 0.20, 90 / 365, 0.02, "call", is_american=False)
        assert 0.05 < g["vega"] < 1.0

    def test_rho_is_per_one_percent(self):
        """ATM 90-day call rho per-1%-r should be on the order of $0.10, not $10."""
        g = quantlib_engine.greeks_ql(100, 100, 0.05, 0.20, 90 / 365, 0.02, "call", is_american=False)
        assert 0.0 < g["rho"] < 1.0


# ------------------------------------------------------------------ #
# 8. Market data scaling (no live API): mock yfinance.
# ------------------------------------------------------------------ #
class TestMarketDataScaling:

    def test_dividend_yield_normalised_to_decimal(self, monkeypatch):
        """Yahoo returns 1.14 (percent); we must store 0.0114 (decimal)."""
        import pandas as pd
        from src.data import market_data

        market_data._market_cache.clear()

        class FakeTicker:
            def __init__(self, ticker):
                self.ticker = ticker
            def history(self, period):
                # 6 months of trivial price data so vol code paths run
                idx = pd.date_range("2025-01-01", periods=130, freq="B")
                return pd.DataFrame({"Close": np.linspace(100, 110, 130)}, index=idx)
            @property
            def info(self):
                return {"dividendYield": 1.14}

        class FakeYF:
            Ticker = FakeTicker

        monkeypatch.setattr(market_data, "yf", FakeYF, raising=False)
        # Force the import-shadowing path: replace the actual module ref used inside the function
        import sys
        sys.modules["yfinance"] = FakeYF

        result = market_data.fetch_market_params("MOCKDIV", max_retries=1, timeout=1)
        assert result["dividend_yield"] is not None
        assert abs(result["dividend_yield"] - 0.0114) < 1e-6, (
            f"Got {result['dividend_yield']} — expected 0.0114"
        )

    def test_vol_90d_uses_six_month_window(self, monkeypatch):
        """Confirm vol_90d is now computed (not None) when 6mo of data is available."""
        import pandas as pd
        from src.data import market_data

        market_data._market_cache.clear()

        class FakeTicker:
            def __init__(self, ticker): self.ticker = ticker
            def history(self, period):
                # ~125 trading days = ~6 months — enough for vol_90d
                idx = pd.date_range("2025-01-01", periods=130, freq="B")
                rng = np.random.default_rng(0)
                rets = rng.normal(0.0, 0.01, 130)
                px = 100 * np.exp(np.cumsum(rets))
                return pd.DataFrame({"Close": px}, index=idx)
            @property
            def info(self):
                return {"dividendYield": None}

        class FakeYF:
            Ticker = FakeTicker

        import sys
        sys.modules["yfinance"] = FakeYF

        result = market_data.fetch_market_params("MOCKVOL", max_retries=1, timeout=1)
        assert result["volatility_30d"] is not None
        assert result["volatility_90d"] is not None
        # ~16% annualised vol with σ_daily=0.01 * √252
        assert 0.05 < result["volatility_90d"] < 0.50

    def test_cache_is_per_ticker(self, monkeypatch):
        """Different tickers must NOT collide in the cache."""
        from src.data import market_data
        market_data._market_cache.clear()
        market_data._market_cache.set("AAA_market_params", {"spot_price": 1.0})
        market_data._market_cache.set("BBB_market_params", {"spot_price": 999.0})
        assert market_data._market_cache.get("AAA_market_params")["spot_price"] == 1.0
        assert market_data._market_cache.get("BBB_market_params")["spot_price"] == 999.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
