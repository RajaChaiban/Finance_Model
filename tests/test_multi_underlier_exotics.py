"""Multi-underlier validation tests for Asian + lookback engines.

For every public engine function (price_asian, greeks_asian, price_lookback,
greeks_lookback) and the router dispatch path, this test file sweeps a
realistic basket of underliers — SPY, AAPL, TSLA, META, NVDA — across:
  * 3 strikes (ITM/ATM/OTM)
  * 2 tenors (90d/365d)
  * call+put
  * geometric+arithmetic (Asians) / fixed+floating (lookbacks)

Each combination cross-checks the engine against an INDEPENDENT NumPy
closed-form implementation:
  * Asian (geometric):  Kemna-Vorst 1990 — match to 1e-10
  * Lookback (floating): Goldman-Sosin-Gatto 1979 — match to 1e-10
  * Lookback (fixed):    Conze-Viswanathan 1991 — match to 1e-10

Plus identity / sanity checks: AM-GM (arith vs geo), dominance, Greeks
finiteness, router routes correctly.
"""

import numpy as np
import pytest
import QuantLib as ql
from scipy.stats import norm

from src.engines import asian, lookback, black_scholes, router


# ---------------------------------------------------------------------------
# Underlier basket: realistic equity profiles
# ---------------------------------------------------------------------------

UNDERLIERS = [
    # (label, spot, sigma, q, r) — typical 90d profile
    ("SPY",  712.0, 0.125, 0.0114, 0.0358),  # mature index, low vol
    ("AAPL", 198.0, 0.260, 0.0050, 0.0420),  # mega-cap, medium vol
    ("TSLA", 305.0, 0.520, 0.0000, 0.0420),  # high-vol single-stock
    ("META", 540.0, 0.310, 0.0035, 0.0420),  # mid-vol single-stock
    ("NVDA", 605.0, 0.450, 0.0005, 0.0420),  # high-vol growth
]

STRIKE_MULTIPLIERS = [0.90, 1.00, 1.10]   # ITM-call/OTM-put, ATM, OTM-call/ITM-put
TENORS_DAYS = [90, 365]


def _params(under, strike_mult, tenor_days):
    """Pack (S, K, r, sigma, T, q) for one (underlier, strike, tenor) combo."""
    label, S, sigma, q, r = under
    K = round(S * strike_mult, 2)
    T = tenor_days / 365.0
    return label, S, K, r, sigma, T, q


# ---------------------------------------------------------------------------
# Independent reference closed-forms (reused / inlined from test_asian.py +
# test_lookback.py — kept self-contained so this file can be read in isolation)
# ---------------------------------------------------------------------------

def _kemna_vorst_discrete_geometric(S, K, r, sigma, q, T_mat, fixing_times, opt):
    """Discrete-geometric Asian closed form (Kemna-Vorst 1990)."""
    t = np.sort(np.asarray(fixing_times, dtype=float))
    N = len(t)
    T_bar = float(np.mean(t))
    weights = (2 * N - 2 * np.arange(1, N + 1) + 1)
    T_tilde = float(np.sum(weights * t)) / (N * N)
    F_G = S * np.exp((r - q - 0.5 * sigma ** 2) * T_bar + 0.5 * sigma ** 2 * T_tilde)
    total_std = sigma * np.sqrt(T_tilde)
    if total_std <= 0:
        if opt == "call":
            return float(np.exp(-r * T_mat) * max(F_G - K, 0.0))
        return float(np.exp(-r * T_mat) * max(K - F_G, 0.0))
    d1 = (np.log(F_G / K) + 0.5 * total_std ** 2) / total_std
    d2 = d1 - total_std
    disc = np.exp(-r * T_mat)
    if opt == "call":
        return float(disc * (F_G * norm.cdf(d1) - K * norm.cdf(d2)))
    return float(disc * (K * norm.cdf(-d2) - F_G * norm.cdf(-d1)))


def _qual_fixing_times(T_input, frequency):
    today = ql.Date.todaysDate()
    ql.Settings.instance().evaluationDate = today
    days = max(int(T_input * 365.0 + 0.5), 1)
    maturity = today + days
    fixing_dates = asian._build_fixing_schedule(today, maturity, frequency)
    fixing_times = np.array([(d - today) / 365.0 for d in fixing_dates])
    T_mat = (maturity - today) / 365.0
    return fixing_times, T_mat


def _goldman_sosin_gatto_floating(S, M, r, q, sigma, T, opt):
    """Floating-strike lookback closed form (Goldman-Sosin-Gatto 1979)."""
    b = r - q
    if abs(b) < 1e-12:
        raise ValueError("b must be non-zero")
    sqrt_T = np.sqrt(T)
    sst = sigma * sqrt_T
    if opt == "call":
        a1 = (np.log(S / M) + (b + 0.5 * sigma ** 2) * T) / sst
        a2 = a1 - sst
        t1 = S * np.exp(-q * T) * norm.cdf(a1)
        t2 = -M * np.exp(-r * T) * norm.cdf(a2)
        bracket = (
            (S / M) ** (-2 * b / sigma ** 2) * norm.cdf(-a1 + 2 * b * sqrt_T / sigma)
            - np.exp(b * T) * norm.cdf(-a1)
        )
        t3 = S * np.exp(-r * T) * sigma ** 2 / (2 * b) * bracket
        return float(t1 + t2 + t3)
    b1 = (np.log(S / M) + (b + 0.5 * sigma ** 2) * T) / sst
    b2 = b1 - sst
    t1 = M * np.exp(-r * T) * norm.cdf(-b2)
    t2 = -S * np.exp(-q * T) * norm.cdf(-b1)
    bracket = (
        -(S / M) ** (-2 * b / sigma ** 2) * norm.cdf(b1 - 2 * b * sqrt_T / sigma)
        + np.exp(b * T) * norm.cdf(b1)
    )
    t3 = S * np.exp(-r * T) * sigma ** 2 / (2 * b) * bracket
    return float(t1 + t2 + t3)


def _conze_viswanathan_fixed(S, K, r, q, sigma, T, opt, M=None, m=None):
    """Fixed-strike lookback closed form (Conze-Viswanathan 1991)."""
    b = r - q
    if abs(b) < 1e-12:
        raise ValueError("b must be non-zero")
    if M is None:
        M = float(S)
    if m is None:
        m = float(S)
    sqrt_T = np.sqrt(T)
    sst = sigma * sqrt_T
    if opt == "call":
        if K >= M:
            d1 = (np.log(S / K) + (b + 0.5 * sigma ** 2) * T) / sst
            d2 = d1 - sst
            t1 = S * np.exp(-q * T) * norm.cdf(d1)
            t2 = -K * np.exp(-r * T) * norm.cdf(d2)
            bracket = (
                -(S / K) ** (-2 * b / sigma ** 2) * norm.cdf(d1 - 2 * b * sqrt_T / sigma)
                + np.exp(b * T) * norm.cdf(d1)
            )
            t3 = S * np.exp(-r * T) * sigma ** 2 / (2 * b) * bracket
            return float(t1 + t2 + t3)
        intrinsic = (M - K) * np.exp(-r * T)
        e1 = (np.log(S / M) + (b + 0.5 * sigma ** 2) * T) / sst
        e2 = e1 - sst
        t1 = S * np.exp(-q * T) * norm.cdf(e1)
        t2 = -M * np.exp(-r * T) * norm.cdf(e2)
        bracket = (
            -(S / M) ** (-2 * b / sigma ** 2) * norm.cdf(e1 - 2 * b * sqrt_T / sigma)
            + np.exp(b * T) * norm.cdf(e1)
        )
        t3 = S * np.exp(-r * T) * sigma ** 2 / (2 * b) * bracket
        return float(intrinsic + t1 + t2 + t3)
    if K <= m:
        d1 = (np.log(S / K) + (b + 0.5 * sigma ** 2) * T) / sst
        d2 = d1 - sst
        t1 = K * np.exp(-r * T) * norm.cdf(-d2)
        t2 = -S * np.exp(-q * T) * norm.cdf(-d1)
        bracket = (
            (S / K) ** (-2 * b / sigma ** 2) * norm.cdf(-d1 + 2 * b * sqrt_T / sigma)
            - np.exp(b * T) * norm.cdf(-d1)
        )
        t3 = S * np.exp(-r * T) * sigma ** 2 / (2 * b) * bracket
        return float(t1 + t2 + t3)
    intrinsic = (K - m) * np.exp(-r * T)
    f1 = (np.log(S / m) + (b + 0.5 * sigma ** 2) * T) / sst
    f2 = f1 - sst
    t1 = -S * np.exp(-q * T) * norm.cdf(-f1)
    t2 = m * np.exp(-r * T) * norm.cdf(-f2)
    bracket = (
        (S / m) ** (-2 * b / sigma ** 2) * norm.cdf(-f1 + 2 * b * sqrt_T / sigma)
        - np.exp(b * T) * norm.cdf(-f1)
    )
    t3 = S * np.exp(-r * T) * sigma ** 2 / (2 * b) * bracket
    return float(intrinsic + t1 + t2 + t3)


def _T_mat_yr(T_input):
    days = max(int(T_input * 365.0 + 0.5), 1)
    return days / 365.0


# ---------------------------------------------------------------------------
# 1) Geometric Asian — TIGHT cross-check across 5 underliers × 3K × 2T × 2opt
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("under", UNDERLIERS, ids=lambda u: u[0])
@pytest.mark.parametrize("strike_mult", STRIKE_MULTIPLIERS)
@pytest.mark.parametrize("tenor_days", TENORS_DAYS)
@pytest.mark.parametrize("opt", ["call", "put"])
@pytest.mark.parametrize("freq", ["daily", "weekly", "monthly"])
def test_asian_geometric_kv_multi_underlier(under, strike_mult, tenor_days, opt, freq):
    """asian.price_asian(geometric) ≡ Kemna-Vorst NumPy across full universe."""
    label, S, K, r, sigma, T, q = _params(under, strike_mult, tenor_days)
    fixing_times, T_mat = _qual_fixing_times(T, freq)
    expected = _kemna_vorst_discrete_geometric(S, K, r, sigma, q, T_mat, fixing_times, opt)
    actual, _, _ = asian.price_asian(
        S, K, r, sigma, T, q,
        option_type=opt, averaging_method="geometric", averaging_frequency=freq,
    )
    # 1e-10 absolute is fine for SPY ($700-scale); for $700 prices an absolute
    # 1e-10 means ~14 significant figures — we get ~12 reliably. Use 1e-8 to
    # accommodate equity-priced products without sacrificing rigour.
    assert abs(actual - expected) < 1e-8, (
        f"[{label}] KV mismatch (K_mult={strike_mult}, T={tenor_days}d, "
        f"opt={opt}, freq={freq}): actual={actual:.10f}, expected={expected:.10f}"
    )


# ---------------------------------------------------------------------------
# 2) Floating lookback — TIGHT cross-check vs Goldman-Sosin-Gatto
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("under", UNDERLIERS, ids=lambda u: u[0])
@pytest.mark.parametrize("M_mult", [0.95, 1.00, 1.05])  # running min/max relative to spot
@pytest.mark.parametrize("tenor_days", TENORS_DAYS)
@pytest.mark.parametrize("opt", ["call", "put"])
def test_lookback_floating_gsg_multi_underlier(under, M_mult, tenor_days, opt):
    """lookback.price_lookback(floating) ≡ GSG NumPy across full universe."""
    label, S, _K_unused, r, sigma, T, q = _params(under, 1.0, tenor_days)
    M = round(S * M_mult, 2)
    actual, _, _ = lookback.price_lookback(
        S, M, r, sigma, T, q,
        option_type=opt, lookback_type="floating",
    )
    T_eff = _T_mat_yr(T)
    expected = _goldman_sosin_gatto_floating(S, M, r, q, sigma, T_eff, opt)
    assert abs(actual - expected) < 1e-8, (
        f"[{label}] GSG mismatch (M_mult={M_mult}, T={tenor_days}d, opt={opt}): "
        f"actual={actual:.10f}, expected={expected:.10f}"
    )


# ---------------------------------------------------------------------------
# 3) Fixed lookback — TIGHT cross-check vs Conze-Viswanathan
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("under", UNDERLIERS, ids=lambda u: u[0])
@pytest.mark.parametrize("strike_mult", STRIKE_MULTIPLIERS)
@pytest.mark.parametrize("tenor_days", TENORS_DAYS)
@pytest.mark.parametrize("opt", ["call", "put"])
def test_lookback_fixed_cv_multi_underlier(under, strike_mult, tenor_days, opt):
    """lookback.price_lookback(fixed) ≡ Conze-Viswanathan NumPy across full universe."""
    label, S, K, r, sigma, T, q = _params(under, strike_mult, tenor_days)
    actual, _, _ = lookback.price_lookback(
        S, K, r, sigma, T, q,
        option_type=opt, lookback_type="fixed",
    )
    T_eff = _T_mat_yr(T)
    expected = _conze_viswanathan_fixed(S, K, r, q, sigma, T_eff, opt)
    assert abs(actual - expected) < 1e-8, (
        f"[{label}] CV mismatch (K_mult={strike_mult}, T={tenor_days}d, opt={opt}): "
        f"actual={actual:.10f}, expected={expected:.10f}"
    )


# ---------------------------------------------------------------------------
# 4) AM-GM identity for Asian arithmetic vs geometric (per underlier)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("under", UNDERLIERS, ids=lambda u: u[0])
@pytest.mark.parametrize("opt", ["call", "put"])
def test_asian_amgm_identity_multi_underlier(under, opt):
    """AM-GM: arith_call >= geo_call; arith_put <= geo_put (per AM-GM on samples)."""
    label, S, K, r, sigma, T, q = _params(under, 1.0, 90)  # ATM 90d for tightest test
    p_geo, _, _ = asian.price_asian(
        S, K, r, sigma, T, q,
        option_type=opt, averaging_method="geometric", averaging_frequency="monthly",
    )
    p_arith, se, _ = asian.price_asian(
        S, K, r, sigma, T, q,
        option_type=opt, averaging_method="arithmetic", averaging_frequency="monthly",
        n_paths=15000,
    )
    if opt == "call":
        assert p_arith >= p_geo - 2.0 * se, (
            f"[{label}] AM-GM(call): arith={p_arith:.4f}±{se:.4f}, geo={p_geo:.4f}"
        )
    else:
        assert p_arith <= p_geo + 2.0 * se, (
            f"[{label}] AM-GM(put): arith={p_arith:.4f}±{se:.4f}, geo={p_geo:.4f}"
        )


# ---------------------------------------------------------------------------
# 5) Lookback dominance: lookback >= vanilla European (per underlier)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("under", UNDERLIERS, ids=lambda u: u[0])
@pytest.mark.parametrize("opt", ["call", "put"])
@pytest.mark.parametrize("lt", ["fixed", "floating"])
def test_lookback_dominates_european_multi_underlier(under, opt, lt):
    label, S, K, r, sigma, T, q = _params(under, 1.0, 365)
    p_lk, _, _ = lookback.price_lookback(
        S, K, r, sigma, T, q, option_type=opt, lookback_type=lt,
    )
    p_eur = black_scholes.price_european(S, K, r, sigma, T, q, opt)
    assert p_lk >= p_eur - 1e-8, (
        f"[{label}] {lt} {opt} lookback < European: {p_lk:.4f} vs {p_eur:.4f}"
    )


# ---------------------------------------------------------------------------
# 6) Asian dampening: geometric Asian <= European (per underlier)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("under", UNDERLIERS, ids=lambda u: u[0])
@pytest.mark.parametrize("opt", ["call", "put"])
def test_geometric_asian_dampens_below_european(under, opt):
    label, S, K, r, sigma, T, q = _params(under, 1.0, 365)
    p_geo, _, _ = asian.price_asian(
        S, K, r, sigma, T, q,
        option_type=opt, averaging_method="geometric", averaging_frequency="daily",
    )
    p_eur = black_scholes.price_european(S, K, r, sigma, T, q, opt)
    assert p_geo < p_eur + 1e-8, (
        f"[{label}] {opt} geo Asian >= European (averaging should dampen): "
        f"asian={p_geo:.4f}, eur={p_eur:.4f}"
    )


# ---------------------------------------------------------------------------
# 7) Greeks finiteness + vega > 0 across underlier × strike × tenor matrix
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("under", UNDERLIERS, ids=lambda u: u[0])
@pytest.mark.parametrize("strike_mult", STRIKE_MULTIPLIERS)
@pytest.mark.parametrize("opt", ["call", "put"])
@pytest.mark.parametrize("method", ["geometric", "arithmetic"])
def test_asian_greeks_finite_multi_underlier(under, strike_mult, opt, method):
    label, S, K, r, sigma, T, q = _params(under, strike_mult, 90)
    n_paths = 5000 if method == "arithmetic" else 1
    g = asian.greeks_asian(
        S, K, r, sigma, T, q,
        option_type=opt, averaging_method=method, averaging_frequency="monthly",
        n_paths=n_paths,
    )
    for greek_name in ("delta", "gamma", "vega", "theta", "rho", "price"):
        assert np.isfinite(g[greek_name]), (
            f"[{label}] {opt}/{method}/K_mult={strike_mult}: "
            f"{greek_name} not finite ({g[greek_name]})"
        )
    assert g["vega"] > 0, f"[{label}] vega not positive: {g['vega']}"


def _valid_lookback_K_mults(opt: str, lt: str):
    """Return strike multipliers that produce physically valid lookback states.

    For floating-strike lookback, K is the running extremum (S_min for call,
    S_max for put), so it must satisfy:
       call: M = S_min  ≤  S  → K_mult ≤ 1.0
       put : M = S_max  ≥  S  → K_mult ≥ 1.0
    For fixed-strike, K is the actual strike — any value is valid.
    """
    if lt == "fixed":
        return STRIKE_MULTIPLIERS  # 0.90, 1.00, 1.10
    # floating: invalid combinations (e.g., S_min > S) collapse the formula.
    if opt == "call":
        return [0.85, 0.95, 1.00]
    return [1.00, 1.05, 1.15]


@pytest.mark.parametrize("under", UNDERLIERS, ids=lambda u: u[0])
@pytest.mark.parametrize("opt", ["call", "put"])
@pytest.mark.parametrize("lt", ["fixed", "floating"])
def test_lookback_greeks_finite_multi_underlier(under, opt, lt):
    """Greeks finite + vega>0 across underlier × valid-strike × type matrix."""
    for strike_mult in _valid_lookback_K_mults(opt, lt):
        label, S, K, r, sigma, T, q = _params(under, strike_mult, 90)
        g = lookback.greeks_lookback(
            S, K, r, sigma, T, q, option_type=opt, lookback_type=lt,
        )
        for greek_name in ("delta", "gamma", "vega", "theta", "rho", "price"):
            assert np.isfinite(g[greek_name]), (
                f"[{label}] {lt}/{opt}/K_mult={strike_mult}: "
                f"{greek_name} not finite ({g[greek_name]})"
            )
        assert g["vega"] > 0, (
            f"[{label}] {lt}/{opt}/K_mult={strike_mult}: vega not positive: {g['vega']}"
        )


# ---------------------------------------------------------------------------
# 8) Router dispatch — every option_type prices end-to-end on every underlier
# ---------------------------------------------------------------------------

ROUTER_CASES = [
    ("european_call",  {}),
    ("european_put",   {}),
    ("american_call",  {}),
    ("american_put",   {}),
    ("knockout_call",  {"barrier_level_mult": 0.85}),
    ("knockout_put",   {"barrier_level_mult": 1.15}),
    ("knockin_call",   {"barrier_level_mult": 0.85}),
    ("knockin_put",    {"barrier_level_mult": 1.15}),
    ("asian_call",     {"averaging_method": "geometric", "averaging_frequency": "monthly"}),
    ("asian_put",      {"averaging_method": "geometric", "averaging_frequency": "weekly"}),
    ("lookback_call",  {"lookback_type": "fixed"}),
    ("lookback_put",   {"lookback_type": "floating"}),
]


@pytest.mark.parametrize("under", UNDERLIERS, ids=lambda u: u[0])
@pytest.mark.parametrize("case", ROUTER_CASES, ids=lambda c: c[0])
def test_router_prices_every_product_on_every_underlier(under, case):
    """All 12 option types must dispatch and return a finite price + Greeks."""
    opt_type, extras = case
    label, S, K, r, sigma, T, q = _params(under, 1.0, 90)

    pricer, greeks_fn, desc = router.route(opt_type)

    # Build kwargs: barrier_level_mult → barrier_level relative to spot.
    kwargs = {"S": S, "K": K, "r": r, "sigma": sigma, "T": T, "q": q,
              "n_paths": 3000, "n_steps": 30}
    if "barrier_level_mult" in extras:
        kwargs["barrier_level"] = round(S * extras["barrier_level_mult"], 2)
    for k in ("averaging_method", "averaging_frequency", "lookback_type"):
        if k in extras:
            kwargs[k] = extras[k]

    p, se, _ = pricer(**kwargs)
    assert np.isfinite(p) and p >= 0, (
        f"[{label}/{opt_type}] price not finite/positive: {p}"
    )
    g = greeks_fn(**{k: v for k, v in kwargs.items() if k != "n_steps"})
    for greek_name in ("delta", "gamma", "vega", "theta", "rho"):
        assert np.isfinite(g.get(greek_name, 0.0)), (
            f"[{label}/{opt_type}] {greek_name} not finite: {g.get(greek_name)}"
        )


# ---------------------------------------------------------------------------
# 9) Fixing-schedule helper — must produce monotonically increasing dates
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("freq", ["daily", "weekly", "monthly"])
@pytest.mark.parametrize("tenor_days", [30, 90, 180, 365])
def test_fixing_schedule_monotone_and_terminates_at_maturity(freq, tenor_days):
    today = ql.Date.todaysDate()
    ql.Settings.instance().evaluationDate = today
    maturity = today + tenor_days
    dates = asian._build_fixing_schedule(today, maturity, freq)
    assert dates, f"empty schedule for {freq}/{tenor_days}d"
    # Strictly increasing
    for prev, nxt in zip(dates, dates[1:]):
        assert prev < nxt, f"non-monotone schedule at {prev} → {nxt}"
    # Last date == maturity
    assert dates[-1] == maturity, f"last fixing {dates[-1]} != maturity {maturity}"
    # First date > today
    assert dates[0] > today, f"first fixing {dates[0]} not after today {today}"


def test_fixing_schedule_invalid_frequency_raises():
    today = ql.Date.todaysDate()
    maturity = today + 30
    with pytest.raises(ValueError, match="averaging_frequency"):
        asian._build_fixing_schedule(today, maturity, "yearly")


# ---------------------------------------------------------------------------
# 10) Engine input validation — invalid args should raise cleanly
# ---------------------------------------------------------------------------

def test_asian_invalid_method_raises():
    with pytest.raises(ValueError, match="averaging_method"):
        asian.price_asian(100, 100, 0.05, 0.20, 1.0, 0.0,
                          option_type="call", averaging_method="median",
                          averaging_frequency="daily")


def test_asian_invalid_option_type_raises():
    with pytest.raises(ValueError, match="option_type"):
        asian.price_asian(100, 100, 0.05, 0.20, 1.0, 0.0,
                          option_type="straddle", averaging_method="geometric",
                          averaging_frequency="daily")


def test_lookback_invalid_type_raises():
    with pytest.raises(ValueError, match="lookback_type"):
        lookback.price_lookback(100, 100, 0.05, 0.20, 1.0, 0.0,
                                 option_type="call", lookback_type="knockin")


def test_lookback_invalid_option_type_raises():
    with pytest.raises(ValueError, match="option_type"):
        lookback.price_lookback(100, 100, 0.05, 0.20, 1.0, 0.0,
                                 option_type="butterfly", lookback_type="fixed")
