# AMERICAN PUT OPTIONS EXPLAINED
## What They Are, Why They Matter, How to Price Them

---

## THE CORE IDEA IN 30 SECONDS

**European Put:**
```
You can ONLY exercise on the expiration date
"You have to wait until day 90, then get paid"
```

**American Put:**
```
You can exercise ANYTIME before expiration
"You can take your profit today, tomorrow, or day 90 - your choice"
```

**That's it.** That one difference creates massive pricing complexity.

---

## CONCRETE EXAMPLE: Stock Crashes

### **Scenario Setup**

```
Today (Day 0):
  Stock Price: $100
  You buy a put option
  Strike: $95
  Expiration: 90 days
  Payoff: max($95 - Stock_Price, 0)
```

### **Day 30: Stock Crashes to $60**

Your put is now **DEEP IN THE MONEY:**
```
Intrinsic value = max($95 - $60, 0) = $35
```

**You have two choices:**

### **EUROPEAN PUT (No Choice)**
```
"You MUST wait until day 90"

You're forced to hold, hoping:
  Option A: Stock goes to $30 (you get $65)
  Option B: Stock recovers to $80 (you get $15)
  Option C: Stock stays at $60 (you get $35)

You have to WAIT and see what happens.
```

### **AMERICAN PUT (Your Choice!)**
```
"You CAN exercise today if you want"

You think:
  "Stock is at $60. I can get $35 right now.
   
   OR I wait and hope it crashes more.
   
   But what if it bounces back to $90?
   Then I'm left with almost nothing.
   
   Risk: Stock goes up, my option becomes worthless
   Reward: Stock crashes more, I get more money
   
   Expected reward: +$5 (not much upside)
   Risk: -$35 (could lose everything)"
   
Decision: EXERCISE NOW and take the $35
```

**This is the power of American puts:**
- You can lock in profit when it's good enough
- You don't have to wait for expiration hoping for more

---

## WHY THIS MATTERS: THE EARLY EXERCISE DECISION

### **The Problem Black-Scholes Can't Solve**

```
BLACK-SCHOLES ASSUMES:
  "You hold the option until expiration"
  → Can't value early exercise decision
  → Gives wrong price for American options

AMERICAN PUT REALITY:
  At EVERY day, you ask: "Should I exercise now or wait?"
  → This is a dynamic decision
  → Depends on current price vs future possibilities
  → Creates extra value compared to European
```

### **The Math Doesn't Work**

```
Black-Scholes formula:
  P = K×e^(-r×T)×N(-d₂) - S×N(-d₁)
  
This formula assumes:
  "Option holder always holds until expiration"
  
But American put holder says:
  "I'll exercise whenever it's optimal"
  
These are contradictory!
→ Black-Scholes FAILS for American options
→ Gives price that's TOO LOW
```

---

## REAL-WORLD EXAMPLE: Portfolio Manager

### **Scenario: You manage $10 million in stocks**

```
October 2024: S&P 500 at 5,800
You buy put options to protect your portfolio
Strike: 5,500
Expiration: December (60 days)
Cost: $200 per contract

December 2024: S&P crashes to 5,000
Your puts are now worth:
  Intrinsic: max(5,500 - 5,000, 0) = $500

AMERICAN PUT (Your Reality):
  You can exercise RIGHT NOW
  Sell protection immediately
  Lock in $300 profit per contract
  
  OR wait and hope for more downside
  Risk: Market bounces back, loses protection value

Your decision:
  "Market crashed, economy looks bad,
   I got my $300 profit protection.
   Let me exercise and lock it in."
   
EXERCISE: Take the $500, sell the puts
Portfolio is protected at 5,500 level
```

```
EUROPEAN PUT (Theoretical):
  You HAVE to hold the puts
  Hope for more downside
  What if market bounces back to 5,400 by expiration?
  Your puts go from $500 → $100 (worth less)
  
  FORCED to wait (can't exercise early)
  Might miss the best time to exit
```

**Real money difference:**
```
American: Exercise at $500, lock in profit
European: Wait and hope, might end at $100
Difference: $400 per contract

× 100 contracts = $40,000 difference!
```

This is why American puts are worth MORE than European puts.

---

## THE EARLY EXERCISE PREMIUM

### **American vs European Put Comparison**

```
Example: S&P 500 put with same parameters
─────────────────────────────────────────

EUROPEAN PUT (Black-Scholes):
  Price: $3.45
  → Assumes you hold until expiration
  → No early exercise option
  
AMERICAN PUT (Monte Carlo LSM):
  Price: $11.35
  → You can exercise anytime
  → Includes early exercise premium
  
DIFFERENCE: $11.35 - $3.45 = $7.90
            ↑
            EARLY EXERCISE PREMIUM (229% more!)

Why so much more?
  Because you can LOCK IN PROFITS whenever you want
  This is valuable!
```

### **When is Early Exercise Valuable?**

Early exercise becomes valuable when:

```
1. DEEP IN THE MONEY
   Stock: $100, Strike: $50, Put worth: $50
   
   You can:
   Option A: Exercise now, get $50 cash, invested at 4% interest
   Option B: Wait 60 days, maybe get $52 (if stock drops more)
   
   If 4% interest on $50 for 60 days > expected upside of $2
   → EXERCISE NOW is better!

2. BEFORE DIVIDEND
   Stock: $100, Strike: $95
   Major dividend announced: $5 per share tomorrow
   
   If you DON'T exercise:
   → Stock drops to $95 (prices adjust for dividend)
   → Your put becomes worthless
   
   If you exercise TODAY:
   → Get $95, avoid dividend loss
   → Better decision!
   → You GET the dividend

3. HIGH INTEREST RATES
   When risk-free rate is high (5-6%+)
   
   Holding put = missing out on interest
   
   Exercising put = get cash, earn interest
   
   At high rates, early exercise becomes more attractive

4. STOCK CRASHED HARD
   Stock: $100 → Crashed to $40, Strike: $95
   
   You get $55 by exercising today
   Chance stock goes to $30: small
   Chance stock bounces to $60: more likely (mean reversion)
   
   You might prefer:
   "Take the $55 now rather than risk bouncing back"
```

---

## AMERICAN vs EUROPEAN: SIDE BY SIDE

### **The Comparison**

| Feature | European Put | American Put |
|---------|---|---|
| **Exercise Time** | Only at expiration | Anytime before expiration |
| **Flexibility** | No choice | Your choice |
| **Price** | Lower | Higher |
| **Closed-form formula?** | YES (Black-Scholes) | NO (need Monte Carlo) |
| **Complexity** | Simple | Complex (dynamic decision) |
| **Use case** | Index options, simple hedges | Stock options, portfolio hedging |
| **Typical Premium** | 100% | 130-250% (early exercise adds value) |

### **Example Prices (Real Numbers)**

```
S&P 500 Put, Strike $5,400, 90 days, 18.45% vol

EUROPEAN PUT (Black-Scholes):
  Price: $2.50
  
AMERICAN PUT (Monte Carlo LSM):
  Price: $11.35
  
Difference: $8.85 (354% premium!)
  
Why so much more?
  1. Deep in the money: early exercise valuable
  2. Can lock in profits at optimal times
  3. Can exercise before dividend ex-date
  4. Can benefit from interest rates
```

---

## WHY BLACK-SCHOLES FAILS (THE MATHEMATICAL REASON)

### **The Problem**

```
Black-Scholes Equation:
  ∂C/∂t + (1/2)σ²S²∂²C/∂S² + rS∂C/∂S - rC = 0

This equation assumes:
  "Option holder has NO control over exercise decision"
  
But American option says:
  "At each moment, I can exercise if I want"
  
This breaks the equation!
  → Need to add constraint: "American" = max(intrinsic, European_value)
  → This creates an inequality problem
  → No closed-form solution
```

### **Why We Need Monte Carlo**

```
The Decision Tree for American Put:

Day 0: Stock = 100
  ├─ Exercise now? Get $95 - $100 = -$5 (no, intrinsic negative)
  └─ Hold: Expected future payoff?
           Need to know all possible futures!
           
Day 30: Stock = 60
  ├─ Exercise now? Get $95 - $60 = $35 ✓ (valuable!)
  ├─ OR hold: Expected future payoff?
  │           Could go to $30 (worth $65) or $80 (worth $15)
  │           Expected value ~ $50 - costs of waiting
  │           Likely worth more than $35
  └─ Need to compare: $35 now vs E[Future] to decide

This comparison changes AT EVERY NODE
And depends on ALL future possibilities

Can't write a closed-form formula for this
Must simulate all futures to decide optimally at each point
→ Monte Carlo is the ONLY way
```

---

## HOW TO PRICE AN AMERICAN PUT (The Algorithm)

### **The Monte Carlo LSM Method**

```
LEAST SQUARES METHOD (LSM) FOR AMERICAN OPTIONS
═════════════════════════════════════════════════

INPUT:
  S = 100 (stock price)
  K = 95 (strike)
  T = 90/365 (time, in years)
  r = 0.05 (interest rate)
  σ = 0.25 (volatility)
  N_paths = 10,000
  N_steps = 90 (daily steps)


STEP 1: Generate random paths (10,000 of them)
────────────────────────────────────────────
for path i = 1 to 10,000:
    S_0 = 100
    for day t = 1 to 90:
        Z = random normal ~ N(0,1)
        dS = r × S × dt + σ × S × Z × sqrt(dt)
        S_t = S_{t-1} + dS
    
    path_i = [S_0, S_1, S_2, ..., S_90]


STEP 2: Work backward from maturity
─────────────────────────────────────
At day 90 (maturity):
    For each path i:
        value_i[90] = max(K - S_i[90], 0)


STEP 3: At each earlier date, decide exercise or hold
────────────────────────────────────────────────────
for t = 89, 88, ..., 1:
    for path i = 1 to 10,000:
        
        # Intrinsic value (if exercise today)
        intrinsic_i = max(K - S_i[t], 0)
        
        # Continuation value (if hold)
        continuation_i = LSM_regression(paths with similar S_i[t],
                                       their future values)
        
        # Decision
        if intrinsic_i > continuation_i:
            value_i[t] = intrinsic_i  # EXERCISE
        else:
            value_i[t] = continuation_i × e^(-r×dt)  # HOLD
    
    # LSM uses polynomial regression to estimate
    # continuation value based on stock price


STEP 4: Discount back to today
───────────────────────────────
option_price = mean(value_i[0]) × e^(-r×T)


OUTPUT:
american_put_price ≈ $11.35 ± $0.30
```

### **What This Algorithm Does**

```
Key insight: BACKWARD INDUCTION

Day 90: "If I'm still holding, I get max(K - S, 0)"

Day 89: "Should I exercise today?"
        Compare:
          A) Exercise now: Get max(K - S, 0) in hand
          B) Hold to day 90: Expected value from regression
        Pick maximum → that's the value
        
Day 88: "Should I exercise today?"
        Compare using day 89's values
        (not day 90's, because some paths already exercised on day 89)

...continue backward...

Day 1: "Should I exercise today?"
       Compare immediate payoff vs expected future from day 2+

Day 0: Average across all paths → American put price
```

This is why it's called **LEAST SQUARES METHOD**:
- Uses polynomial regression (least squares) to estimate continuation value
- Works backward through time
- Makes optimal exercise decision at each node
- Can only be done via simulation (Monte Carlo)

---

## REAL-WORLD AMERICAN PUT USE CASES

### **1. Portfolio Manager (Protective Put)**

```
Situation:
  - Manage $100M in large-cap stocks
  - Market is uncertain
  - Buy put options to protect

Why American?
  ✓ If market crashes, want to exercise immediately
  ✓ Lock in protection at exact right moment
  ✓ Don't want to wait for expiration date
  ✓ European would force you to hold longer than needed

Typical parameters:
  Strike: 5-10% OTM
  Expiration: 3-6 months
  Cost: 2-5% of portfolio value
```

### **2. Equity Trader (Downside Hedging)**

```
Situation:
  - Long 1000 shares of Apple at $180
  - Worried about earnings announcement tomorrow
  - Buy put option to limit downside

Why American?
  ✓ If stock crashes tomorrow before earnings, exercise immediately
  ✓ Lock in loss limit right away
  ✓ Don't wait until expiration
  ✓ European would be useless if stock stays crashed

Typical parameters:
  Strike: 5-15% OTM
  Expiration: Few days to weeks
  Cost: 0.5-2% of position
```

### **3. Dividend Capture Strategy**

```
Situation:
  - Stock trading at $100
  - $5 dividend announced for tomorrow (ex-date)
  - Stock will drop to $95 after dividend

If you DON'T exercise before ex-date:
  ✗ Stock drops $5 automatically
  ✗ Your put becomes less valuable
  
If you EXERCISE your put before ex-date:
  ✓ Get strike price immediately
  ✓ Avoid dividend adjustment
  ✓ European put forces you to wait (worthless!)
  
This is ONLY possible with AMERICAN PUT
```

### **4. Currency Hedging (Foreign Exchange)**

```
Situation:
  - US company expects €10M payment in 60 days
  - Euro at $1.10
  - Worried about depreciation
  - Buy put on Euro (right to sell at $1.10)

Why American?
  ✓ If Euro crashes to $0.95 next week, exercise immediately
  ✓ Lock in exchange rate right away
  ✓ Don't wait 60 days for payment
  ✓ Can exercise when you get the money (any day, not just day 60)

This is business reality - European doesn't fit
```

---

## AMERICAN VS EUROPEAN: PRICING COMPARISON

### **Stock: Apple, Strike: $170 (put), 90 days, 20% volatility**

```
Black-Scholes (European):
  Price: $2.75
  
  Assumes: "Hold until day 90, get payoff then"
  
Monte Carlo LSM (American):
  Price: $8.40 ± $0.25
  
  Assumes: "Exercise optimally at each opportunity"
  
Early Exercise Premium:
  $8.40 - $2.75 = $5.65 (206% premium!)
  
Why so much?
  At stock price = $150, you're deep ITM
  Early exercise becomes very valuable
  Can lock in $20 profit immediately
```

### **Index: S&P 500, Strike: $5,200 (put), 180 days, 16% volatility**

```
Black-Scholes (European):
  Price: $45.20
  
Monte Carlo LSM (American):
  Price: $67.85 ± $1.50
  
Early Exercise Premium:
  $67.85 - $45.20 = $22.65 (50% premium)
  
Why less premium than Apple?
  Index options less likely to be deep ITM
  Less extreme scenarios
  Early exercise benefit smaller
```

---

## KEY INSIGHT: WHY AMERICAN PUTS ARE MORE VALUABLE

### **The Intuition**

```
AMERICAN PUT = EUROPEAN PUT + EARLY EXERCISE OPTION

You get everything the European put gives you
PLUS the right to exercise early

This right has value!

How much value?
  - Depends on how deep ITM the option can get
  - Depends on interest rates
  - Depends on dividends
  - Depends on volatility
  
Typically: 30-250% premium for American vs European
(varies wildly by parameters)
```

### **The Formula Intuition**

```
American Put Price ≥ European Put Price

Always greater than or equal (the early exercise option is never worthless)

Equality only when:
  - Option is way out of the money (early exercise worthless)
  - Interest rates are very low (no incentive to exercise early)
  - No dividends (no special timing)
```

---

## SUMMARY TABLE

| Aspect | European Put | American Put |
|--------|---|---|
| **Definition** | Exercise on expiration ONLY | Exercise ANYTIME before expiration |
| **Flexibility** | No | YES - full control |
| **Real-world use** | Rare (mostly academic) | Common (most equity puts) |
| **Price** | Lower | Higher (includes early exercise premium) |
| **Black-Scholes works?** | YES ✓ | NO ✗ (need Monte Carlo) |
| **Pricing time** | <1ms | 2-5 seconds |
| **When to exercise** | Day 90 only | Whenever optimal (backward induction) |
| **Early exercise premium** | 0% | 30-250% (depends on parameters) |

---

## PRACTICAL DECISION: WHEN WOULD YOU USE EACH?

### **Use EUROPEAN Put When:**
```
✓ You MUST hold exactly until expiration
✓ No early exit allowed (regulatory constraint)
✓ Trading index options (ETFs, indices)
✓ Academic/theoretical pricing
✓ Want simple closed-form valuation
```

### **Use AMERICAN Put When:**
```
✓ You CAN exit whenever you want (normal real-world case)
✓ Portfolio hedging (common use)
✓ Stock options (equity)
✓ Currency options
✓ Commodity options
✓ Bonds and convertibles
✓ ANY situation where early exercise might make sense
```

**Real Talk:** 
In practice, almost ALL equity put options you see are AMERICAN
Very few people trade European puts (except in academic settings or on indices)

---

## FINAL THOUGHT

```
EUROPEAN PUT:
  "You have to wait until day 90"
  → Simpler, cheaper, less valuable
  → Black-Scholes works

AMERICAN PUT:
  "You can get out whenever you want"
  → More complex, more expensive, more valuable
  → Black-Scholes fails, need Monte Carlo
  → This is what real traders use
  
The difference in price is the value of FLEXIBILITY
And flexibility is worth a LOT of money!
```

