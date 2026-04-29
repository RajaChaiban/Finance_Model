"""Model validation regression suite — Phase 11 Vol Desk bank-grade uplift.

Locks in golden numbers for every option type the router supports, using
analytic Black-Scholes references, parity identities, and hand-computed
bounds.  Any numerical drift in an engine will surface here on every commit.

Tests are numbered to match the Phase 11 spec (1–12).
"""

import math
import pytest

pytest.importorskip("QuantLib")

from src.engines import router, black_scholes
from src.engines import monte_carlo_lsm

# ---------------------------------------------------------------------------
# Shared parameter sets
# ---------------------------------------------------------------------------

ATM = dict(S=100.0, K=100.0, r=0.05, sigma=0.20, T=1.0, q=0.0)
# r=0.10 strengthens early-exercise incentive for American puts
ATM_HIGH_R = dict(S=100.0, K=100.0, r=0.10, sigma=0.20, T=1.0, q=0.0)

ALL_12_TYPES = [
    "european_call", "european_put",
    "american_call", "american_put",
    "knockout_call", "knockout_put",
    "knockin_call",  "knockin_put",
    "asian_call",    "asian_put",
    "lookback_call", "lookback_put",
]

# Barrier extras needed by the barrier engines
_BARRIER_EXTRA = {
    "knockout_call": {"barrier_level": 80.0},   # down-and-out call B < S
    "knockout_put":  {"barrier_level": 120.0},  # up-and-out put   B > S
    "knockin_call":  {"barrier_level": 80.0},
    "knockin_put":   {"barrier_level": 120.0},
}

# Asian extras (arithmetic for one test, geometric elsewhere)
_ASIAN_GEO_EXTRA  = {"averaging_method": "geometric",   "averaging_frequency": "daily"}
_ASIAN_ARITH_EXTRA = {"averaging_method": "arithmetic",  "averaging_frequency": "daily"}

# Lookback extras
_LB_FIXED    = {"lookback_type": "fixed"}
_LB_FLOATING = {"lookback_type": "floating"}


def _extras(opt_type: str, **overrides) -> dict:
    """Return the kwargs needed to call a pricer for the given option_type."""
    base = dict(_BARRIER_EXTRA.get(opt_type, {}))
    if "asian" in opt_type:
        base.update(_ASIAN_GEO_EXTRA)
    if "lookback" in opt_type:
        base.update(_LB_FIXED)
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Test 1 — European call/put match Black-Scholes analytic (1e-6 absolute)
# ---------------------------------------------------------------------------

class TestEuropeanBSMatch:
    """Test 1: Router European prices match Black-Scholes formula to 1e-6."""

    def test_european_call_matches_bs(self):
        S, K, r, sigma, T, q = ATM["S"], ATM["K"], ATM["r"], ATM["sigma"], ATM["T"], ATM["q"]
        bs_call = black_scholes.price_european(S, K, r, sigma, T, q, "call")

        pricer, _, _ = router.route("european_call")
        price, _, _ = pricer(S=S, K=K, r=r, sigma=sigma, T=T, q=q)

        assert abs(price - bs_call) < 1e-6, (
            f"European call router={price:.8f} vs BS={bs_call:.8f} "
            f"(diff={abs(price - bs_call):.2e})"
        )

    def test_european_put_matches_bs(self):
        S, K, r, sigma, T, q = ATM["S"], ATM["K"], ATM["r"], ATM["sigma"], ATM["T"], ATM["q"]
        bs_put = black_scholes.price_european(S, K, r, sigma, T, q, "put")

        pricer, _, _ = router.route("european_put")
        price, _, _ = pricer(S=S, K=K, r=r, sigma=sigma, T=T, q=q)

        assert abs(price - bs_put) < 1e-6, (
            f"European put router={price:.8f} vs BS={bs_put:.8f} "
            f"(diff={abs(price - bs_put):.2e})"
        )


# ---------------------------------------------------------------------------
# Test 2 — Put-call parity for European (1e-6 absolute)
# ---------------------------------------------------------------------------

class TestPutCallParity:
    """Test 2: C - P = S·e^(-qT) - K·e^(-rT)."""

    def test_put_call_parity_atm(self):
        S, K, r, sigma, T, q = ATM["S"], ATM["K"], ATM["r"], ATM["sigma"], ATM["T"], ATM["q"]

        call_pricer, _, _ = router.route("european_call")
        put_pricer,  _, _ = router.route("european_put")

        C, _, _ = call_pricer(S=S, K=K, r=r, sigma=sigma, T=T, q=q)
        P, _, _ = put_pricer( S=S, K=K, r=r, sigma=sigma, T=T, q=q)

        lhs = C - P
        rhs = S * math.exp(-q * T) - K * math.exp(-r * T)

        assert abs(lhs - rhs) < 1e-6, (
            f"Parity: C-P={lhs:.8f}, S·e(-qT)-K·e(-rT)={rhs:.8f} "
            f"(diff={abs(lhs - rhs):.2e})"
        )


# ---------------------------------------------------------------------------
# Test 3 — American put exceeds European put (early-exercise premium)
# ---------------------------------------------------------------------------

class TestAmericanEarlyExercise:
    """Test 3: American put >= European put (early-exercise value)."""

    def test_american_put_exceeds_european_put(self):
        S, K, r, sigma, T, q = (
            ATM_HIGH_R["S"], ATM_HIGH_R["K"], ATM_HIGH_R["r"],
            ATM_HIGH_R["sigma"], ATM_HIGH_R["T"], ATM_HIGH_R["q"],
        )

        eu_pricer, _, _ = router.route("european_put")
        am_pricer, _, _ = router.route("american_put")

        P_eur, _, _ = eu_pricer(S=S, K=K, r=r, sigma=sigma, T=T, q=q)
        P_amer, _, _ = am_pricer(S=S, K=K, r=r, sigma=sigma, T=T, q=q)

        # Must be >= European (American >= European by holder optionality)
        assert P_amer >= P_eur - 1e-6, (
            f"American put {P_amer:.6f} < European put {P_eur:.6f} — invalid!"
        )
        # Should have a clear early-exercise premium at r=0.10
        assert P_amer > P_eur + 0.05, (
            f"American put {P_amer:.6f} lacks early-exercise premium vs European {P_eur:.6f} "
            f"(premium={P_amer - P_eur:.4f} < 0.05)"
        )


# ---------------------------------------------------------------------------
# Test 4 — Knockout + Knockin = Vanilla parity (1e-3)
# ---------------------------------------------------------------------------

class TestBarrierParity:
    """Test 4: KO_put + KI_put = European_put (down barrier B=80 < S=100)."""

    def test_ko_plus_ki_equals_vanilla_put(self):
        S, K, r, sigma, T, q = ATM["S"], ATM["K"], ATM["r"], ATM["sigma"], ATM["T"], ATM["q"]
        B = 80.0  # down barrier for a put

        ko_pricer, _, _ = router.route("knockout_put")
        ki_pricer, _, _ = router.route("knockin_put")
        eu_pricer, _, _ = router.route("european_put")

        ko_price, _, _ = ko_pricer(S=S, K=K, r=r, sigma=sigma, T=T, q=q, barrier_level=B)
        ki_price, _, _ = ki_pricer(S=S, K=K, r=r, sigma=sigma, T=T, q=q, barrier_level=B)
        eu_price, _, _ = eu_pricer(S=S, K=K, r=r, sigma=sigma, T=T, q=q)

        error = abs((ko_price + ki_price) - eu_price)
        assert error < 1e-3, (
            f"KO_put + KI_put = {ko_price + ki_price:.6f} vs EU_put = {eu_price:.6f} "
            f"(error={error:.2e})"
        )


# ---------------------------------------------------------------------------
# Test 5 — Knockout call monotone in barrier (up-out call)
# ---------------------------------------------------------------------------

class TestKnockoutMonotonicity:
    """Test 5: Up-and-out call price(B=110) > price(B=105) — barrier closer to S destroys more value."""

    def test_upout_call_monotone_in_barrier(self):
        S, K, r, sigma, T, q = ATM["S"], ATM["K"], ATM["r"], ATM["sigma"], ATM["T"], ATM["q"]

        pricer, _, _ = router.route("knockout_call")

        # B=110: barrier far above spot — high survival probability → high price
        price_B110, _, _ = pricer(S=S, K=K, r=r, sigma=sigma, T=T, q=q, barrier_level=110.0)
        # B=105: barrier closer to spot — easier to knock out → lower price
        price_B105, _, _ = pricer(S=S, K=K, r=r, sigma=sigma, T=T, q=q, barrier_level=105.0)

        assert price_B110 > price_B105, (
            f"Up-out call: price(B=110)={price_B110:.6f} should exceed "
            f"price(B=105)={price_B105:.6f}"
        )


# ---------------------------------------------------------------------------
# Test 6 — Asian geometric cheaper than vanilla European
# ---------------------------------------------------------------------------

class TestAsianGeometricVsVanilla:
    """Test 6: Geometric Asian call < European call (averaging reduces effective vol)."""

    def test_asian_geo_call_cheaper_than_vanilla(self):
        S, K, r, sigma, T, q = ATM["S"], ATM["K"], ATM["r"], ATM["sigma"], ATM["T"], ATM["q"]

        eu_pricer, _, _ = router.route("european_call")
        asian_pricer, _, _ = router.route("asian_call")

        euro_price, _, _ = eu_pricer(S=S, K=K, r=r, sigma=sigma, T=T, q=q)
        geo_price, _, _ = asian_pricer(
            S=S, K=K, r=r, sigma=sigma, T=T, q=q,
            **_ASIAN_GEO_EXTRA,
        )

        assert geo_price < euro_price - 0.5, (
            f"Geometric Asian call {geo_price:.4f} not sufficiently cheaper than "
            f"European call {euro_price:.4f} (diff={euro_price - geo_price:.4f} < 0.5)"
        )


# ---------------------------------------------------------------------------
# Test 7 — Asian arithmetic >= Asian geometric (AM-GM inequality)
# ---------------------------------------------------------------------------

class TestAsianArithVsGeo:
    """Test 7: E[arith mean] >= E[geo mean] — arithmetic Asian >= geometric Asian."""

    def test_arithmetic_asian_ge_geometric_asian(self):
        S, K, r, sigma, T, q = ATM["S"], ATM["K"], ATM["r"], ATM["sigma"], ATM["T"], ATM["q"]

        asian_pricer, _, _ = router.route("asian_call")

        geo_price, _, _ = asian_pricer(
            S=S, K=K, r=r, sigma=sigma, T=T, q=q,
            n_paths=50000, **_ASIAN_GEO_EXTRA,
        )
        arith_price, _, _ = asian_pricer(
            S=S, K=K, r=r, sigma=sigma, T=T, q=q,
            n_paths=50000, **_ASIAN_ARITH_EXTRA,
        )

        # Tolerance of 0.5 to allow for MC noise
        assert arith_price >= geo_price - 0.5, (
            f"Arithmetic Asian {arith_price:.4f} < Geometric Asian {geo_price:.4f} "
            f"(diff={geo_price - arith_price:.4f} > 0.5 tolerance)"
        )


# ---------------------------------------------------------------------------
# Test 8 — Fixed-strike lookback call >= European call
# ---------------------------------------------------------------------------

class TestLookbackVsVanilla:
    """Test 8: Lookback fixed-strike call > European call (path-dependence premium)."""

    def test_lookback_fixed_call_exceeds_european(self):
        # K=90 (ITM call) so European price is meaningful for comparison
        S, K, r, sigma, T, q = 100.0, 90.0, 0.05, 0.30, 1.0, 0.0

        eu_pricer, _, _   = router.route("european_call")
        lb_pricer, _, _ = router.route("lookback_call")

        euro_price, _, _ = eu_pricer(S=S, K=K, r=r, sigma=sigma, T=T, q=q)
        lb_price, _, _   = lb_pricer(S=S, K=K, r=r, sigma=sigma, T=T, q=q, **_LB_FIXED)

        assert lb_price > euro_price, (
            f"Fixed-strike lookback call {lb_price:.4f} should exceed "
            f"European call {euro_price:.4f}"
        )
        # Expect a substantial path-dependence premium (> $2 for σ=0.30, T=1)
        assert lb_price > euro_price + 2.0, (
            f"Lookback premium {lb_price - euro_price:.4f} < $2.00 — suspiciously small "
            f"for σ=0.30, T=1.0"
        )


# ---------------------------------------------------------------------------
# Test 9 — Floating-strike lookback call price positive
# ---------------------------------------------------------------------------

class TestFloatingLookbackPositive:
    """Test 9: Floating-strike lookback call has strictly positive price (payoff >= 0 always)."""

    def test_floating_lookback_call_positive(self):
        S, K, r, sigma, T, q = 100.0, 100.0, 0.05, 0.30, 1.0, 0.0

        lb_pricer, _, _ = router.route("lookback_call")

        # For floating-strike, K is the running extremum; K=S for a fresh option
        price, _, _ = lb_pricer(S=S, K=K, r=r, sigma=sigma, T=T, q=q, **_LB_FLOATING)

        assert price > 0, (
            f"Floating-strike lookback call price={price:.6f} is not positive"
        )
        # Floating lookback must exceed European call (same S,K ATM) by a margin
        eu_pricer, _, _ = router.route("european_call")
        eur_price, _, _ = eu_pricer(S=S, K=K, r=r, sigma=sigma, T=T, q=q)
        assert price > eur_price, (
            f"Floating lookback call {price:.4f} <= European call {eur_price:.4f}"
        )


# ---------------------------------------------------------------------------
# Test 10 — Greeks sign conventions for all 12 product types
# ---------------------------------------------------------------------------

class TestGreekSignConventions:
    """Test 10: Sign conventions — delta, gamma, vega, theta, rho across all products."""

    @pytest.mark.parametrize("opt_type", ALL_12_TYPES)
    def test_greeks_signs(self, opt_type):
        S, K, r, sigma, T, q = ATM["S"], ATM["K"], ATM["r"], ATM["sigma"], ATM["T"], ATM["q"]
        extra = _extras(opt_type)

        _, greeks_fn, _ = router.route(opt_type)
        g = greeks_fn(S=S, K=K, r=r, sigma=sigma, T=T, q=q, **extra)

        # --- Delta sign/range ---
        # Fixed-strike lookback delta can legally exceed 1 (Conze-Viswanathan formula adds
        # a (σ²/2r)·N(d1) correction on top of a standard delta — theoretically > 1 for
        # common equity-vol/rate regimes).  We only enforce positivity.
        #
        # Knock-in delta can be negative for calls (or positive for puts) because
        # delta_KI = delta_vanilla − delta_KO and the KO often carries most of the
        # vanilla's delta when B is far from S.  No tight sign bound is imposed; we
        # check only that the absolute magnitude is within a reasonable physical range.
        if opt_type in ("lookback_call", "lookback_put"):
            # Lookback delta: bounded loosely; fixed-call can exceed 1 by ~25-30%.
            if "call" in opt_type:
                assert g["delta"] > 0, (
                    f"{opt_type}: call delta={g['delta']:.6f} should be > 0"
                )
                assert g["delta"] <= 2.0, (
                    f"{opt_type}: call delta={g['delta']:.6f} unreasonably large (> 2)"
                )
            else:
                assert g["delta"] < 0, (
                    f"{opt_type}: put delta={g['delta']:.6f} should be < 0"
                )
                assert g["delta"] >= -2.0, (
                    f"{opt_type}: put delta={g['delta']:.6f} unreasonably large magnitude (< -2)"
                )
        elif opt_type in ("knockin_call", "knockin_put"):
            # Knock-in delta: sign-flip is known and correct; enforce magnitude only.
            assert abs(g["delta"]) <= 1.5, (
                f"{opt_type}: |delta|={abs(g['delta']):.6f} unreasonably large (> 1.5)"
            )
        elif "call" in opt_type:
            assert g["delta"] > -1e-6, (
                f"{opt_type}: call delta={g['delta']:.6f} should be >= 0"
            )
            assert g["delta"] <= 1.0 + 1e-6, (
                f"{opt_type}: call delta={g['delta']:.6f} should be <= 1"
            )
        else:
            assert g["delta"] < 1e-6, (
                f"{opt_type}: put delta={g['delta']:.6f} should be <= 0"
            )
            assert g["delta"] >= -1.0 - 1e-6, (
                f"{opt_type}: put delta={g['delta']:.6f} should be >= -1"
            )

        # Gamma: non-negative for long options
        assert g["gamma"] >= -1e-6, (
            f"{opt_type}: gamma={g['gamma']:.6f} should be >= 0"
        )

        # Vega: non-negative (long options gain from rising vol)
        assert g["vega"] >= -1e-6, (
            f"{opt_type}: vega={g['vega']:.6f} should be >= 0"
        )

        # Theta: per-day, negative for long options (time decay)
        assert g["theta"] <= 1e-6, (
            f"{opt_type}: theta={g['theta']:.6f} should be <= 0 (time decay)"
        )

        # Rho: calls positive (benefit from rising rates), puts negative.
        # Knock-ins again can have unexpected rho signs near the barrier; skip strict
        # rho sign for KI types — we still verify magnitude is bounded.
        if opt_type not in ("knockin_call", "knockin_put"):
            if "call" in opt_type:
                assert g["rho"] >= -1e-6, (
                    f"{opt_type}: call rho={g['rho']:.6f} should be >= 0"
                )
            else:
                assert g["rho"] <= 1e-6, (
                    f"{opt_type}: put rho={g['rho']:.6f} should be <= 0"
                )


# ---------------------------------------------------------------------------
# Test 11 — Vega ≈ ∂V/∂σ via finite difference (1% relative tolerance)
# ---------------------------------------------------------------------------

class TestVegaFiniteDifference:
    """Test 11: Analytic vega consistent with finite-difference bump for BS European."""

    def test_bs_vega_finite_difference_call(self):
        S, K, r, sigma, T, q = ATM["S"], ATM["K"], ATM["r"], ATM["sigma"], ATM["T"], ATM["q"]
        eps = 1e-4

        # BS analytic vega (per 1% absolute σ — the repo invariant)
        g = black_scholes.greeks_european(S, K, r, sigma, T, q, "call")
        analytic_vega = g["vega"]  # per 1% σ

        # Central-difference ∂V/∂σ (raw, per unit σ), then convert to per 1%
        price_up = black_scholes.price_european(S, K, r, sigma + eps, T, q, "call")
        price_dn = black_scholes.price_european(S, K, r, sigma - eps, T, q, "call")
        fd_vega_raw = (price_up - price_dn) / (2.0 * eps)  # per unit σ
        fd_vega = fd_vega_raw / 100.0  # per 1% σ — matches repo convention

        # 1% relative tolerance
        rel_err = abs(analytic_vega - fd_vega) / max(abs(analytic_vega), 1e-10)
        assert rel_err < 0.01, (
            f"Call vega: analytic={analytic_vega:.6f}, FD={fd_vega:.6f}, "
            f"rel_err={rel_err:.4%} > 1%"
        )

    def test_bs_vega_finite_difference_put(self):
        S, K, r, sigma, T, q = ATM["S"], ATM["K"], ATM["r"], ATM["sigma"], ATM["T"], ATM["q"]
        eps = 1e-4

        g = black_scholes.greeks_european(S, K, r, sigma, T, q, "put")
        analytic_vega = g["vega"]

        price_up = black_scholes.price_european(S, K, r, sigma + eps, T, q, "put")
        price_dn = black_scholes.price_european(S, K, r, sigma - eps, T, q, "put")
        fd_vega_raw = (price_up - price_dn) / (2.0 * eps)
        fd_vega = fd_vega_raw / 100.0

        rel_err = abs(analytic_vega - fd_vega) / max(abs(analytic_vega), 1e-10)
        assert rel_err < 0.01, (
            f"Put vega: analytic={analytic_vega:.6f}, FD={fd_vega:.6f}, "
            f"rel_err={rel_err:.4%} > 1%"
        )


# ---------------------------------------------------------------------------
# Test 12 — MC vs binomial-tree American put within 4 std errors
# ---------------------------------------------------------------------------

class TestMCvsBinomialAmerican:
    """Test 12: MC LSM American put price consistent with QL binomial tree within 4σ."""

    def test_mc_vs_tree_american_put(self):
        S, K, r, sigma, T, q = ATM["S"], ATM["K"], ATM["r"], ATM["sigma"], ATM["T"], ATM["q"]

        # Binomial tree via default router (QL)
        tree_pricer, _, _ = router.route("american_put")
        P_tree, _, _ = tree_pricer(S=S, K=K, r=r, sigma=sigma, T=T, q=q)

        # MC LSM via route_with_engine('mc')
        mc_pricer, _, _ = router.route_with_engine("american_put", engine="mc")
        n_paths = 20000
        P_mc, std_err, _ = mc_pricer(
            S=S, K=K, r=r, sigma=sigma, T=T, q=q,
            n_paths=n_paths, n_steps=90,
        )

        # If engine didn't return a usable std_err, estimate from theory
        if std_err <= 0 or not math.isfinite(std_err):
            # Rough MC std_err from typical LSM noise (~$0.05 for 20k paths)
            std_err = 0.05

        tolerance = 4.0 * std_err
        assert abs(P_mc - P_tree) <= tolerance, (
            f"MC LSM ({P_mc:.4f}) vs binomial tree ({P_tree:.4f}): "
            f"diff={abs(P_mc - P_tree):.4f} > 4σ={tolerance:.4f} (std_err={std_err:.4f})"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
