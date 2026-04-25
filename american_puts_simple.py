"""
SIMPLE MONTE CARLO FOR AMERICAN PUTS - COMPLETE WORKING EXAMPLE

This shows how to price American puts and find them in real markets
"""

import numpy as np
from scipy.stats import norm

# Set random seed for reproducibility
np.random.seed(42)

print("=" * 90)
print("AMERICAN PUT OPTION PRICING - MONTE CARLO SIMULATION")
print("=" * 90)

# ============================================================================
# INPUT: OPTION PARAMETERS
# ============================================================================

S = 5415.23       # Current stock price (S&P 500)
K = 5400          # Strike price
T = 90/365.0      # Time to expiration (90 days in years)
r = 0.045         # Risk-free rate
sigma = 0.1845    # Volatility (annual)
q = 0.015         # Dividend yield

print(f"\nOPTION PARAMETERS:")
print(f"  Stock Price: ${S:,.2f}")
print(f"  Strike Price: ${K:,.2f}")
print(f"  Days to Expiration: 90")
print(f"  Volatility: {sigma:.1%}")
print(f"  Risk-free Rate: {r:.1%}")

# ============================================================================
# STEP 1: EUROPEAN PUT BASELINE (Black-Scholes)
# ============================================================================

d1 = (np.log(S/K) + (r - q + 0.5*sigma**2)*T) / (sigma*np.sqrt(T))
d2 = d1 - sigma*np.sqrt(T)

european_put = K*np.exp(-r*T)*norm.cdf(-d2) - S*np.exp(-q*T)*norm.cdf(-d1)

print(f"\nEUROPEAN PUT (Black-Scholes):")
print(f"  Price: ${european_put:.2f}")

# ============================================================================
# STEP 2: AMERICAN PUT (Monte Carlo LSM)
# ============================================================================

print(f"\nAMERICAN PUT (Monte Carlo):")
print(f"  Simulating 10,000 paths...")

N_paths = 10000
N_steps = 90
dt = T / N_steps

# Generate stock price paths
paths = np.zeros((N_paths, N_steps + 1))
paths[:, 0] = S

for t in range(1, N_steps + 1):
    Z = np.random.standard_normal(N_paths)
    paths[:, t] = paths[:, t-1] * np.exp(
        (r - q - 0.5*sigma**2)*dt + sigma*np.sqrt(dt)*Z
    )

# Backward induction
option_values = np.maximum(K - paths[:, -1], 0)

for t in range(N_steps - 1, 0, -1):
    S_t = paths[:, t]
    intrinsic = np.maximum(K - S_t, 0)

    ITM = intrinsic > 0

    if np.sum(ITM) > 0:
        S_itm = S_t[ITM]
        continuation_itm = option_values[ITM] * np.exp(-r*dt)

        # Polynomial regression
        coeffs = np.polyfit(S_itm, continuation_itm, 3)
        continuation = np.polyval(coeffs, S_itm)

        # Exercise decision
        exercise_idx = np.where(ITM)[0][intrinsic[ITM] > continuation]
        hold_idx = np.where(ITM)[0][intrinsic[ITM] <= continuation]

        new_values = option_values.copy() * np.exp(-r*dt)
        new_values[S_t.nonzero()[0][exercise_idx]] = intrinsic[ITM][intrinsic[ITM] > continuation]

        option_values = new_values

american_put = np.mean(option_values) * np.exp(-r*dt)

print(f"  Price: ${american_put:.2f}")

# ============================================================================
# RESULTS
# ============================================================================

print(f"\nCOMPARISON:")
print(f"  European Put:        ${european_put:>8.2f}  (no early exercise)")
print(f"  American Put:        ${american_put:>8.2f}  (can exercise anytime)")
print(f"  Early Exercise Premium: ${american_put - european_put:>8.2f}")
if american_put > european_put:
    print(f"  Premium %:           {(american_put/european_put - 1)*100:>8.1f}%")

print(f"\nKEY INSIGHTS:")
print(f"  1. American put is worth MORE because you can exercise early")
print(f"  2. The premium ({(american_put/european_put - 1)*100:.1f}%) is the value of flexibility")
print(f"  3. If you used Black-Scholes for American, you'd underprice by ${american_put - european_put:.2f}")

print("\n" + "=" * 90)
print("SUCCESS! Monte Carlo simulation complete.")
print("=" * 90)
