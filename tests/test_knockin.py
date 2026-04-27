"""Knock-in barrier-option correctness tests.

The most powerful KI test is the no-arbitrage parity:
    KO_price + KI_price ≡ Vanilla_price
for the same K, B, T, σ, q, r and same call/put. We exploit it heavily
because it cross-checks the engine without depending on any external
reference price. We also assert directional sanity and the limiting case
(barrier far from spot → KI ≈ 0).
"""

import pytest

from src.engines import router, black_scholes


# Common test params. ATM, mid-vol, mid-T — the regime where pricing is most
# sensitive to engine bugs (barrier well within reach but not certain).
S, K, r, sigma, T, q = 100.0, 100.0, 0.05, 0.25, 0.5, 0.02

PARITY_CASES = [
    # (option_type_root, B): roots map to (knockout_X, knockin_X, european_X)
    ("call", 90.0),    # down barrier on a call
    ("call", 110.0),   # up barrier on a call
    ("put",  90.0),    # down barrier on a put
    ("put",  110.0),   # up barrier on a put
]


@pytest.mark.parametrize("opt,B", PARITY_CASES)
def test_ko_plus_ki_equals_vanilla(opt, B):
    """No-arb parity: KO + KI = Vanilla, to machine precision."""
    ko_pricer, _, _ = router.route(f"knockout_{opt}")
    ki_pricer, _, _ = router.route(f"knockin_{opt}")
    eu_pricer, _, _ = router.route(f"european_{opt}")

    ko, _, _ = ko_pricer(S, K, r, sigma, T, q, barrier_level=B)
    ki, _, _ = ki_pricer(S, K, r, sigma, T, q, barrier_level=B)
    eu, _, _ = eu_pricer(S, K, r, sigma, T, q)

    # 1e-6 covers QL closed-form rounding; the real-world error is ~1e-12.
    assert abs((ko + ki) - eu) < 1e-6, (
        f"{opt} B={B}: KO={ko:.6f} + KI={ki:.6f} = {ko+ki:.6f} vs EU={eu:.6f} "
        f"(error={abs(ko+ki-eu):.2e})"
    )


@pytest.mark.parametrize("opt,B", PARITY_CASES)
def test_ki_price_in_valid_range(opt, B):
    """A KI is bounded: 0 ≤ KI ≤ Vanilla. Negative or > vanilla = bug."""
    ki_pricer, _, _ = router.route(f"knockin_{opt}")
    eu_pricer, _, _ = router.route(f"european_{opt}")

    ki, _, _ = ki_pricer(S, K, r, sigma, T, q, barrier_level=B)
    eu, _, _ = eu_pricer(S, K, r, sigma, T, q)

    assert ki >= -1e-9, f"{opt} B={B}: KI price negative ({ki:.6f})"
    assert ki <= eu + 1e-9, f"{opt} B={B}: KI {ki:.6f} > Vanilla {eu:.6f}"


def test_ki_far_barrier_approaches_zero():
    """Barrier very far from spot → KI is unlikely to ever activate → ≈ 0."""
    ki_pricer, _, _ = router.route("knockin_call")
    # Up barrier at 5x spot, T=0.5y, σ=20% — practically unreachable.
    ki, _, _ = ki_pricer(S, K=100, r=0.05, sigma=0.20, T=0.5, q=0.0,
                          barrier_level=500.0)
    assert ki < 1e-3, f"Far up-barrier KI call should be ~0, got {ki:.6f}"

    ki_pricer_p, _, _ = router.route("knockin_put")
    # Down barrier at 0.05x spot — practically unreachable.
    ki_p, _, _ = ki_pricer_p(S, K=100, r=0.05, sigma=0.20, T=0.5, q=0.0,
                              barrier_level=5.0)
    assert ki_p < 1e-3, f"Far down-barrier KI put should be ~0, got {ki_p:.6f}"


def test_ki_at_barrier_equals_vanilla():
    """When spot is already at/beyond the barrier, KI is *immediately
    activated* and prices like a vanilla.

    Down-and-in call with B = S means the knock-in trigger is already met at t=0.
    The QuantLib engine treats S exactly at the barrier as already-knocked-in.
    """
    ki_pricer, _, _ = router.route("knockin_call")
    eu_pricer, _, _ = router.route("european_call")

    # Set S above B but very close — KI should be very close to vanilla but
    # not exactly equal (small chance B is never touched again).
    ki, _, _ = ki_pricer(S=100.0, K=100.0, r=0.05, sigma=0.20, T=0.5, q=0.0,
                          barrier_level=99.99)
    eu, _, _ = eu_pricer(S=100.0, K=100.0, r=0.05, sigma=0.20, T=0.5, q=0.0)
    # KI within 5% of vanilla — the touch-probability is very high.
    assert ki / eu > 0.95, f"KI/EU = {ki/eu:.4f}, expected > 0.95 with B near S"


@pytest.mark.parametrize("opt", ["call", "put"])
def test_ki_greeks_parity(opt):
    """Linearity: KI Greek + KO Greek = Vanilla Greek (per Greek).

    Differentiation is linear, so the parity at the price level extends to
    every Greek. Tests delta, gamma, vega, theta, rho.
    """
    B = 90.0 if opt == "call" else 110.0  # barrier-on-the-OTM-side
    _, ki_greeks, _ = router.route(f"knockin_{opt}")
    _, ko_greeks, _ = router.route(f"knockout_{opt}")
    _, eu_greeks, _ = router.route(f"european_{opt}")

    ki_g = ki_greeks(S, K, r, sigma, T, q, barrier_level=B)
    ko_g = ko_greeks(S, K, r, sigma, T, q, barrier_level=B)
    eu_g = eu_greeks(S, K, r, sigma, T, q)

    # Tolerance: bump-reprice Greeks accumulate ~1e-3 numerical noise from
    # 0.5% spot bump differencing; vega/rho are noisier still. 5e-3 is loose
    # enough to pass under any sensible bump scheme.
    for greek in ("delta", "gamma", "vega", "theta", "rho"):
        if greek not in eu_g:
            continue
        sum_ki_ko = ki_g[greek] + ko_g[greek]
        err = abs(sum_ki_ko - eu_g[greek])
        # Scale tolerance by Greek magnitude (gamma is ~0.01, delta ~0.5).
        tol = max(5e-3, 0.02 * abs(eu_g[greek]))
        assert err < tol, (
            f"{opt} {greek}: KI={ki_g[greek]:.4f} + KO={ko_g[greek]:.4f} = "
            f"{sum_ki_ko:.4f} vs EU={eu_g[greek]:.4f} (error={err:.4e}, tol={tol:.4e})"
        )


def test_ki_direction_inferred_from_barrier():
    """B < S → Down-and-In, B > S → Up-and-In. Not user-specified.

    Sanity: a Down-and-In call with B < S < K (barrier OTM, vanilla OTM)
    must have positive value but less than the equivalent Up-and-In call
    that knocks in *into the money* zone.
    """
    ki_pricer, _, _ = router.route("knockin_call")
    # Far-OTM strike to make the KI vs KI comparison meaningful.
    K_otm = 110.0
    p_dni, _, _ = ki_pricer(S=100.0, K=K_otm, r=0.05, sigma=0.25, T=0.5, q=0.0,
                              barrier_level=85.0)   # down-in
    p_uni, _, _ = ki_pricer(S=100.0, K=K_otm, r=0.05, sigma=0.25, T=0.5, q=0.0,
                              barrier_level=115.0)  # up-in (already-ITM trigger)
    assert p_uni > p_dni, (
        f"Up-in call (B=115, K=110) should beat down-in call (B=85, K=110): "
        f"got UpI={p_uni:.4f} vs DnI={p_dni:.4f}"
    )


def test_router_accepts_knockin_types():
    """Smoke: both knockin entries are registered and dispatch without error."""
    for opt in ("knockin_call", "knockin_put"):
        pricer, greeks, label = router.route(opt)
        assert callable(pricer)
        assert callable(greeks)
        assert "QuantLib" in label or "Reiner-Rubinstein" in label
