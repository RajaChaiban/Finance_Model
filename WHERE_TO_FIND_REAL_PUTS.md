# WHERE TO FIND REAL AMERICAN PUT OPTIONS TO TRADE

You now understand how to price them. Here's where to actually BUY and SELL real puts.

---

## QUICK ANSWER

| Platform | Type | Cost | Ease | Use Case |
|----------|------|------|------|----------|
| **Tastytrade** | Trading app | Free trades | Easy | Beginners, frequent traders |
| **TD Ameritrade** | Brokerage | Free trades | Medium | Professional traders |
| **Interactive Brokers** | Professional | Very low fees | Hard | Quants, professionals |
| **E*TRADE** | Brokerage | Free trades | Medium | Casual traders |
| **Yahoo Finance** | Free data | FREE | Easy | Research only |
| **ThinkorSwim** | Platform | Free | Medium | Advanced traders |

---

## OPTION 1: TASTYTRADE (Easiest for Beginners)

### What it is:
```
Modern options trading app focused on options trading
Perfect for learning and practicing
```

### How to find puts:
```
1. Download Tastytrade app (or go to www.tastytrade.com)
2. Create account (takes 5 minutes)
3. Fund with $500+ minimum
4. Search for ticker: "SPY" (S&P 500 ETF) or "QQQ" (Nasdaq)
5. Click "Put" tab
6. See all available puts with different strikes and expirations
```

### What you'll see:

```
SPY 90-Day Put Options (Today's Date)

Strike | Bid  | Ask  | Volume | IV   | Days
$580   | $0.05| $0.10| 1,200  | 12%  | 90
$585   | $0.15| $0.20| 2,500  | 12%  | 90
$590   | $0.40| $0.50| 5,000  | 13%  | 90
$595   | $1.20| $1.30|10,000  | 13%  | 90
$600   | $3.50| $3.60|15,000  | 14%  | 90  ← Current price

Bid  = Price brokers will pay you to SELL
Ask  = Price you pay to BUY
IV   = Implied volatility (use this in Black-Scholes/Monte Carlo!)
Days = Days to expiration
```

### Real example (SPY at $600):

```
BUY a $595 put (5% below current):
  Ask price: $1.30
  You pay: 1.30 × 100 = $130 per contract
  
Your put has early exercise premium built in!
When you sell it later, you profit from:
  1. Stock movement (delta)
  2. Volatility changes (vega)
  3. Time decay (theta)
```

**Cost to get started:**
- Account: Free
- Minimum to trade: $500-$2,000
- Per trade: $0 (commission free)

**Learning mode:**
- Tastytrade has paper trading (fake money)
- Practice pricing without real money first!

---

## OPTION 2: YAHOO FINANCE (Free Research)

### What it is:
```
Completely FREE
Real put prices updated every minute
Perfect for research and learning
NO trading capability (can't actually buy/sell)
```

### How to find puts:

```
1. Go to www.finance.yahoo.com
2. Search "SPY" (S&P 500 ETF, easiest to find)
3. Click "Options" tab
4. See all puts for different expiration dates

Example URL:
https://finance.yahoo.com/quote/SPY/options
```

### What you'll see:

```
SPY Options - April 25, 2026

Calls | Puts (CLICK THIS)

Contract Name | Last Price | Bid  | Ask  | Change | % Change | IV   | Expiration
SPY240517P00600000 | 1.28 | 1.25 | 1.30 | +0.05 | +4.0%  | 14.2% | May 17 (90 days)
SPY240517P00595000 | 0.95 | 0.92 | 0.98 | +0.03 | +3.2%  | 13.8% | May 17 (90 days)
SPY240517P00590000 | 0.68 | 0.65 | 0.72 | +0.02 | +3.0%  | 13.4% | May 17 (90 days)

^^ Exact data you can use in your Monte Carlo model!
```

**Cost:** FREE (just ads)
**Use case:** Research, learning, homework

---

## OPTION 3: TD AMERITRADE / THINKORSWIM (Most Powerful)

### What it is:
```
Professional trading platform
Owned by Charles Schwab
Free to use for trading
Most advanced charting tools
```

### How to find puts:

```
1. Go to www.tdameritrade.com
2. Download ThinkorSwim (free app)
3. Log in with account
4. Click "Analyze" tab
5. Search ticker "SPY" or "QQQ"
6. Right-click on option chain → see all puts

Or use Trade Desk:
  1. New Order
  2. Select "Put" option type
  3. Choose strike and expiration
  4. See real-time bid/ask prices
```

### Why it's great:

```
✓ Built-in Greeks calculation (Delta, Gamma, Vega, Theta)
✓ Shows implied volatility (σ for your model)
✓ Option chain calculator
✓ Probability analysis (probability of being ITM)
✓ Free paper trading account
✓ Excellent for backtesting
```

**Cost:** Free (Charles Schwab acquired TD)
**Use case:** Serious traders, professionals

---

## OPTION 4: INTERACTIVE BROKERS (Quants & Professionals)

### What it is:
```
Lowest-cost professional platform
Used by quants and institutional traders
API access available
Can program your own strategies
```

### How to use it:

```
1. Create account at www.interactivebrokers.com
2. Fund $10,000+ minimum
3. Use Trader Workstation (TWS)
4. Search options by symbol, strike, expiration
5. Access API for automated trading (advanced)
```

### Advantages:

```
✓ Lowest fees in the industry
✓ API for automated trading
✓ Access to exotic instruments
✓ Historical data for backtesting
✓ Can do complex trades (spreads, hedges, etc.)
```

**Cost:** $1-$3 per trade, but lowest margins
**Use case:** Active traders, algorithms, professionals

---

## OPTION 5: YOUR OWN BROKERAGE (Advanced)

### If you have one already:

```
Likely options available: Check "Options Trading" section

Common brokers:
  Fidelity   → Go to Fidelity.com > Trade > Options
  Vanguard   → Limited options, requires approval
  Merrill    → ML Guided Investing
  Schwab     → Built-in options platform
  E*TRADE    → Full options platform
```

---

## REAL EXAMPLE: Find a Put and Price It

### Step 1: Find put data online

```
Go to: finance.yahoo.com/quote/SPY/options

Today's date: April 25, 2026
Current SPY price: $600
Find "May 17, 2026" expiration (90 days out)
Click "PUTS"

See: SPY 595 Put
- Bid: $1.25
- Ask: $1.30
- Implied Volatility: 13.8%
```

### Step 2: Use YOUR Monte Carlo code

```
Input parameters:
  S = 600 (current SPY price)
  K = 595 (strike)
  T = 90/365 = 0.2466 years
  r = 0.045 (risk-free rate)
  sigma = 0.138 (13.8% implied volatility from market)
  q = 0.01 (SPY dividend yield)

Run your Monte Carlo (american_puts_simple.py):
  European Put: $X.XX
  American Put: $Y.YY
  
Compare to market:
  Market price: $1.25-$1.30
  Your price: $Y.YY
  
If Your Price > Market Ask ($1.30):
  → BUY (it's underpriced!)
  
If Your Price < Market Bid ($1.25):
  → SELL (it's overpriced!)
```

---

## STEP-BY-STEP: YOUR FIRST TRADE

### Plan: Buy a protective put on SPY

```
STEP 1: Research
  Go to Yahoo Finance
  Find SPY puts, 90-day expiration
  Note the IV (implied volatility)

STEP 2: Model it
  Run your Monte Carlo simulation
  Get fair value using LSM algorithm
  Compare to market price

STEP 3: Find the edge
  If American put worth $2.00 but trading at $1.50
  You found a 33% edge!
  (Assuming your model is right)

STEP 4: Open account
  Tastytrade or TD Ameritrade (easiest)
  Fund with $2,000
  Enable options trading

STEP 5: Place trade
  "Buy to open: 10 contracts SPY $595 Put"
  30-60 days to expiration
  Monitor daily

STEP 6: Close trade
  When profit target reached (e.g., 50%)
  Or when stock recovers
  Or at 1 day before expiration
  "Sell to close" to exit
```

---

## FINDING DATA FOR YOUR MODEL

### Where to get historical data to improve your models:

```
1. Yahoo Finance (Free)
   finance.yahoo.com
   Download daily OHLC data
   Use in volatility calculations

2. Quandl (Free tier available)
   www.quandl.com
   Extensive historical option data
   Free for basic research

3. Alpha Vantage (Free API)
   www.alphavantage.co
   Real-time stock prices
   Historical data
   Free API key

4. IB (Interactive Brokers) Data
   Historic data via TWS
   $1-5 per month for different data feeds
   Most accurate for options

5. Your broker's API
   TD Ameritrade: thinkorswim API
   Interactive Brokers: TWS API
   Real-time option prices for backtesting
```

---

## KEY PARAMETERS TO TRACK

When you're pricing American puts, you need these from the market:

```
From option chain:
  ✓ Current stock price (S)
  ✓ Strike price (K)
  ✓ Days to expiration (T)
  ✓ Bid price (what you sell for)
  ✓ Ask price (what you buy for)
  ✓ Implied volatility (σ) ← CRITICAL for your model
  ✓ Open interest (how many contracts exist)
  ✓ Volume (liquidity)

From market data:
  ✓ Risk-free rate (r) [look up Fed Funds rate]
  ✓ Dividend yield (q) [stock dividend / price]

Your model outputs:
  ✓ Intrinsic value
  ✓ Fair value price
  ✓ Greeks (delta, gamma, vega, theta)
  ✓ Early exercise premium
```

---

## REAL DOLLAR EXAMPLE

### You find a deal:

```
SPY $600 put, 90 days out
Market price: $1.50 per share = $150 per contract

Your Monte Carlo says it's worth:
  European: $1.20
  American: $1.80
  Early exercise premium: $0.60

Your edge: You think it's worth $1.80
Market selling at: $1.50
Profit opportunity: $0.30 per share = $30 per contract

Buy 10 contracts:
  Cost: 10 × $150 = $1,500
  Potential profit: 10 × $30 = $300 (20% return)

Risks:
  ✓ Your model might be wrong
  ✓ Volatility changes
  ✓ Stock movements
  ✓ Liquidity (hard to sell 10 contracts)

But if you're RIGHT:
  $300 profit on $1,500 capital = 20% in 90 days
  That's 89% annualized return!
```

---

## FINAL CHECKLIST

```
To start trading American puts based on your models:

□ Download Tastytrade or use Yahoo Finance first (free)
□ Learn to read options chains
□ Practice running your Monte Carlo on 5-10 real options
□ Compare your prices to market prices
□ Track when you're RIGHT and when you're WRONG
□ Improve your model based on feedback
□ Open real account with $2,000-$5,000
□ Start with paper trading (fake money)
□ Place small real trades (1-2 contracts)
□ Keep detailed records of profits/losses
□ Iterate and improve model

Your Monte Carlo skills:
  ✓ You know how to price American puts
  ✓ You know why Black-Scholes fails
  ✓ You know how to find the early exercise premium
  ✓ You're ready to trade quantitatively!
```

