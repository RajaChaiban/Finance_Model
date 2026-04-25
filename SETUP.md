# Finance Model Setup Guide

Complete installation and configuration guide for the derivatives pricing pipeline with Yahoo Finance integration.

## Prerequisites

- Python 3.7 or higher
- pip (Python package manager)

## Installation

### Step 1: Clone the Repository

```bash
git clone <repository-url>
cd Finance_Model
```

### Step 2: Install Dependencies

```bash
pip install -r requirements.txt
```

This installs all required packages:
- `numpy` — numerical computations
- `scipy` — scientific computing
- `pandas` — data manipulation
- `matplotlib` — charting
- `pyyaml` — configuration files
- `jinja2` — HTML templating
- `yfinance` — Yahoo Finance market data

## Configuration

### Basic Configuration

All pricing parameters are configured via YAML files in the `configs/` directory:

```bash
python main.py --config configs/american_put_spy.yaml
```

**Config file structure:**

```yaml
option:
  type: american_put              # american_put | european_put | european_call | knockout_call
  underlying: SPY
  spot_price: 5415.23            # Current spot price
  strike_price: 5400              # Strike price
  days_to_expiration: 90         # Days until expiration
  risk_free_rate: 0.045          # Annual risk-free rate
  volatility: 0.1845              # Annual volatility (sigma)
  dividend_yield: 0.015           # Annual dividend yield

pricing:
  n_paths: 10000                 # Monte Carlo paths (American options)
  n_steps: 90                    # Time steps per path
  variance_reduction: antithetic  # none | antithetic

output:
  report_format: html
  save_to: ./reports/
```

### Market Data Configuration

Yahoo Finance integration is **optional** but recommended for live market comparisons.

#### Default Settings

By default, the system uses:
- **Cache TTL**: 1 hour (avoid redundant API calls)
- **Timeout**: 10 seconds per request
- **Max Retries**: 3 attempts with exponential backoff
- **Retry Backoff**: 2x (2s, 4s, 8s between attempts)

#### Customize via Environment Variables

Override defaults by setting environment variables:

```bash
# Increase timeout to 20 seconds
export MARKET_DATA_TIMEOUT=20

# Reduce cache TTL to 30 minutes
export MARKET_DATA_CACHE_TTL=1800

# Increase retries to 5
export MARKET_DATA_MAX_RETRIES=5

# Run pipeline with live market data
python main.py --config configs/american_put_spy.yaml --fetch-market-data
```

All environment variables:

| Variable | Default | Unit | Purpose |
|----------|---------|------|---------|
| `MARKET_DATA_CACHE_TTL` | 3600 | seconds | Cache expiration time (1 hour) |
| `MARKET_DATA_TIMEOUT` | 10 | seconds | API request timeout |
| `MARKET_DATA_MAX_RETRIES` | 3 | count | Retry attempts on failure |
| `MARKET_DATA_RETRY_BACKOFF` | 2 | multiplier | Exponential backoff: 2^n |

## Usage

### Basic Pricing (Config Values Only)

Price an option using only config parameters:

```bash
python main.py --config configs/american_put_spy.yaml
```

**Output:**
- Console: Price, Greeks, risk score
- HTML report: `./reports/SPY_american_put_<timestamp>.html`

### Live Market Data Comparison

Fetch live spot price, volatility, and option bid/ask from Yahoo Finance:

```bash
python main.py --config configs/american_put_spy.yaml --fetch-market-data
```

**What this does:**
1. Fetches current spot price from Yahoo Finance
2. Calculates 30-day and 90-day historical volatility
3. Fetches option market bid/ask for structurer review
4. Generates pricing report + structurer analysis report
5. Compares model fair value to market mid price

**Output:**
- Console: Model price vs market mid, edge %, recommendation
- Pricing report: `./reports/SPY_american_put_<timestamp>.html`
- Structurer review: `./reports/SPY_american_put_structurer_<timestamp>.html`

### No Report Generation

Skip HTML reports and just print to console:

```bash
python main.py --config configs/american_put_spy.yaml --no-report
```

### Skip Structurer Review

Price option but skip the senior VP analysis:

```bash
python main.py --config configs/american_put_spy.yaml --no-structurer-review
```

## Troubleshooting

### "yfinance not installed"

The pipeline can run without yfinance — it will use config values only. To enable market data fetching:

```bash
pip install yfinance
```

### "Failed to fetch market data after 3 retries"

**Causes:**
- Network connectivity issue
- Yahoo Finance server is down
- Rate limit exceeded (too many requests in short time)

**Solutions:**
1. Check internet connection
2. Increase retry timeout: `export MARKET_DATA_TIMEOUT=20`
3. Increase retry attempts: `export MARKET_DATA_MAX_RETRIES=5`
4. Wait a few minutes and retry (rate limit resets)
5. Run without `--fetch-market-data` to skip market data

### "Invalid bid/ask prices"

Market data quality issues from Yahoo Finance. The system will:
1. Log a warning
2. Retry with exponential backoff
3. Fall back to using market_bid/market_ask as None if all retries exhausted
4. Continue pricing with model fair value only

**Solution:** Wait a moment and retry, or use config values only.

### Reports not generating

Ensure `./reports/` directory is writable:

```bash
mkdir -p ./reports/
chmod 755 ./reports/
```

## Running Tests

Test market data functionality:

```bash
pytest tests/test_market_data.py -v
```

Run all tests:

```bash
pytest tests/ -v
```

## Command Reference

| Command | Purpose |
|---------|---------|
| `python main.py --config configs/american_put_spy.yaml` | Price option, generate report |
| `python main.py --config configs/american_put_spy.yaml --fetch-market-data` | Price + fetch live market data |
| `python main.py --config configs/american_put_spy.yaml --no-report` | Price only, no HTML |
| `python main.py --config configs/american_put_spy.yaml --no-structurer-review` | Skip senior VP analysis |
| `pytest tests/test_market_data.py -v` | Run market data tests |

## Performance Tips

1. **Cache hit**: Caching avoids redundant API calls. Default 1-hour TTL.
2. **Batch runs**: Price multiple options in quick succession to benefit from cache.
3. **Network**: Higher timeout settings if network is slow (e.g., corporate VPN).
4. **Large simulations**: Increase `n_paths` for tighter confidence intervals (slower).

## Production Deployment

For production use:

1. **Logging**: Monitor `logging` output in logs for debugging
2. **Error handling**: The pipeline gracefully falls back to config values if market data unavailable
3. **Rate limiting**: Yahoo Finance may rate-limit requests; cache helps
4. **Monitoring**: Check `./reports/` directory for generated reports
5. **Database**: Consider caching market data in a database for high-volume runs

## Architecture Overview

```
Config YAML
    ↓
Load & Validate
    ↓
Fetch Market Data (optional)
    ├─ Spot price (retry, cache, logging)
    ├─ Dividend yield (retry, cache, logging)
    └─ Volatility (retry, cache, logging)
    ↓
Route to Pricing Engine
    ├─ Black-Scholes (European)
    ├─ Monte Carlo LSM (American)
    └─ Merton Barrier (Knockout)
    ↓
Calculate Greeks
    ├─ Analytical (Black-Scholes)
    └─ Bump-and-reprice (Monte Carlo)
    ↓
Generate Pricing Report
    ↓
Run Structurer Review
    ├─ Fetch market bid/ask (retry, logging)
    ├─ Compare to market mid
    ├─ Generate recommendation
    └─ Assess risk
    ↓
Generate Structurer Report
    ↓
Done
```

## Support

For issues or questions:
1. Check this guide
2. Review logs in console output
3. Check `./reports/` for generated reports
4. Verify config file syntax (YAML)
5. Ensure Python 3.7+ and all dependencies installed

## Additional Resources

- Black-Scholes pricing: See `src/engines/black_scholes.py`
- Monte Carlo: See `src/engines/monte_carlo_lsm.py`
- Market data: See `src/data/market_data.py`
- Structurer agent: See `src/analysis/structurer_agent.py`
