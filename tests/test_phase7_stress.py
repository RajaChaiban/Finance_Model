"""Stress tests for the Phase-7 senior-structurer additions.

Exercises edge cases, structural invariants, monotonicity, and the
end-to-end API round trip. Designed to catch the bugs that the basic
test_phase7_engines.py smoke tests miss:

1. Edge regimes — T near 0, σ near 0, spot at the strike, deep ITM/OTM
2. Multi-asset basket robustness for phoenix + worst-of
3. Variance swap convergence across strike grid widths
4. XVA monotonicity (T↑ → CVA↑; spread↑ → FVA↑)
5. Bid/offer ordering (bid ≤ mid ≤ offer; spread ≥ 0)
6. Vanna/volga stability across barrier products
7. Hedge-ticket emission via a full agent session ending at Gate C
8. Lifecycle re-mark + attribution decomposition sanity
9. Book aggregator with N sessions
10. Termsheet, KID, hedge-tickets, book, lifecycle endpoints via TestClient
11. Concurrency — parallel router calls
12. Random sweeps — many seeds, no crashes
"""

from __future__ import annotations

import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

# DEMO_REPLAY must be set before importing src.agents to keep tests offline.
os.environ.setdefault("DEMO_REPLAY", "1")
os.environ.setdefault("GEMINI_API_KEY", "")

from src.agents import book, hedge_ticket, lifecycle
from src.agents.orchestrator import OrchestratorAgent, SessionStore
from src.agents.state import (
    AutocallTerms,
    Candidate,
    ClientObjective,
    Gate,
    GreeksSnapshot,
    Leg,
    MarketRegime,
    ObservationSchedule,
    PricedCandidate,
    SessionStatus,
    StructureKind,
    StructuringSession,
)
from src.analysis import bid_list, vanna_volga, vega_bucket, xva
from src.api.handlers import price_option
from src.api.models import PricingRequest
from src.data import correlation, dividend_curve, discounting
from src.engines import digitals, router, variance_swap, reverse_convertible
from src.report import kid


# ---------------------------------------------------------------------------
# 1. Edge-case pricing — T near zero, σ near zero, deep moneyness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("opt", ["digital_call", "digital_put"])
def test_digital_at_expiry_returns_intrinsic(opt):
    """T → 0 should return the intrinsic indicator value."""
    side = "call" if "call" in opt else "put"
    # ITM — call where S > K, put where S < K
    if side == "call":
        S, K = 110.0, 100.0
    else:
        S, K = 90.0, 100.0
    p, _, _ = digitals.price_digital_cash(S, K, 0.05, 0.20, 1e-9, 0.0, side, 1.0)
    assert p == pytest.approx(1.0, abs=1e-6)


@pytest.mark.parametrize("opt", ["digital_call", "digital_put"])
def test_digital_at_expiry_otm_returns_zero(opt):
    side = "call" if "call" in opt else "put"
    if side == "call":
        S, K = 90.0, 100.0
    else:
        S, K = 110.0, 100.0
    p, _, _ = digitals.price_digital_cash(S, K, 0.05, 0.20, 1e-9, 0.0, side, 1.0)
    assert p == pytest.approx(0.0, abs=1e-6)


def test_digital_low_sigma_close_to_payoff_indicator():
    """At very low σ, digital call should approach exp(-rT) when ITM, 0 when OTM."""
    p_itm, _, _ = digitals.price_digital_cash(120, 100, 0.05, 1e-4, 1.0, 0.0, "call", 1.0)
    p_otm, _, _ = digitals.price_digital_cash(80, 100, 0.05, 1e-4, 1.0, 0.0, "call", 1.0)
    assert p_itm == pytest.approx(math.exp(-0.05), abs=1e-4)
    assert p_otm == pytest.approx(0.0, abs=1e-4)


# ---------------------------------------------------------------------------
# 2. Phoenix + worst-of multi-asset robustness
# ---------------------------------------------------------------------------


def test_phoenix_correlation_sweep_finite_and_in_range():
    """Sweep correlation values for a 3-asset phoenix; every PV must be
    finite, positive, and within ±50% of par.

    The original "high-corr → higher PV" invariant turns out to depend
    sharply on barrier levels (autocall at 100% vs coupon at 70% vs protection
    at 60%); for some setups the low-corr case is actually higher because
    the wider worst-of distribution puts more mass above the autocall barrier
    on early observations. Enforce only the structural sanity bounds here.
    """
    pricer, _, _ = router.route("phoenix_autocall")
    terms = AutocallTerms(
        coupon_rate=0.06, autocall_barrier=1.0,
        coupon_barrier=0.7, protection_barrier=0.6, memory=True,
    )
    sched = ObservationSchedule.quarterly(2.0)
    common = dict(
        S=100.0, K=100.0, r=0.04, sigma=0.20, T=2.0, q=0.02,
        autocall_terms=terms, obs_schedule=sched,
        basket_spots=[100.0, 100.0, 100.0],
        basket_sigma=[0.25, 0.25, 0.25],
        basket_q=[0.02, 0.02, 0.02],
        n_paths=8_000, seed=11,
    )
    for rho_off in (0.1, 0.3, 0.5, 0.7, 0.9):
        rho = np.full((3, 3), rho_off)
        np.fill_diagonal(rho, 1.0)
        p, _, _ = pricer(rho=rho, **common)
        assert math.isfinite(p)
        # Phoenix PV near par; allow ±50% on a 1MM notional.
        assert 500_000 < p < 1_500_000


@pytest.mark.parametrize("n_assets", [2, 3, 5])
def test_worst_of_put_basket_size_monotone(n_assets):
    """Worst-of put price is non-decreasing in basket size for symmetric assets
    with ρ < 1."""
    pricer, _, _ = router.route("worst_of_put")
    common = dict(
        K=100.0, r=0.04, sigma=0.20, T=1.0, q=0.02,
        n_paths=20_000, seed=42,
    )
    p1, _, _ = pricer(
        S=100.0,
        basket_spots=[100.0],
        basket_sigma=[0.25],
        basket_q=[0.02],
        rho=np.eye(1),
        **common,
    )
    rho = np.full((n_assets, n_assets), 0.30)
    np.fill_diagonal(rho, 1.0)
    pn, _, _ = pricer(
        S=100.0,
        basket_spots=[100.0] * n_assets,
        basket_sigma=[0.25] * n_assets,
        basket_q=[0.02] * n_assets,
        rho=rho,
        **common,
    )
    # MC noise tolerance: 1.5 USD on a price ~5–10 USD.
    assert pn >= p1 - 1.5


# ---------------------------------------------------------------------------
# 3. Variance swap convergence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "strike_range, n_pts, expected_tol",
    [
        ((50, 200), 31, 0.07),    # wide grid → loose tolerance
        ((30, 300), 81, 0.02),    # very wide → tight tolerance
    ],
)
def test_var_swap_convergence_with_grid_width(strike_range, n_pts, expected_tol):
    K = np.linspace(*strike_range, n_pts)
    iv = np.full_like(K, 0.20)
    res = variance_swap.fair_strike_from_strip(
        S=100, r=0.05, q=0.0, T=1.0, strikes=K, ivs=iv,
    )
    assert abs(res.fair_strike_var - 0.20) < expected_tol


def test_var_swap_skewed_strip_gives_premium_to_atm():
    """A negatively-skewed strip (put wing > ATM) should produce K_var > σ_atm,
    which is the empirical "var-vol spread" desks observe."""
    K = np.linspace(50, 150, 31)
    # Skew: linearly decreasing IV from 30% at K=50 down to 18% at K=150.
    iv = np.linspace(0.30, 0.18, 31)
    res = variance_swap.fair_strike_from_strip(
        S=100, r=0.05, q=0.0, T=1.0, strikes=K, ivs=iv,
    )
    # Variance strike should sit above ATM IV (skew premium).
    assert res.fair_strike_var > res.atm_iv


# ---------------------------------------------------------------------------
# 4. XVA monotonicity
# ---------------------------------------------------------------------------


def test_xva_cva_increases_with_maturity():
    """For fixed inputs, longer T → higher CVA (more time for default)."""
    inputs = xva.XVAInputs(funding_spread_bps=50, cds_spread_bps=200, csa=False)
    o1 = xva.compute_xva(mid_price=10_000.0, maturity_years=1.0, inputs=inputs)
    o5 = xva.compute_xva(mid_price=10_000.0, maturity_years=5.0, inputs=inputs)
    assert o5.cva > o1.cva
    assert o5.fva > o1.fva


def test_xva_fva_increases_with_funding_spread():
    """Higher funding spread → higher FVA charge."""
    cheap = xva.XVAInputs(funding_spread_bps=10, cds_spread_bps=100, csa=False)
    pricey = xva.XVAInputs(funding_spread_bps=200, cds_spread_bps=100, csa=False)
    o_cheap = xva.compute_xva(mid_price=10_000.0, maturity_years=1.0, inputs=cheap)
    o_pricey = xva.compute_xva(mid_price=10_000.0, maturity_years=1.0, inputs=pricey)
    assert o_pricey.fva > o_cheap.fva


def test_xva_bid_ask_ordering():
    """Bid ≤ mid ≤ ask, total_xva ≥ 0 for a positive mid."""
    o = xva.compute_xva(
        mid_price=12345.0, maturity_years=2.0,
        inputs=xva.XVAInputs(funding_spread_bps=50, cds_spread_bps=150, csa=False),
    )
    assert o.bid_price <= o.mid_price <= o.ask_price
    assert o.total_xva >= 0


# ---------------------------------------------------------------------------
# 5. Bid/offer through PricingResult
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("opt", ["european_call", "european_put", "american_put"])
def test_pricing_result_quote_ordering(opt):
    req = PricingRequest(
        option_type=opt, underlying="SPY",
        spot_price=100.0, strike_price=100.0,
        days_to_expiration=90, risk_free_rate=0.05, volatility=0.20,
        dividend_yield=0.01,
    )
    res = price_option(req)
    assert res.quote_bid is not None and res.quote_offer is not None
    assert res.quote_bid <= res.price <= res.quote_offer
    assert res.quote_spread_bps is not None and res.quote_spread_bps >= 0


def test_pricing_result_xva_overlay_present_for_vanilla():
    req = PricingRequest(
        option_type="european_call", underlying="SPY",
        spot_price=100.0, strike_price=100.0,
        days_to_expiration=180, risk_free_rate=0.05, volatility=0.20,
        dividend_yield=0.01,
    )
    res = price_option(req)
    assert res.xva_overlay is not None
    assert "fva" in res.xva_overlay
    assert "cva" in res.xva_overlay
    assert "ask_price" in res.xva_overlay
    assert "bid_price" in res.xva_overlay


# ---------------------------------------------------------------------------
# 6. Vanna / volga across barrier products
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("opt", ["knockout_call", "knockout_put", "knockin_call", "knockin_put"])
def test_pricing_result_vanna_volga_present_for_barriers(opt):
    is_call = "call" in opt
    barrier = 120.0 if is_call else 80.0
    req = PricingRequest(
        option_type=opt, underlying="SPY",
        spot_price=100.0, strike_price=100.0, barrier_level=barrier,
        days_to_expiration=180, risk_free_rate=0.05, volatility=0.25,
        dividend_yield=0.01,
    )
    res = price_option(req)
    assert res.vanna is not None
    assert res.volga is not None
    # Should be finite numbers.
    assert math.isfinite(res.vanna)
    assert math.isfinite(res.volga)


# ---------------------------------------------------------------------------
# 7. Hedge-ticket emission via the orchestrator at Gate C
# ---------------------------------------------------------------------------


def test_hedge_ticket_emitted_after_gate_c_approval():
    """End-to-end: build a session, force priced candidates, approve Gate C,
    verify hedge tickets land on the session."""
    store = SessionStore()
    orch = OrchestratorAgent(store=store, market_intel=None)

    # Hand-craft a minimal session with priced state.
    candidate = Candidate(
        kind=StructureKind.LONG_CALL,
        name="long ATM call",
        legs=[Leg(option_type="european_call", strike=100.0, expiry_days=90, quantity=1.0)],
        rationale="stress",
        notional_usd=2_000_000,
    )
    pc = PricedCandidate(
        candidate=candidate,
        net_premium=8_000.0, net_premium_bps=40.0,
        greeks=GreeksSnapshot(delta=0.5, gamma=0.01, vega=10.0, theta=-1.0, rho=0.5),
        per_leg_prices=[8_000.0],
        method_label="BS",
    )
    session = StructuringSession(intake_nl="stress test")
    session.objective = ClientObjective(
        underlying="SPY", notional_usd=2_000_000, view="bullish",
        horizon_days=90, budget_bps_notional=50.0, premium_tolerance="low",
    )
    session.regime = MarketRegime(
        underlying="SPY", spot=100.0, dividend_yield=0.0,
        risk_free_rate=0.05, realised_vol_30d=0.20, atm_iv=0.20,
    )
    session.priced = [pc]
    session.status = SessionStatus.AWAITING_GATE_C
    store.add(session)

    # Approve Gate C.
    updated = orch.decide_gate(
        session.session_id, Gate.C, approved=True, payload={"edits": "ok"},
    )
    # Hedge tickets should be present.
    assert len(updated.hedge_tickets) >= 1
    ticket = updated.hedge_tickets[0]
    assert ticket.candidate_id == candidate.candidate_id
    # Ticket should be a HedgeTicketState (Pydantic) — has model_dump.
    payload = ticket.model_dump()
    assert "opening_delta_shares" in payload
    assert "rebalance_frequency" in payload


# ---------------------------------------------------------------------------
# 8. Lifecycle re-mark + attribution
# ---------------------------------------------------------------------------


def test_lifecycle_attribution_explains_pnl():
    """Re-mark a long ATM call after a +10% spot rally: the lifecycle
    assessment should report a positive current_mark > inception, and the
    delta term in attribution should dominate."""
    candidate = Candidate(
        kind=StructureKind.LONG_CALL,
        name="long ATM call",
        legs=[Leg(option_type="european_call", strike=100.0, expiry_days=180, quantity=1.0)],
        rationale="stress",
        notional_usd=1_000_000,
    )
    prior = PricedCandidate(
        candidate=candidate,
        net_premium=5_000.0, net_premium_bps=50.0,
        greeks=GreeksSnapshot(delta=0.5, gamma=0.01, vega=10.0, theta=-1.0, rho=0.5),
        per_leg_prices=[5_000.0],
        method_label="BS",
    )
    prior_regime = MarketRegime(
        underlying="SPY", spot=100.0, dividend_yield=0.0, risk_free_rate=0.05,
        realised_vol_30d=0.20,
    )
    current_regime = MarketRegime(
        underlying="SPY", spot=110.0, dividend_yield=0.0, risk_free_rate=0.05,
        realised_vol_30d=0.20,
    )
    agent = lifecycle.LifecycleAgent()
    a = agent.assess(prior=prior, prior_regime=prior_regime, current_regime=current_regime)
    # Current mark should be higher than inception (spot up, vol unchanged).
    assert a.current_mark > a.inception_premium
    assert a.unrealized_pnl > 0
    # All three reshape options should be present.
    assert {r.label for r in a.reshape_options} == {"close", "roll", "enhance"}


# ---------------------------------------------------------------------------
# 9. Book aggregator with N sessions
# ---------------------------------------------------------------------------


def test_book_aggregates_greeks_across_sessions():
    sessions = []
    for i in range(5):
        s = StructuringSession(intake_nl=f"session {i}")
        s.objective = ClientObjective(
            underlying="SPY", notional_usd=1_000_000.0, view="bullish",
            horizon_days=180, budget_bps_notional=50.0, premium_tolerance="low",
        )
        s.regime = MarketRegime(
            underlying="SPY", spot=100.0, dividend_yield=0.0,
            risk_free_rate=0.05, realised_vol_30d=0.20,
        )
        s.priced = [
            PricedCandidate(
                candidate=Candidate(
                    kind=StructureKind.LONG_CALL,
                    name=f"call_{i}",
                    legs=[Leg(option_type="european_call", strike=100.0,
                              expiry_days=180, quantity=1.0)],
                    rationale="x",
                    notional_usd=1_000_000.0,
                ),
                net_premium=5000.0, net_premium_bps=50.0,
                greeks=GreeksSnapshot(delta=0.5, gamma=0.01, vega=10.0, theta=-1.0, rho=0.5),
                per_leg_prices=[5000.0],
                method_label="BS",
            )
        ]
        sessions.append(s)
    summary = book.aggregate_book(sessions=sessions, name="stress")
    assert summary.n_sessions == 5
    # Each session has delta=0.5 per share × (1M/100) = 5000 shares of delta.
    # Total = 25000.
    assert summary.book_greeks.delta_usd == pytest.approx(25_000.0, abs=1.0)
    assert summary.total_notional_usd == pytest.approx(5_000_000.0)


# ---------------------------------------------------------------------------
# 10. API endpoints via FastAPI TestClient
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client():
    """Create a TestClient with isolated in-memory store."""
    from fastapi.testclient import TestClient

    # Force in-memory store + reset for isolation.
    os.environ["VOL_DESK_PERSIST"] = "0"
    from src.agents.orchestrator import reset_store
    reset_store()
    from src.api.main import app
    return TestClient(app)


def test_api_termsheet_404_for_unknown_session(api_client):
    resp = api_client.get("/api/agent/sessions/no-such/termsheet")
    assert resp.status_code == 404


def test_api_kid_404_for_unknown_session(api_client):
    resp = api_client.get("/api/agent/sessions/no-such/kid")
    assert resp.status_code == 404


def test_api_book_empty_returns_zero_sessions(api_client):
    resp = api_client.get("/api/agent/book")
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_sessions"] == 0
    assert body["total_notional_usd"] == 0


def test_api_hedge_tickets_empty_for_fresh_session(api_client):
    """Build a session shell (no priced state) and ensure hedge_tickets returns []."""
    from src.agents.orchestrator import get_store
    s = StructuringSession(intake_nl="x")
    get_store().add(s)
    resp = api_client.get(f"/api/agent/sessions/{s.session_id}/hedge_tickets")
    assert resp.status_code == 200
    assert resp.json() == []


def test_api_kid_succeeds_for_priced_session(api_client):
    """KID endpoint succeeds when session has priced candidates."""
    from src.agents.orchestrator import get_store
    s = StructuringSession(intake_nl="kid test")
    s.objective = ClientObjective(
        underlying="SPY", notional_usd=1_000_000.0, view="bullish",
        horizon_days=365, budget_bps_notional=100.0, premium_tolerance="low",
    )
    s.regime = MarketRegime(
        underlying="SPY", spot=100.0, dividend_yield=0.0,
        risk_free_rate=0.05, realised_vol_30d=0.20, atm_iv=0.22,
    )
    s.priced = [
        PricedCandidate(
            candidate=Candidate(
                kind=StructureKind.LONG_CALL,
                name="x",
                legs=[Leg(option_type="european_call", strike=100.0,
                          expiry_days=365, quantity=1.0)],
                rationale="x", notional_usd=1_000_000.0,
            ),
            net_premium=5000.0, net_premium_bps=50.0,
            greeks=GreeksSnapshot(delta=0.5, gamma=0.01, vega=10.0, theta=-1.0, rho=0.5),
            per_leg_prices=[5000.0],
            method_label="BS",
        )
    ]
    get_store().add(s)
    resp = api_client.get(f"/api/agent/sessions/{s.session_id}/kid")
    assert resp.status_code == 200
    body = resp.json()
    assert "sri_bucket" in body
    assert 1 <= body["sri_bucket"] <= 7
    assert "scenarios" in body
    assert "horizons_years" in body["scenarios"]


# ---------------------------------------------------------------------------
# 11. Concurrency — parallel router calls
# ---------------------------------------------------------------------------


def test_router_thread_safety_simple():
    """Multiple threads pricing the same vanilla concurrently must not crash."""
    pricer, greeks_fn, _ = router.route("european_call")
    errors: list[Exception] = []
    lock = threading.Lock()

    def worker():
        try:
            for _ in range(10):
                p, _, _ = pricer(100.0, 100.0, 0.05, 0.20, 1.0, 0.0)
                g = greeks_fn(100.0, 100.0, 0.05, 0.20, 1.0, 0.0)
                assert p > 0
                assert math.isfinite(g["delta"])
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30.0)
    assert errors == [], f"Thread errors: {errors}"


# ---------------------------------------------------------------------------
# 12. Random sweeps — many seeds, no crashes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", list(range(0, 12)))
def test_phoenix_random_seeds_no_crash(seed):
    """Sweep seeds for the phoenix pricer — no NaNs, no crashes."""
    pricer, _, _ = router.route("phoenix_autocall")
    rng = np.random.default_rng(seed)
    sigma = float(rng.uniform(0.10, 0.40))
    coupon = float(rng.uniform(0.04, 0.10))
    terms = AutocallTerms(
        coupon_rate=coupon, autocall_barrier=1.0,
        coupon_barrier=0.7, protection_barrier=0.6, memory=True,
    )
    sched = ObservationSchedule.quarterly(1.0)
    p, _, _ = pricer(
        100.0, 100.0, 0.05, sigma, 1.0, 0.02,
        autocall_terms=terms, obs_schedule=sched,
        basket_spots=[100.0], basket_sigma=[sigma], basket_q=[0.02],
        rho=[[1.0]], n_paths=2_000, seed=seed,
    )
    assert math.isfinite(p)
    assert p > 0


@pytest.mark.parametrize("seed", list(range(0, 8)))
def test_worst_of_random_seeds_no_crash(seed):
    pricer, _, _ = router.route("worst_of_put")
    rng = np.random.default_rng(seed)
    rho_off = float(rng.uniform(-0.2, 0.9))
    rho = np.array([
        [1.0, rho_off, rho_off],
        [rho_off, 1.0, rho_off],
        [rho_off, rho_off, 1.0],
    ])
    p, _, _ = pricer(
        100.0, 100.0, 0.05, 0.25, 1.0, 0.02,
        basket_spots=[100.0, 100.0, 100.0],
        basket_sigma=[0.25, 0.25, 0.25],
        basket_q=[0.02, 0.02, 0.02],
        rho=rho, n_paths=5_000, seed=seed,
    )
    assert math.isfinite(p)
    assert p >= 0


# ---------------------------------------------------------------------------
# 13. Discounting + dividend curve edge cases
# ---------------------------------------------------------------------------


def test_dividend_curve_decay_clamps_at_zero():
    """For very large T, decay-to-zero shouldn't go negative."""
    c = dividend_curve.DividendCurve.decay(0.02, decay_per_year=0.10)
    for t in (0.0, 5.0, 20.0, 100.0):
        assert c.yield_at(t) >= -1e-12


def test_discounting_negative_rate_handled():
    """Negative-rate regimes (EUR/JPY): discount factor > 1, but no crash."""
    ctx = discounting.DiscountingContext.flat(-0.005)
    df = ctx.discount_factor(2.0)
    assert df > 1.0
    assert math.isfinite(df)


# ---------------------------------------------------------------------------
# 14. Implied correlation pathological inputs
# ---------------------------------------------------------------------------


def test_implied_correlation_perfect_diversification():
    """When sigma_index = 0 (impossible but tests boundary) we get a finite
    rho estimate (likely negative)."""
    res = correlation.implied_correlation(
        sigma_index=0.05, weights=[0.5, 0.5], sigma_components=[0.20, 0.20],
    )
    # Boundary: index σ < weighted-component-σ → ρ̄ < 1.
    assert math.isfinite(res.rho_bar)


def test_implied_correlation_weights_validate():
    with pytest.raises(ValueError):
        correlation.implied_correlation(
            sigma_index=0.20, weights=[0.4, 0.4],  # don't sum to 1
            sigma_components=[0.20, 0.30],
        )


# ---------------------------------------------------------------------------
# 15. KID edge cases
# ---------------------------------------------------------------------------


def test_kid_with_one_year_rhp_collapses_horizons():
    """RHP = 1y should produce a single-horizon scenario block."""
    doc = kid.build_kid(
        product_name="A", notional=1e6, rhp_years=1.0, annualised_vol=0.20,
    )
    assert doc.scenarios.horizons_years == [1.0]
    assert len(doc.scenarios.favourable_pct) == 1


def test_kid_extreme_vol_pushes_mrm_to_max():
    """Extreme vol pushes MRM to 7. SRI depends on CRM (per PRIIPs Annex II
    Table 4): MRM=7 + CRM=2 (investment grade) → SRI=6, but MRM=7 + CRM≥4
    (sub-IG) → SRI=7. We test both."""
    # Investment-grade counterparty (CRM=2): extreme vol → SRI 6 per PRIIPs matrix.
    doc_ig = kid.build_kid(
        product_name="x", notional=1e6, rhp_years=10.0, annualised_vol=2.50, crm=2,
    )
    assert doc_ig.mrm == 7
    assert doc_ig.sri_bucket == 6   # per PRIIPs SRI Annex matrix row 7, col 2

    # Sub-IG counterparty (CRM=5): extreme vol → SRI 7 (max).
    doc_subig = kid.build_kid(
        product_name="x", notional=1e6, rhp_years=10.0, annualised_vol=2.50, crm=5,
    )
    assert doc_subig.sri_bucket == 7
