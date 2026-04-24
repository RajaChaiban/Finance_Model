# S&P 500 Knockout Option Pricer

A comprehensive educational and professional tool for pricing knockout (barrier) options on the S&P 500 using Black-Scholes formulas implemented from scratch, validated against QuantLib.

## Overview

This project implements a complete knockout option pricing model that:

- **Fetches real market data** - S&P 500 prices and calculates historical volatility
- **Implements Black-Scholes from scratch** - Pure Python implementation with heavy educational comments
- **Prices knockout options** - Both calls and puts with barrier adjustments
- **Calculates Greeks** - Delta, Gamma, Vega, Theta for risk analysis
- **Validates against QuantLib** - Industry-standard comparison to ensure correctness
- **Visualizes results** - Professional 4-panel charts showing payoffs and Greeks

## What is a Knockout Option?

A **knockout option** (also called a barrier option) is a standard call or put option that becomes **worthless** if the underlying asset's price hits a specified barrier level before expiration.

### Key Features:
- **Cheaper than vanilla options** - The seller is protected if the barrier is breached
- **Path-dependent** - The option value depends on whether the barrier is hit, not just final price
- **Practical use** - Used for hedging with lower costs or as speculation with leverage constraints

### Example:
- **Knockout Call**: Buy call at S&P=4500 (strike), but it dies if S&P falls to 4050 (10% barrier)
- **Knockout Put**: Buy put for downside protection, but it expires if S&P rallies to 4950

## Project Structure

```
Finance_Model/
├── Knockout_Option_Pricer_SP500.ipynb   # Main notebook (8 cells)
├── docs/
│   └── superpowers/
│       ├── plans/
│       │   └── 2026-04-24-knockout-pricer.md      # Implementation plan
│       └── specs/
└── README.md
```

## Getting Started

### Google Colab (Recommended)

1. Open [Google Colab](https://colab.research.google.com)
2. Click "File → Open notebook → GitHub"
3. Search for this repository
4. Click on `Knockout_Option_Pricer_SP500.ipynb`
5. Run all cells (Ctrl+F9)

### Local Jupyter

```bash
# Clone the repo
git clone <repo-url>
cd Finance_Model

# Install dependencies
pip install numpy scipy pandas matplotlib yfinance QuantLib

# Open notebook
jupyter notebook Knockout_Option_Pricer_SP500.ipynb
```

## Notebook Structure

| Cell | Topic | Key Content |
|------|-------|------------|
| 1 | Setup | Imports: NumPy, SciPy, yfinance, QuantLib |
| 2 | Theory | Black-Scholes formula, Greeks, barrier adjustments |
| 3 | Data | Fetch S&P 500 data, calculate volatility |
| 4 | Pricing | Black-Scholes implementation from scratch |
| 5 | Greeks | Delta, Gamma, Vega, Theta calculations |
| 6 | Charts | 4-panel visualization (payoffs + Greeks) |
| 7 | Validation | Compare manual implementation vs QuantLib |
| 8 | Summary | Key formulas, insights, and next steps |

## Key Formulas

### Black-Scholes Call Price
```
C = S₀×N(d₁) - K×e^(-rT)×N(d₂)

where:
d₁ = [ln(S₀/K) + (r + σ²/2)T] / (σ√T)
d₂ = d₁ - σ√T
```

### Knockout Adjustment
```
Knockout Price = Vanilla Price × (B/S)^(2λ-1)

where:
λ = (r + σ²/2) / σ²
B = Barrier level
S = Current spot price
```

## The Greeks

| Greek | Symbol | Meaning | Use Case |
|-------|--------|---------|----------|
| Delta | Δ | Price change per $1 stock move | Directional exposure |
| Gamma | Γ | Rate of delta change | Hedging frequency |
| Vega | ν | Price change per 1% volatility | Volatility bets |
| Theta | Θ | Daily time decay | Cost of holding |

## Technologies Used

- **NumPy/SciPy** - Numerical computing and statistical distributions
- **Pandas** - Data manipulation and analysis
- **Matplotlib** - Professional data visualization
- **yfinance** - Real S&P 500 market data
- **QuantLib** - Industry-standard derivatives pricing library

## Parameters

The notebook uses these default parameters (easily adjustable):

- **Underlying**: S&P 500 (^GSPC) - yesterday's close
- **Strike**: At-the-money (= spot price)
- **Barrier (Call)**: 10% below spot
- **Barrier (Put)**: 10% above spot
- **Volatility**: Historical (1-year annualized)
- **Time to Expiration**: 90 days (3 months)
- **Risk-free Rate**: 4.5% (Treasury yield)
- **Dividend Yield**: 1.5% (S&P average)

## Output Examples

When you run the notebook, you'll see:

1. **Data Summary** - S&P 500 spot price, volatility, data completeness
2. **Pricing Results** - Vanilla vs knockout prices for calls and puts
3. **Greeks Table** - All sensitivities side-by-side
4. **Visualization** - 4 professional charts
5. **Validation** - Manual vs QuantLib comparison (differences < 1%)

## Next Steps / Extensions

This foundation can be extended to:

- **Scenario Analysis** - Test different barrier levels, volatilities, time horizons
- **Deleveraging Simulation** - Model portfolio forced-selling scenarios
- **Web UI** - React/Flask interface for configuration and results
- **Backend API** - FastAPI service for automated pricing
- **Risk Reports** - Daily Greeks updates, hedge ratio calculations
- **Monte Carlo** - Simulate paths and calculate breach probabilities
- **Other Underlyings** - Extend to individual stocks, indices, FX pairs

## Educational Value

This project teaches:

- ✓ **Quantitative Finance** - Black-Scholes theory and implementation
- ✓ **Derivatives Pricing** - Barrier options and adjustments
- ✓ **Risk Management** - Greeks and hedging strategies
- ✓ **Python for Finance** - NumPy, SciPy, QuantLib
- ✓ **Data Science** - Fetching, calculating, and visualizing financial data
- ✓ **Validation Techniques** - Comparing custom implementations to industry standards

## Who This Is For

- **Finance Professionals** - Traders, quants, risk managers
- **Students** - Learning derivatives pricing and risk management
- **VPs/Decision Makers** - Proof-of-concept for pricing engines
- **Engineers** - Building financial software or ML models on top

## License

MIT - Free to use and modify

## Contact

Built by RajaChaiban | rajachaiban@gmail.com

---

**Last Updated**: April 24, 2026  
**Status**: Production-ready for educational and professional use
