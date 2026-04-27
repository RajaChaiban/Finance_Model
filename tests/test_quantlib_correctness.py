
import pytest
import numpy as np
from src.engines import quantlib_engine

def test_quantlib_vanilla_call_benchmark():
    """
    Benchmark against the analytic Black-Scholes value.
    S=100, K=100, r=0.05, sigma=0.2, T=0.5, q=0.0 → BS = $6.88873.

    With Actual/365 day-count and round-half-up T-quantization (T=0.5 → 183
    days → T_eff = 0.5014), the QL price differs from BS-at-exact-T by
    ≤ 1 day worth of theta — about $0.011 for an ATM call at these params.
    """
    S, K, r, sigma, T, q = 100.0, 100.0, 0.05, 0.2, 0.5, 0.0
    res = quantlib_engine.greeks_ql(S, K, r, sigma, T, q, option_type='call', is_american=False)

    # Tightened from $0.10 (145 bp) to $0.015 (~22 bp). Bounded by ATM-call
    # theta × 0.5 day with safety margin.
    assert abs(res['price'] - 6.88873) < 0.015
    assert 0.5 < res['delta'] < 0.7
    assert res['gamma'] > 0
    assert res['vega'] > 0
    assert res['theta'] < 0
    assert res['rho'] > 0


def test_quantlib_vanilla_put_benchmark():
    """
    S=100, K=100, r=0.05, sigma=0.2, T=0.5, q=0.0 → BS = $4.41972.
    (The previous reference value 4.4523 was incorrect — confirm via put-call
    parity: C − P = S − K·exp(−rT) ≈ 2.469, gives P ≈ 6.889 − 2.469 = 4.420.)
    """
    S, K, r, sigma, T, q = 100.0, 100.0, 0.05, 0.2, 0.5, 0.0
    res = quantlib_engine.greeks_ql(S, K, r, sigma, T, q, option_type='put', is_american=False)

    assert abs(res['price'] - 4.41972) < 0.015
    assert -0.5 < res['delta'] < -0.3
    assert res['gamma'] > 0
    assert res['vega'] > 0
    assert res['theta'] < 0
    assert res['rho'] < 0

def test_quantlib_american_put_premium():
    """
    Verify that American Put price > European Put price.
    """
    S, K, r, sigma, T, q = 100.0, 100.0, 0.05, 0.2, 0.5, 0.02
    eur = quantlib_engine.greeks_ql(S, K, r, sigma, T, q, option_type='put', is_american=False)
    amer = quantlib_engine.greeks_ql(S, K, r, sigma, T, q, option_type='put', is_american=True)
    
    assert amer['price'] > eur['price']
    print(f"American Premium: {amer['price'] - eur['price']:.4f}")

def test_quantlib_knockout_vs_vanilla():
    """
    Verify that Knockout price < Vanilla price.
    """
    S, K, B, r, sigma, T, q = 100.0, 100.0, 80.0, 0.05, 0.2, 0.5, 0.02
    vanilla = quantlib_engine.greeks_ql(S, K, r, sigma, T, q, option_type='call', is_american=False)
    ko = quantlib_engine.greeks_knockout_ql(S, K, B, r, sigma, T, q, option_type='call')
    
    assert ko['price'] < vanilla['price']
    assert ko['delta'] > 0 # Knockout call delta should be positive if S is far from B

if __name__ == "__main__":
    # If run directly, just execute the benchmarks
    S, K, r, sigma, T, q = 100.0, 100.0, 0.05, 0.2, 0.5, 0.0
    res = quantlib_engine.greeks_ql(S, K, r, sigma, T, q, option_type='call', is_american=False)
    print(f"QL European Call Price: {res['price']:.6f} (Expected ~6.8887)")
    
    res_p = quantlib_engine.greeks_ql(S, K, r, sigma, T, q, option_type='put', is_american=False)
    print(f"QL European Put Price: {res_p['price']:.6f} (Expected ~4.4523)")
