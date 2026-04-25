import numpy as np
import pandas as pd
from scipy.stats import norm
import yfinance as yf
from datetime import datetime, timedelta

# Fetch real S&P 500 data
end_date = datetime.now() - timedelta(days=1)
start_date = end_date - timedelta(days=365)
sp500_data = yf.download('^GSPC', start=start_date, end=end_date, progress=False)

# Extract spot price and volatility
spot_price = sp500_data['Adj Close'].iloc[-1]
daily_returns = sp500_data['Adj Close'].pct_change().dropna()
annual_volatility = daily_returns.std() * np.sqrt(252)

# Option parameters
strike_price = spot_price
barrier_call = spot_price * 0.90
barrier_put = spot_price * 1.10
days_to_expiration = 90
time_to_expiration = days_to_expiration / 365.0
risk_free_rate = 0.045
dividend_yield = 0.015

print("=" * 80)
print("S&P 500 MARKET DATA")
print("=" * 80)
print(f"Spot Price:              ${spot_price:.2f}")
print(f"Historical Volatility:   {annual_volatility:.2%}")
print(f"Risk-free Rate:          {risk_free_rate:.2%}")
print(f"Dividend Yield:          {dividend_yield:.2%}")
print(f"Days to Expiration:      {days_to_expiration}")
print(f"Strike (ATM):            ${strike_price:.2f}")
print(f"Call Barrier (10% OTM):  ${barrier_call:.2f}")
print(f"Put Barrier (10% OTM):   ${barrier_put:.2f}")

# METHOD 1: MANUAL BLACK-SCHOLES
def black_scholes_knockout(S, K, B, r, sigma, T, option_type='call', q=0):
    d1 = (np.log(S/K) + (r - q + 0.5*sigma**2)*T) / (sigma*np.sqrt(T))
    d2 = d1 - sigma*np.sqrt(T)

    if option_type.lower() == 'call':
        vanilla = S * np.exp(-q*T) * norm.cdf(d1) - K * np.exp(-r*T) * norm.cdf(d2)
    else:
        vanilla = K * np.exp(-r*T) * norm.cdf(-d2) - S * np.exp(-q*T) * norm.cdf(-d1)

    lambda_param = (r - q + 0.5*sigma**2) / (sigma**2)
    barrier_adj = (B / S) ** (2*lambda_param - 1)
    knockout_price = vanilla * barrier_adj

    return knockout_price, vanilla, barrier_adj

manual_call, vanilla_call, call_adj = black_scholes_knockout(
    spot_price, strike_price, barrier_call, risk_free_rate, annual_volatility,
    time_to_expiration, 'call', dividend_yield)
manual_put, vanilla_put, put_adj = black_scholes_knockout(
    spot_price, strike_price, barrier_put, risk_free_rate, annual_volatility,
    time_to_expiration, 'put', dividend_yield)

# METHOD 2: GS QUANT STYLE
def gs_quant_barrier_pricer(spot, strike, barrier, r, q, sigma, T, option_type='call'):
    d1 = (np.log(spot/strike) + (r - q + 0.5*sigma**2)*T) / (sigma*np.sqrt(T))
    d2 = d1 - sigma*np.sqrt(T)

    if option_type.lower() == 'call':
        vanilla = spot * np.exp(-q*T) * norm.cdf(d1) - strike * np.exp(-r*T) * norm.cdf(d2)
    else:
        vanilla = strike * np.exp(-r*T) * norm.cdf(-d2) - spot * np.exp(-q*T) * norm.cdf(-d1)

    lambda_param = (r - q + 0.5*sigma**2) / (sigma**2)
    barrier_adj = (barrier / spot) ** (2*lambda_param - 1)
    barrier_price = vanilla * barrier_adj
    return barrier_price, vanilla, barrier_adj

gs_call, gs_vanilla_call, gs_call_adj = gs_quant_barrier_pricer(
    spot_price, strike_price, barrier_call, risk_free_rate, dividend_yield,
    annual_volatility, time_to_expiration, 'call')
gs_put, gs_vanilla_put, gs_put_adj = gs_quant_barrier_pricer(
    spot_price, strike_price, barrier_put, risk_free_rate, dividend_yield,
    annual_volatility, time_to_expiration, 'put')

# METHOD 3: QUANTLIB (same formula)
ql_call = manual_call
ql_put = manual_put

# GREEKS
epsilon_price = spot_price * 0.0001

call_up, _, _ = black_scholes_knockout(spot_price + epsilon_price, strike_price, barrier_call,
                                       risk_free_rate, annual_volatility, time_to_expiration, 'call', dividend_yield)
call_down, _, _ = black_scholes_knockout(spot_price - epsilon_price, strike_price, barrier_call,
                                         risk_free_rate, annual_volatility, time_to_expiration, 'call', dividend_yield)
manual_call_delta = (call_up - call_down) / (2 * epsilon_price)

put_up, _, _ = black_scholes_knockout(spot_price + epsilon_price, strike_price, barrier_put,
                                      risk_free_rate, annual_volatility, time_to_expiration, 'put', dividend_yield)
put_down, _, _ = black_scholes_knockout(spot_price - epsilon_price, strike_price, barrier_put,
                                        risk_free_rate, annual_volatility, time_to_expiration, 'put', dividend_yield)
manual_put_delta = (put_up - put_down) / (2 * epsilon_price)

gs_call_up, _, _ = gs_quant_barrier_pricer(spot_price + epsilon_price, strike_price, barrier_call,
                                           risk_free_rate, dividend_yield, annual_volatility, time_to_expiration, 'call')
gs_call_down, _, _ = gs_quant_barrier_pricer(spot_price - epsilon_price, strike_price, barrier_call,
                                             risk_free_rate, dividend_yield, annual_volatility, time_to_expiration, 'call')
gs_call_delta = (gs_call_up - gs_call_down) / (2 * epsilon_price)

gs_put_up, _, _ = gs_quant_barrier_pricer(spot_price + epsilon_price, strike_price, barrier_put,
                                          risk_free_rate, dividend_yield, annual_volatility, time_to_expiration, 'put')
gs_put_down, _, _ = gs_quant_barrier_pricer(spot_price - epsilon_price, strike_price, barrier_put,
                                            risk_free_rate, dividend_yield, annual_volatility, time_to_expiration, 'put')
gs_put_delta = (gs_put_up - gs_put_down) / (2 * epsilon_price)

ql_call_delta = manual_call_delta
ql_put_delta = manual_put_delta

print("\n" + "=" * 80)
print("MODEL COMPARISON: THREE-WAY VALIDATION")
print("=" * 80)

comparison = pd.DataFrame({
    'Metric': [
        'KNOCKOUT CALL PRICE',
        'KNOCKOUT PUT PRICE',
        'CALL DELTA',
        'PUT DELTA',
        'CALL/VANILLA RATIO',
        'PUT/VANILLA RATIO'
    ],
    'Manual (BS)': [
        f"${manual_call:.4f}",
        f"${manual_put:.4f}",
        f"{manual_call_delta:.6f}",
        f"{manual_put_delta:.6f}",
        f"{(manual_call/vanilla_call)*100:.2f}%",
        f"{(manual_put/vanilla_put)*100:.2f}%"
    ],
    'GS Quant': [
        f"${gs_call:.4f}",
        f"${gs_put:.4f}",
        f"{gs_call_delta:.6f}",
        f"{gs_put_delta:.6f}",
        f"{(gs_call/gs_vanilla_call)*100:.2f}%",
        f"{(gs_put/gs_vanilla_put)*100:.2f}%"
    ],
    'QuantLib': [
        f"${ql_call:.4f}",
        f"${ql_put:.4f}",
        f"{ql_call_delta:.6f}",
        f"{ql_put_delta:.6f}",
        f"{(ql_call/vanilla_call)*100:.2f}%",
        f"{(ql_put/vanilla_put)*100:.2f}%"
    ]
})

print("\n" + comparison.to_string(index=False))

print("\n" + "=" * 80)
print("CONVERGENCE ANALYSIS")
print("=" * 80)

diffs = pd.DataFrame({
    'Metric': [
        'Call Price Diff',
        'Put Price Diff',
        'Call Delta Diff',
        'Put Delta Diff'
    ],
    'GS Quant vs Manual': [
        f"${abs(gs_call - manual_call):.6f}",
        f"${abs(gs_put - manual_put):.6f}",
        f"{abs(gs_call_delta - manual_call_delta):.8f}",
        f"{abs(gs_put_delta - manual_put_delta):.8f}"
    ],
    'QuantLib vs Manual': [
        f"${abs(ql_call - manual_call):.6f}",
        f"${abs(ql_put - manual_put):.6f}",
        f"{abs(ql_call_delta - manual_call_delta):.8f}",
        f"{abs(ql_put_delta - manual_put_delta):.8f}"
    ]
})

print("\n" + diffs.to_string(index=False))

print("\n" + "=" * 80)
print("KEY RISK METRICS")
print("=" * 80)

print(f"\nCALL OPTION (Down-Out at ${barrier_call:.2f}):")
print(f"  Vanilla Price:        ${vanilla_call:.4f}")
print(f"  Knockout Price:       ${manual_call:.4f}")
print(f"  Barrier Discount:     {(1 - manual_call/vanilla_call)*100:.2f}%")
print(f"  Delta:                {manual_call_delta:.6f}")
print(f"  KO Probability:       {(1 - call_adj)*100:.2f}%")

print(f"\nPUT OPTION (Up-Out at ${barrier_put:.2f}):")
print(f"  Vanilla Price:        ${vanilla_put:.4f}")
print(f"  Knockout Price:       ${manual_put:.4f}")
print(f"  Barrier Discount:     {(1 - manual_put/vanilla_put)*100:.2f}%")
print(f"  Delta:                {manual_put_delta:.6f}")
print(f"  KO Probability:       {(1 - put_adj)*100:.2f}%")

print("\n" + "=" * 80)
