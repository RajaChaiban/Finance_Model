# MONTE CARLO SIMULATION EXPLAINED
## What It Is, Why You Need It, When To Use It

---

## INTUITIVE EXPLANATION: THE DART BOARD ANALOGY

### Imagine you want to know: "What's the probability the S&P 500 ends above 5600 in 90 days?"

**BAD WAY (Guessing):**
"Hmm, it's at 5415, volatility is 18%... I think... 40%?"
❌ No math, no confidence

**SMART WAY (Black-Scholes):**
Use the formula to calculate exact probability
✓ Takes 1ms, gives exact answer = 43.7%

**FLEXIBLE WAY (Monte Carlo):**
Throw 100,000 darts at a dartboard representing "all possible futures"
- Dart 1: S&P goes to 5,201 (below barrier)
- Dart 2: S&P goes to 5,823 (above barrier)
- Dart 3: S&P goes to 5,567 (above barrier)
- ... (100,000 darts total)
Count: 43,700 darts above 5,600 = 43.7% probability
✓ Takes 2 seconds, approximates answer = 43.7%

**Both give the same answer for simple cases, but Monte Carlo works when Black-Scholes can't.**

---

## THE PROBLEM MONTE CARLO SOLVES

### **Problem 1: American Put (Early Exercise)**

You own a put option on S&P 500, strike $5,400, 90 days to expiration.

**Day 30:** Market crashes, S&P drops to $4,800
- Your put is worth $600 intrinsic value
- Question: Should you exercise now (take $600) OR hold for more downside?

**Black-Scholes says:**
"Assume you hold to expiration" 
→ Calculates price = $2.50 (doesn't account for early exercise)
❌ **WRONG** - You actually exercise now and get $600!

**Monte Carlo says:**
For each simulated path:
  "At each day, should I exercise or wait?"
  → Uses regression to estimate continuation value
  → Compares: Exercise now ($600) vs Expected future payoff ($620)
  → If exercise > future payoff: EXERCISE
→ Iterates backward through time
→ Gets correct price = $11.35 (includes early exercise premium)
✓ **CORRECT**

**Real dollar impact:** $11.35 - $2.50 = **$8.85 per contract mispricing!**

---

### **Problem 2: Path-Dependent Payoff (Asian Option)**

**Vanilla Call:**
Only cares about final price on day 90
"If S&P ends above 5400, I get the profit"
✓ Black-Scholes handles this easily

**Asian Call (Average Price Option):**
Payoff = max(average price from now to day 90 - 5400, 0)
- It's not just about the final price
- It's about the AVERAGE of ALL prices along the way

Example paths:
```
Path 1: 5415 → 5500 → 5600 → 5200 → 5400
        Average = 5423 ✓ In the money

Path 2: 5415 → 5700 → 5900 → 5100 → 5000
        Average = 5423 ✓ Same average, very different journey
```

**Black-Scholes:**
"There's no closed-form formula for Asian options"
❌ Can't price it analytically

**Monte Carlo:**
Simulate 10,000 paths
For each path: calculate average price → payoff → discount
Average all payoffs = Asian option price
✓ **Works perfectly**

---

### **Problem 3: Multi-Asset Correlation**

**Basket Call on 3 stocks:** Apple, Microsoft, Google

**Black-Scholes:**
Assumes all assets move independently
❌ Ignores correlations between stocks

**Reality:**
Apple and Microsoft are highly correlated (both tech)
If Apple goes down, Microsoft probably goes down too
This REDUCES basket value (less diversification benefit)

**Monte Carlo:**
```python
# Create correlation matrix
corr_matrix = [
    [1.0,  0.75, 0.65],    # Apple-Apple, Apple-MSFT, Apple-Google
    [0.75, 1.0,  0.70],    # MSFT-Apple, MSFT-MSFT, MSFT-Google
    [0.65, 0.70, 1.0]      # Google-Apple, Google-MSFT, Google-Google
]

# Generate correlated random numbers
# Simulate all 3 stocks moving TOGETHER (correlated)
# Calculate basket payoff
# Repeat 10,000 times
```
✓ Captures real correlations

---

## HOW MONTE CARLO ACTUALLY WORKS

### **The Algorithm (Step by Step)**

```
MONTE CARLO ALGORITHM
═══════════════════════════════════════════════════════════

INPUT:
  - Spot price S = 5415
  - Strike K = 5400
  - Volatility σ = 0.1845
  - Time T = 90/365 = 0.247 years
  - Risk-free rate r = 0.045
  - Number of paths N = 10,000

STEP 1: Generate Random Paths
───────────────────────────────
for path i = 1 to N:
    
    Start at spot: S_0 = 5415
    
    for time step t = 1 to T:
        # Random shock (Brownian motion)
        Z = random normal number ~ N(0,1)
        
        # Stock price moves by random amount
        dS = μ × S × dt + σ × S × dZ × sqrt(dt)
        
        S_t = S_{t-1} + dS
        
        # Now you have price at time t
        # Continue until maturity
    
    # At end of path, you have S_T
    payoff_i = max(S_T - K, 0)  # Call payoff
    
    # Discount back to today
    PV_i = payoff_i × e^(-r×T)


STEP 2: Average All Paths
──────────────────────────
option_price = (PV_1 + PV_2 + ... + PV_N) / N


STEP 3: Calculate Standard Error
─────────────────────────────────
std_error = std(PV_payoffs) / sqrt(N)
confidence_interval = option_price ± 1.96 × std_error

Example:
  Price = $12.34
  Std Error = $0.15
  95% CI = [$12.04, $12.64]
  
  (With more paths, CI gets smaller)


OUTPUT:
  option_price ≈ $12.34 ± $0.15
```

---

## VISUAL EXAMPLE: ONE MONTE CARLO PATH

```
Day 0:    Spot = 5415 (start here)

Day 10:   Random shock = +2.1%
          5415 → 5529

Day 20:   Random shock = -1.5%
          5529 → 5445

Day 30:   Random shock = +3.2%
          5445 → 5619

Day 40:   Random shock = -4.1%
          5619 → 5390

Day 50:   Random shock = +1.8%
          5390 → 5487

Day 60:   Random shock = +2.3%
          5487 → 5612

Day 70:   Random shock = -0.8%
          5612 → 5556

Day 80:   Random shock = +1.2%
          5556 → 5623

Day 90:   Final price = 5623
          
          Payoff = max(5623 - 5400, 0) = 223
          PV = 223 × e^(-0.045×0.247) = 220

This is ONE path. Repeat 9,999 more times.
Average all 10,000 payoffs = Option price
```

---

## WHY THE RANDOMNESS WORKS

You might think: "Aren't you just guessing with random numbers?"

**No! Here's why it works:**

```
Law of Large Numbers:
═══════════════════════════════════════════════════════════

With 1 random path:
  "S&P goes to 5,500"
  ✗ Meaningless, could be wrong

With 10 random paths:
  "Average endpoint: 5,410"
  ~ Might be close, might be wrong

With 100 random paths:
  "Average endpoint: 5,409"
  ~~ Getting closer to true expectation

With 10,000 random paths:
  "Average endpoint: 5,408"
  ✓✓ Almost surely equals true expectation

With 100,000 random paths:
  "Average endpoint: 5,408.1"
  ✓✓✓ Converges to mathematical expectation

The random errors cancel out.
With enough paths, you get the TRUE answer.
```

**Mathematical Property:**
```
Standard Error = σ_payoff / √N

N = 100        → Std Error = $1.50
N = 1,000      → Std Error = $0.47
N = 10,000     → Std Error = $0.15
N = 100,000    → Std Error = $0.047

More paths = smaller error = more accurate
But also: 10x more paths = 3x improvement in accuracy
(due to sqrt relationship)
```

---

## WHEN MONTE CARLO IS NECESSARY (Can't Use Black-Scholes)

### **TYPE 1: American Options (Early Exercise)**

```python
# AMERICAN PUT EXAMPLE
price_american = monte_carlo_lsm(
    spot=5415,
    strike=5400,
    T=90/365,
    exercise_type='american'  # ← Can exercise anytime
)

# Black-Scholes can't handle this
# Must simulate to know if/when to exercise
```

**Why Black-Scholes fails:**
```
Black-Scholes formula assumes:
  "Hold until expiration"
  
But American option holder says:
  "I'll exercise whenever it's optimal"
  
This dynamic decision can't be captured by static formula
→ Need Monte Carlo to simulate backward through time
→ At each node, decide: Exercise now or wait?
```

**Typical American premium:**
```
Vanilla European Put:   $3.45
American Put:          $11.35
Early Exercise Premium: $7.90  (129% more!)

← This premium can ONLY be found by Monte Carlo
```

---

### **TYPE 2: Path-Dependent Options**

**Asian Option (Average Price):**
```python
payoff = max(average_price - strike, 0)
# Depends on entire path, not just endpoint
# Black-Scholes: "No formula exists"
# Monte Carlo: "Simulate, track average, calculate payoff"
```

**Lookback Option:**
```python
payoff = max(max_price_ever - strike, 0)
# Depends on highest price ever seen
# Black-Scholes: "No formula"
# Monte Carlo: "Track maximum as you go, payoff at end"
```

**Barrier Option with Discrete Monitoring:**
```python
# Barrier checked only on specific dates (not continuous)
# Black-Scholes: "Assumes continuous monitoring"
# Monte Carlo: "Check on exact dates specified"
```

---

### **TYPE 3: Multi-Asset Derivatives**

**Basket Option:**
```python
payoff = max(0.4×Apple + 0.3×MSFT + 0.3×Google - strike, 0)

# Assets are correlated
# Black-Scholes: "Can't handle correlation between multiple assets"
# Monte Carlo: "Generate correlated random numbers, simulate all 3 together"
```

**Portfolio derivative:**
```python
payoff = function(price_equity, price_bond, price_commodity)
# Three assets, all moving together
# Black-Scholes: Impossible
# Monte Carlo: Simulate all three, calculate payoff
```

---

### **TYPE 4: Exotic/Complex Payoffs**

**CLO Tranche (Collateralized Loan Obligation):**
```python
# Payoff depends on:
# - Defaults of 100+ underlying loans
# - Recovery rates (random)
# - Loss cascade through capital structure
# - Multiple state variables
# Black-Scholes: Completely inapplicable
# Monte Carlo: Only way to value it
```

**Convertible Bond:**
```python
# Payoff depends on:
# - Bond value (interest rate risk)
# - Equity value (stock price)
# - Call/put provisions (optionality)
# - Credit spread
# Black-Scholes: Too many moving parts
# Monte Carlo: Build a full model
```

---

## SIDE-BY-SIDE COMPARISON

### **Example: American Put Option**

**Assumptions:**
- Spot: 5415
- Strike: 5400
- Vol: 18.45%
- T: 90 days
- r: 4.5%

**Black-Scholes (WRONG):**
```
Assumes European exercise only
put_price = european_put_formula(S, K, r, q, σ, T)
           = $2.50

Time: <1ms
Status: INCORRECT (ignores early exercise)
```

**Monte Carlo LSM (CORRECT):**
```
Simulate 10,000 paths
At each time step on each path:
  - Calculate intrinsic value (S - K)
  - Use LSM regression to estimate continuation value
  - If intrinsic > continuation: EXERCISE
  - Else: hold and continue
  
Discount payoff back to today
Average across all 10,000 paths

put_price ≈ $11.35 ± $0.25

Time: 2-5 seconds
Status: CORRECT (includes early exercise premium)

Difference: $11.35 - $2.50 = $8.85 (253% error!)
```

**Which would you trust?** 
The Monte Carlo, obviously. Black-Scholes is fundamentally wrong here.

---

## REAL-WORLD USE CASES

### **1. Investment Bank's Trading Desk**

**Scenario:** Trader receives request to price American put on Apple

```
Morning: Trader needs price in 30 seconds
  → Implement Black-Scholes (fast)
  → Get $3.20 quote
  
Afternoon: Risk manager wants accurate valuation for risk report
  → Run Monte Carlo (2000 paths)
  → Get $11.35 ± $0.35
  → Realize trader's quote was WRONG by $8.15!
  → Update all positions in system
```

**Impact:**
```
200 contracts traded at $3.20:
  - Trader revenue: 200 × $3.20 = $640
  
True fair value: $11.35
  - Bank should have charged: 200 × $11.35 = $2,270
  
Loss: $2,270 - $640 = $1,630 per 200 contracts
× 50 such trades per day
= $40,750 daily loss (before risk mitigation)
```

This is why investment banks run Monte Carlo overnight after market close.

---

### **2. Risk Management (VAR Calculation)**

**Scenario:** Portfolio contains exotic derivatives

```
Black-Scholes Greeks:
  Delta = 0.50
  Risk estimate: "1% market move = $500 loss"
  → Used for daily risk limits
  
But if stock price near barrier...
  → Delta changes DRAMATICALLY (gamma effect)
  → 1% move might cause $5,000 loss!
  
Monte Carlo:
  Simulates 10,000 scenarios with market stress
  Captures tail risks that static delta misses
  → More accurate VAR estimate
  → Better risk controls
```

---

### **3. Exotic Derivatives Pricing (Asset Management)**

**Scenario:** Asset manager considering structured note investment

```
Structured Note Terms:
  - Pays 5% coupon
  - If basket of 5 stocks falls below 60%, investor loses principal
  - 5-year maturity

Black-Scholes:
  "Can't price this"
  
Monte Carlo:
  - Simulate 10,000 scenarios (5 years, 5 stocks, correlated)
  - For each scenario: calculate probability of basket breach
  - If breached: investor loses
  - If not: receives 5% coupons
  - PV of expected cashflows
  
Result: Fair value = $98.50
Offering price: $100.00
→ Structured note is overpriced by $1.50
→ Don't buy it
```

---

### **4. Convertible Bond Analysis**

```
Convertible Bond:
  - 5% coupon
  - Convertible to 50 shares
  - Callable by issuer at 110
  - 5-year maturity

Black-Scholes: Impossible

Monte Carlo:
  Simulate stock price paths
  At each node:
    - Calculate bond value (PV of remaining coupons + principal)
    - Calculate conversion value (stock_price × shares)
    - Issuer calls if beneficial
    - Investor converts if beneficial
    - Take max of all three choices
  
  Discount back to today
  Average across 10,000 scenarios
```

---

## WHEN NOT TO USE MONTE CARLO

❌ **Simple European vanilla option**
   → Black-Scholes is exact, 1000x faster

❌ **Need <100ms response time** (e.g., high-frequency trading)
   → Monte Carlo takes seconds
   → Use Black-Scholes

❌ **Need exact price for regulatory reporting**
   → Monte Carlo is approximate (±$0.15)
   → Black-Scholes is exact

❌ **Single asset, no exotic features**
   → Wasting computation
   → Use analytical formula

---

## IMPLEMENTATION PSEUDOCODE

### **Simple European Call via Monte Carlo**

```python
def monte_carlo_european_call(S, K, r, sigma, T, N_paths=10000):
    """
    Price European call using Monte Carlo
    """
    payoffs = []
    
    for path in range(N_paths):
        # Simulate one path to maturity
        S_T = simulate_path(S, sigma, r, T)
        
        # Calculate payoff
        payoff = max(S_T - K, 0)
        
        # Discount to present value
        pv = payoff * exp(-r * T)
        
        payoffs.append(pv)
    
    # Average across all paths
    option_price = mean(payoffs)
    std_error = std(payoffs) / sqrt(N_paths)
    
    return option_price, std_error


def simulate_path(S, sigma, r, T, steps=252):
    """
    Simulate one stock price path using Geometric Brownian Motion
    
    dS = μ*S*dt + σ*S*dZ
    where dZ ~ N(0, sqrt(dt))
    """
    dt = T / steps
    S_t = S
    
    for step in range(steps):
        Z = random_normal()  # N(0,1)
        dS = r * S_t * dt + sigma * S_t * Z * sqrt(dt)
        S_t = S_t + dS
    
    return S_t
```

### **American Put via Monte Carlo + LSM**

```python
def monte_carlo_american_put(S, K, r, sigma, T, N_paths=10000):
    """
    Price American put using Least Squares Method (LSM)
    """
    dt = T / 252  # Daily steps
    times = arange(0, T, dt)
    
    # Pre-generate all paths
    paths = generate_paths(S, sigma, r, T, N_paths)
    
    # Start from the end and work backward
    values = zeros(N_paths)
    
    # At maturity
    for i in range(N_paths):
        values[i] = max(K - paths[i, -1], 0)
    
    # Backward induction
    for t in range(len(times)-2, 0, -1):
        S_t = paths[:, t]
        payoff = K - S_t  # Intrinsic value if exercised now
        
        # LSM regression: estimate continuation value
        # (simplified: use polynomial regression)
        continuation = polyfit(S_t, values, degree=3)(S_t)
        
        # Exercise decision: intrinsic vs continuation
        exercise = payoff > continuation
        
        # Update values
        values = where(exercise, payoff, values * exp(-r*dt))
    
    # Discount final values and average
    american_put_price = mean(values) * exp(-r*dt)
    
    return american_put_price
```

---

## QUICK SUMMARY

| Feature | Black-Scholes | Monte Carlo |
|---------|---|---|
| **American options** | ✗ Fails | ✓ Required |
| **Asian options** | ✗ Fails | ✓ Perfect |
| **Multiple assets** | ✗ Fails | ✓ Perfect |
| **Path-dependent** | ✗ Fails | ✓ Works |
| **Speed** | <1ms | 1-30s |
| **Accuracy** | 100% exact | 95-99% |
| **Simple vanilla** | ✓ Perfect | ~ Wasteful |
| **Complexity** | Low | High |

---

## THE REAL INSIGHT

**Black-Scholes** = For problems with closed-form solutions
- Fast
- Exact
- Limited to specific option types

**Monte Carlo** = For problems without closed-form solutions
- Flexible
- Approximate
- Works for ANY exotic option type
- Takes longer
- Requires more code

**In derivatives trading:**
- Use Black-Scholes for 95% of daily trading (vanilla options)
- Use Monte Carlo for 5% of trading (exotics, American options, baskets)
- Use Monte Carlo for ALL risk reporting (more accurate)

---

## YOUR NEXT STEPS

1. **Your knockout options?** 
   → Black-Scholes is perfect ✓
   → Don't use Monte Carlo (waste of compute)

2. **If you get asked to price an American put?**
   → "I need Monte Carlo LSM"

3. **If you get asked to price a basket?**
   → "I need Monte Carlo with correlation matrix"

4. **If you see "exotic" or "path-dependent"?**
   → "Monte Carlo time"

