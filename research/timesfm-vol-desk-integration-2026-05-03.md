# TimesFM × Vol Desk: Time-Series Foundation Models in Quant Finance, and an Integration Plan

_Research date: 2026-05-03 • Depth: deep • Search: Tavily_

---

## Glossary of symbols and acronyms

> Read this once, then refer back. Every symbol below appears later in the report.

### Pricing & market variables

| Symbol | Name | Plain English |
|---|---|---|
| **S** | Spot price | The current price of the underlying (e.g. SPY at $510). |
| **K** | Strike | The price at which the option lets you buy (call) or sell (put). |
| **T** (or **τ**, "tau") | Time to expiry | Years until the option expires. 30 days ≈ T = 0.082. |
| **r** | Risk-free rate | The rate on a default-free deposit (USD: 3-month T-bill). |
| **q** | Dividend yield | Annualised continuous dividend on the underlying. |
| **B** | Barrier | Trigger level for KO / KI options. |
| **σ** ("sigma") | Volatility | Annualised std-dev of log-returns — the "wiggle" parameter. |
| **σ_R** (or **RV**) | Realized volatility | Backward-looking σ measured from past prices. |
| **σ_I** (or **IV**) | Implied volatility | Forward-looking σ that makes Black-Scholes match the market price. |
| **M** | Moneyness | K / S (or log(K/S)). "How far out-of-the-money is this option?" |
| **V** | Option price (value) | What the option is worth, in $. |

### The Greeks (sensitivities of V)

| Symbol | Name | Definition | Use |
|---|---|---|---|
| **Δ** ("delta") | Delta | ∂V/∂S | Directional hedge ratio (shares per option). |
| **Γ** ("gamma") | Gamma | ∂²V/∂S² = ∂Δ/∂S | How fast Δ changes — re-hedge frequency. |
| **𝒱** ("vega") | Vega | ∂V/∂σ | Vol exposure (per 1 % σ in your repo). |
| **Θ** ("theta") | Theta | ∂V/∂t | Time decay (per calendar day in your repo). |
| **ρ** ("rho") | Rho | ∂V/∂r | Rate exposure (per 1 % r in your repo). |

### Vol surface objects

| Symbol / term | Plain English |
|---|---|
| **Smile** σ_I (K) | Implied vol as a function of strike, holding expiry fixed. |
| **Term structure** σ_I (T) | Implied vol as a function of expiry, holding strike fixed. |
| **IV grid** σ_I (K, T) | The 2D discrete table of implied vols across strikes × expiries. |
| **Vol surface** σ_I^smooth(K, T) | The smooth interpolation/extrapolation of the IV grid. |
| **Local vol** σ_LV (S, t) | Dupire-style instantaneous vol used by FDM engines for smile-aware barrier pricing. |

### Pricing engines (the things you already understand)

| Acronym | Name | When it applies in your repo |
|---|---|---|
| **BS** | Black-Scholes (analytic) | European vanilla, closed-form. |
| **BIN** | Binomial tree | American (early exercise). |
| **FDM** | Finite-difference method | Smile-aware barriers (KO/KI under local vol). |
| **MC / LSM** | Monte Carlo / Longstaff-Schwartz | Asians, Lookbacks, complex paths. |
| **KO** / **KI** | Knock-Out / Knock-In | Barrier options. |

### Forecasting models (this report compares them)

| Acronym | Full name | Family |
|---|---|---|
| **ARIMA** | Autoregressive Integrated Moving Average | Classical statistics. |
| **ARFIMA** | Autoregressive Fractionally Integrated MA | Long-memory variant of ARIMA. |
| **GARCH** | Generalized Autoregressive Conditional Heteroskedasticity | Volatility model. Variants: EGARCH, GJR, FIGARCH. |
| **HAR** / **CHAR** | Heterogeneous Autoregressive (of realized variance) | The standard for realized-vol forecasting. |
| **GAS** | Generalized Autoregressive Score | Modern econometric VaR model. |
| **LSTM** | Long Short-Term Memory | Recurrent neural net. |
| **TFT** | Temporal Fusion Transformer | Supervised deep learning forecaster. |
| **TSFM** | Time-Series Foundation Model | The new family: Chronos, Moirai, TimesFM, Toto, Sundial, Kronos. |
| **TimesFM** | "Times" Foundation Model (Google) | The subject of this report. |

### TimesFM-specific symbols

| Symbol | Name | Typical value in TimesFM 2.5 |
|---|---|---|
| **L** | Context length (input history) | up to 16,000 points |
| **p_in** | Input patch length | 32 points |
| **p_out** | Output patch length | 128 points |
| **h** | Forecast horizon (output) | up to 256 (compiled), tune-able |
| **N** | Parameter count | 200 M |
| **q** | Quantile (in TimesFM output) | 11 quantiles per horizon step (mean + deciles 10–90 %) |

### Risk & evaluation metrics

| Acronym | Full name | What it measures |
|---|---|---|
| **VaR_α** | Value-at-Risk at confidence α | Loss not exceeded with probability 1–α (e.g. 1-day 99 % VaR). |
| **MASE** | Mean Absolute Scaled Error | Point-forecast error, scaled by naive baseline. Lower = better. |
| **CRPS** | Continuous Ranked Probability Score | How well the forecast *distribution* matches reality. Lower = better. |
| **MSE / MAE** | Mean Squared / Absolute Error | Standard regression errors. |
| **QLIKE** | Quasi-Likelihood loss | Asymmetric error favouring under-prediction of vol — the standard for vol forecast evaluation. |
| **SR** | Sharpe Ratio | (return − r) / σ_strategy. Risk-adjusted return. |
| **DM / GW test** | Diebold-Mariano / Giacomini-White | Statistical test that two forecasters differ in skill. |

### Benchmarks & artefacts

| Name | What it is |
|---|---|
| **GIFT-Eval** | Salesforce-AI / NeurIPS-2024 benchmark of 28 zero-shot forecasting datasets. |
| **M4** | Makridakis-4 forecasting competition dataset (100k series). |
| **ETT** | Electricity Transformer Temperature dataset, popular in long-horizon TS papers. |
| **TimesFM-ICF** | In-Context Fine-tuning variant of TimesFM. |

---

## TL;DR

Google's **TimesFM 2.5** (Sept 2025) is a 200 M-parameter (**N = 200 M**), decoder-only, patch-based transformer that forecasts univariate time series **zero-shot**. It accepts up to **L = 16,000** historical points, emits horizons up to **h = 256+** with **11 calibrated quantiles** per step, ranks **#1 on GIFT-Eval** by **MASE** (point) and **CRPS** (probabilistic), and ships under **Apache-2.0** [^1][^2][^3]. For your Vol Desk, TimesFM is a useful **forecasting layer** — most credibly for **realized-volatility (σ_R) nowcasting**, **dashboard "next-N-day" projections**, and **VaR_α tail quantiles** — but it is **not a drop-in replacement** for **GARCH** or **HAR**, nor for the QuantLib pricing engines (**BS**, **BIN**, **FDM**, **MC**), and **zero-shot on raw stock prices is weak** until you fine-tune on financial data [^4][^5][^6][^7].

---

## Part 1 — How time-series analysis works in quant finance

Time-series analysis is the part of quant that asks: _given what an asset/curve/rate
has done historically, what does the distribution of its future values look
like?_ Three layers, each present in your repo:

### Layer A — descriptive: the "stylized facts"

Empirical work since the 1990s established a stable list of properties that
financial returns r_t = log(S_t / S_{t-1}) exhibit, regardless of asset class [^8][^9]:

- **Heavy tails / leptokurtosis** — extreme moves happen far more often than a
  Gaussian assumption predicts. Roughly 3 % of S&P 500 daily moves exceed 3 σ
  under empirical kurtosis vs. ~0.27 % under normality.
- **Volatility clustering** — large |r_t| follow large |r_{t-1}|. Squared/absolute
  returns (r_t² and |r_t|) are strongly autocorrelated even when signed
  returns are not.
- **Leverage effect** — negative r_t increases future σ more than positive r_t
  of equal magnitude.
- **Non-stationarity / regime changes** — unconditional moments are not
  invariant in time; mean μ, variance σ² and correlations all shift across
  crises, monetary regimes and microstructure changes [^10][^7].
- **Long memory in volatility** — σ-autocorrelations decay hyperbolically
  (giving **HAR** / **ARFIMA** their power), while signed-return
  autocorrelations decay exponentially.

These facts drive **why** simple ARMA-style models for the price level fail
out-of-sample, and why volatility models exist as a separate species.

### Layer B — the classical workhorse models

| Family | Captures | Limitation |
|---|---|---|
| **ARIMA / SARIMA** | linear trend, seasonality | assumes constant variance, linear dependence; degrades fast at long horizons [^11][^12] |
| **GARCH (and EGARCH, GJR, FIGARCH, Realized-GARCH)** | volatility clustering, conditional heteroskedasticity | parametric, weak at structural breaks, univariate-by-default [^13][^7] |
| **HAR / CHAR / ARFIMA** | long memory of realized variance σ_R² | linear; breaks under regime shifts [^4] |
| **VAR / VECM** | linear cross-asset dependence | assumes joint stationarity |
| **Kalman / state-space** | latent factors, term structures | requires explicit state model |

These are still the institutional standards for risk and pricing inputs because
they're (a) fast, (b) interpretable, and (c) regulator-friendly.

### Layer C — modern deep-learning models

| Model | Strengths | Where they break |
|---|---|---|
| **LSTM / GRU** | non-linear, longer dependencies than ARIMA | overfits noisy financial data; weak probabilistic output [^11][^14] |
| **TCN, N-BEATS, TFT, PatchTST, iTransformer** | SOTA when training data exists | per-task training; not zero-shot |
| **TSFMs — Chronos, Moirai, TimesFM, Toto, Sundial, Kronos** | zero-shot or few-shot; cross-domain pretraining | generic ones miss financial stylized facts; need fine-tuning to match GARCH on tail quantiles [^7][^6] |

### Where time-series lives in **your** repo today

- **Historical price intake** — `src/data/market_data.py` (yfinance) and
  `src/api/market_data.py` (REST) drive the dashboard.
- **Movers / index strip** — `src/data/movers.py`, surfaced by
  `useMarketMovers` (60 s poll).
- **Implied-vol surface** σ_I^smooth(K, T) — `src/data/iv_grid.py` +
  `src/data/vol_surface.py`. **Cross-sectional today** (smile across K, term
  across T), not a temporal forecast.
- **Vol input to pricing** — `src/engines/router.py` dispatches to
  **QuantLib**; σ comes either from a constant or from the surface, with **no
  forward-looking statistical model** on top.
- **Scenario engine** — `src/scenarios/` shocks ΔS, Δσ, Δr as deterministic
  what-ifs. There is no probabilistic scenario sampler driven by a forecast
  distribution f(S_{t+h} | history).

In other words, **you have lots of cross-sectional vol intelligence and zero
forward-looking time-series forecasting** beyond the implicit forwards in IV.
That is the gap TimesFM can fill.

---

## Part 2 — What TimesFM actually is

### Architecture (one paragraph with symbols)

TimesFM is a **decoder-only transformer**, in the GPT lineage, but its "tokens"
are **patches** of contiguous time-points. Concretely: the input series of
length **L** is split into **L / p_in** patches of size **p_in = 32**. A
residual MLP projects each patch into a transformer-compatible embedding;
stacked causal self-attention layers run over the patch sequence; an output
residual block emits the **next p_out = 128** future points as a single
output patch. Crucially, **p_out > p_in**, so long horizons need **fewer
autoregressive steps** than a token-level model — that's what makes it fast
at inference [^1][^2][^15][^16].

### Versions

| Version | Released | N (params) | L (context) | Note |
|---|---|---|---|---|
| 1.0 | May 2024 (ICML) | 200 M | 512 | First public zero-shot **TSFM** [^3] |
| 2.0 | Dec 2024 / early 2025 | 500 M | 2,048 | Scaled up [^3] |
| **2.5** | **Sept 2025** | **200 M** | **16,000** | **Half N, 8× L, +25 % accuracy vs 2.0; #1 GIFT-Eval** [^17][^18][^3] |

The 2.0 → 2.5 jump shows that **L mattered more than N** for forecasting [^3].

### Training corpus

Pretrained on ~**100 B time-points** (Google Research's blog number; Pebblous
quotes 10 B for 2.5 specifically), **~80 % real + ~20 % synthetic**, sourced
from Google Trends, Wikipedia Pageviews, **M4**, **ETT**, plus a synthetic
generator producing ARMA-like and seasonal patterns [^1][^4][^19].
**Note:** financial data is **not** a primary part of pretraining. This is the
single most important fact for our use case.

### Open-source status

- **License**: Apache-2.0 [^15][^20].
- **Repo**: `github.com/google-research/timesfm` [^20].
- **Weights**: `huggingface.co/google/timesfm-2.5-200m-pytorch` and the
  Transformers port `google/timesfm-2.5-200m-transformers` [^15][^21].
- **Install**: `pip install -e .` from the repo (PyPI under `timesfm`); since
  April 2026 there's a HuggingFace Transformers + PEFT (**LoRA**)
  fine-tuning example at `timesfm-forecasting/examples/finetuning/` [^20].
- **Production endpoints**: BigQuery ML (GA), Vertex AI Model Garden, Google
  Sheets [^3][^22].

### Inference API (the shape that matters for FastAPI integration)

```python
import numpy as np, timesfm
model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(
    "google/timesfm-2.5-200m-pytorch", torch_compile=True
)
model.compile(timesfm.ForecastConfig(
    max_context=1024,           # L
    max_horizon=256,            # h
    normalize_inputs=True,
    use_continuous_quantile_head=True,
    fix_quantile_crossing=True,
))
point, quantile = model.forecast(horizon=12, inputs=[series1, series2])
# point.shape    == (batch, h)        e.g. (2, 12)
# quantile.shape == (batch, h, 11)    mean + deciles 10..90
```
[^15]

That last shape is what makes TimesFM useful for risk: **11 calibrated
quantiles per horizon step** — exactly the input a probabilistic dashboard or
a **VaR_α** backend wants.

---

## Part 3 — Performance benchmarks

### Generic zero-shot — GIFT-Eval (Salesforce 2024)

GIFT-Eval covers 28 datasets across 7 frequencies. TimesFM 2.5 is **#1 among
zero-shot foundation models** on both **MASE** (point) and **CRPS**
(probabilistic), with no exposure to the train splits [^2][^3][^17].

### Finance-specific, peer-reviewed results

The picture is **task- and fine-tuning-dependent**:

| Task | Setup | Result vs. classical | Source |
|---|---|---|---|
| **Realized vol σ_R** (21 indices, 2000–2021) | TimesFM 2.0 + incremental log-fine-tune | **Beats HAR, CHAR, ARFIMA, Realized-GARCH** on MSE / MAE / QLIKE / MAPE / MDA / sMAPE; significant by **DM / GW** test | [^4] |
| **Value-at-Risk VaR_α** (S&P 100, 2005–2023) | TimesFM linear-probe fine-tune | **Beats GAS at α = 1 %, 2.5 %, 5 % tails** (violation ratios at 1-day); **comparable on quantile loss**; **weak at moderate α and longer horizons** | [^5] |
| **Daily price direction** (TOPIX500 + S&P500, 100 M+ time points) | TimesFM continual-pretrain fine-tune | **SR = 1.68** on S&P500 mock-trade, **SR = 1.06** on TOPIX500, beats AR(1) and zero-shot TimesFM; **underperforms on FX & crypto** | [^6] |
| **Single-stock zero-shot** (AAPL/AMZN, Dow Jones) | TimesFM 1.0 zero-shot | Compares favourably with **LSTM** at h = 1; degrades fast at longer h | [^14] |

**Headline takeaway**: zero-shot TimesFM is competitive on **σ_R**
(stationary-ish stylized facts) and on **tail VaR_α**. It is **not**
competitive on raw price levels without fine-tuning, and it struggles with the
dynamic / conditional aspects of risk that **GARCH**-family models were
designed for [^5][^7].

### Where it is known to fail

- Multivariate / cross-asset interaction (no native multivariate; **Chronos-2**
  and **Moirai-MoE** are stronger here) [^3][^7].
- Event-driven, high-entropy series (web ops, sudden regime breaks) [^3].
- Conditional coverage / dynamic-quantile tests for **VaR_α** at long h —
  the model misses volatility-clustering dynamics that **GARCH** captures
  parametrically [^5].
- Distribution drift — accuracy degrades when live data is far from anything
  in pretraining (financial data is precisely such an OOD domain) [^7].

---

## Part 4 — Integration plan for Vol Desk

### Where TimesFM provides leverage in your existing stack

Five plausible integration points, in order of value-to-effort:

#### (1) **Dashboard "next-N-day" projection on the index strip and movers grid** — quick win
- **Where**: `src/api/market_data.py` and `src/data/movers.py`, surfaced by
  a new `/api/market/forecast` endpoint and a new component
  (`frontend/src/components/IndexForecastBand.tsx`) under `IndexTickerStrip`
  or `MoversGrid`.
- **What**: For each tracked index, fetch 256–1024 daily closes, call
  `model.forecast(horizon=10)`, render the median + 10 / 90 quantile band
  over the price chart.
- **Why it works**: 10-day h on liquid indices is well-conditioned; the user
  needs **directional + uncertainty band**, not a tradable signal; the
  quantile output is calibrated for free [^15].
- **Effort**: backend ~150 LOC, frontend ~80 LOC, ~1 day.

#### (2) **Realized-vol σ_R nowcast as a vol-surface input** — high value
- **Where**: extend `src/data/vol_surface.py` (or add `src/data/rv_forecast.py`)
  and let the smile builder optionally weight the **forward leg of the term
  structure** σ_I(T) with a TimesFM σ_R forecast.
- **What**: Compute 5-min realized variance σ_R² on the underlying for the
  past N days (intraday data needed — yfinance gives 1m/5m for ~7d; consider
  Polygon/IB later), feed √σ_R² into TimesFM, get a τ-day-ahead σ_R forecast,
  blend into the IV grid σ_I(K, T) as a prior for short expiries [^4].
- **Why it works**: This is the **strongest published finance result** for
  TimesFM (with fine-tuning); even zero-shot it beats **HAR** on multiple
  loss functions for liquid index σ_R [^4].
- **Effort**: ~3–5 days; biggest cost is intraday data plumbing, not the
  model.

#### (3) **Probabilistic scenario engine for the option book** — high value, larger scope
- **Where**: extend `src/scenarios/`. Currently shocks ΔS / Δσ / Δr are
  deterministic. Add a `TimesFMScenarioGenerator` that samples paths from
  the quantile head q.
- **What**: For each leg in a structured note, pull (a) underlying-price
  forecast distribution f(S_{t+h}), (b) realized-vol forecast f(σ_R, t+h),
  (c) optionally a rate forecast on a FRED series. Combine via your copula
  assumption, run **QuantLib** pricing per scenario, return the P&L
  distribution and tail metrics (**VaR_α**, expected shortfall ES_α).
- **Why it works**: the quantile output replaces user-specified shock
  magnitudes with a **data-driven** distribution — exactly what a
  structuring desk wants from a co-pilot.
- **Effort**: ~1 week.

#### (4) **VaR_α / Greek-attribution dashboard** — medium value, depends on portfolio plumbing
- **Where**: needs a portfolio-state object you don't currently have (the
  platform is single-trade today). When a "blotter" lands, this becomes the
  natural risk view.
- **What**: Daily VaR_α at α ∈ {1 %, 2.5 %, 5 %} using TimesFM quantiles on
  each position's underlying, aggregated through QuantLib **Δ / 𝒱** exposures.
- **Why**: TimesFM was empirically shown to beat **GAS** on extreme-quantile
  violation ratios at 1-day horizon [^5].
- **Caveat**: At h ∈ {21, 63} days **GAS** still wins on conditional
  coverage; **don't** sell this as a long-horizon risk tool [^5].

#### (5) **Co-pilot agent tool** — low effort, high agent UX win
- **Where**: `src/agents/orchestrator.py` exposes tool functions to Gemini.
  Add `forecast_underlying(ticker, h)` and `forecast_realized_vol(ticker, h)`.
- **What**: Let the structuring agents call TimesFM mid-conversation —
  e.g. when the strategist proposes a strike K, it can ground that on a
  10-day forecast distribution rather than just spot S.
- **Effort**: ~½ day, gated on (1) being deployed.

### Architecture notes (concrete to your repo)

- **Model loading**: TimesFM 2.5 weights are ~800 MB. Load **once at app
  startup** in `src/api/main.py` lifespan handler — not per request (the
  single most common FastAPI / ML mistake) [^23].
- **Inference cost**: ~50–200 ms per `forecast()` call on CPU for a typical
  batch of 1–10 univariate series, sub-100 ms with `torch_compile=True` and a
  modest GPU. Acceptable for /api/price-style synchronous handlers if you
  batch.
- **Caching**: forecasts are deterministic given input. Cache by
  `(ticker, h, last_close_timestamp)` with a 5-minute TTL — the movers grid
  polls every 60 s but the underlying close changes only at daily granularity
  for the dashboard use case.
- **Tests**: extend `tests/` with a `test_timesfm_forecast.py` that pins on a
  tiny synthetic series with known shape (e.g. AR(1)) and asserts the median
  forecast tracks within tolerance — same pattern as `test_engines.py`.
- **Don't break existing engines**: TimesFM is **additive**. The QuantLib
  router and option pricing path stays untouched. Forecasts go into
  **inputs** (σ prior, scenario sampling), not pricing itself.
- **Frontend types**: when adding `/api/market/forecast`, mirror in
  `frontend/src/types.ts` and the API client per CLAUDE.md's "if you change
  request/response shape, update both" rule.
- **Hardware**: Run on CPU first; modern x86 / Apple-Silicon CPUs handle
  TimesFM 2.5 200 M comfortably for the dashboard. Reserve a GPU only if you
  batch hundreds of forecasts (portfolio VaR_α sweeps).

### Risks / non-goals

- **Don't market it as a price-prediction model.** Zero-shot TimesFM is weak
  on raw equity prices [^7][^14]. Honest framing: **"calibrated uncertainty
  bands and short-horizon σ_R nowcasting"**, not alpha.
- **Don't replace QuantLib.** Pricing, Greeks (Δ, Γ, 𝒱, Θ, ρ) and barrier
  mechanics stay in the engines.
- **Fine-tuning is a real engineering project.** PFN's SR = 1.68 result
  used 100 M+ time points and a custom log-loss to handle flash crashes [^6].
  Plan for it as a future phase, not part of the initial wire-up.
- **Multivariate work needs a different model.** If cross-asset is the
  point, **Chronos-2** (Amazon) or **Moirai-2** (Salesforce) are stronger;
  ensembling all three is exactly what TimeCopilot does on GIFT-Eval [^24].

---

## Part 5 — Disagreements & open questions

- **Pretraining size: 10 B vs 100 B time-points.** Pebblous reports 10 B for
  TimesFM 2.5 [^3]; Yuv.ai, Kinlay, and Google Research's own blog report
  100 B [^1][^19][^7]. Single-source-each on the discrepancy; the 100 B
  figure is the one Google itself used in the ICML paper.
- **Is TimesFM 2.5 actually #1 zero-shot, or tied with Chronos-2 / Moirai-2?**
  Google's own posts claim outright #1 on accuracy [^17][^18]. GIFT-Eval's
  most recent runs (Nov 2025) show ensembles of TimesFM 2.5 + Chronos-2 +
  TiRex doing better than any single model [^24]. Read "#1" as "#1 single
  open-source univariate model".
- **Does fine-tuning generalise across asset classes?** PFN's S&P/TOPIX
  fine-tune helped equities but **hurt FX and crypto** [^6] — a
  one-size-fits-all financial fine-tune does not exist yet.
- **Long-horizon risk:** TimesFM beats **GAS** on 1-day extreme quantiles but
  loses at h = 21 and 63 days [^5]. There is no public TimesFM result for
  monthly/quarterly h that beats classical **GARCH** on conditional coverage.
  (One source — flag as unverified.)

---

## Sources

[^1]: Das et al., "A decoder-only foundation model for time-series forecasting" — Google Research blog —
       https://research.google/blog/a-decoder-only-foundation-model-for-time-series-forecasting/ (accessed 2026-05-03)
[^2]: MarkTechPost, "Google AI Ships TimesFM-2.5: Smaller, Longer-Context Foundation Model That Now Leads GIFT-Eval" — 2025-09-16 —
       https://www.marktechpost.com/2025/09/16/google-ai-ships-timesfm-2-5-smaller-longer-context-foundation-model-that-now-leads-gift-eval-zero-shot-forecasting/ (accessed 2026-05-03)
[^3]: Pebblous, "TimesFM Time-Series Foundation Model: Predictive Maintenance and Anomaly Detection in Manufacturing" —
       https://blog.pebblous.ai/report/timesfm-industrial-forecasting/en/ (accessed 2026-05-03)
[^4]: arXiv 2505.11163, "Foundation Time-Series AI Model for Realized Volatility Forecasting" —
       https://arxiv.org/html/2505.11163v1 (accessed 2026-05-03)
[^5]: arXiv 2410.11773, "Time-Series Foundation AI Model for Value-at-Risk Forecasting" —
       https://arxiv.org/html/2410.11773v7 (accessed 2026-05-03)
[^6]: Preferred Networks, "Predicting market prices with a financial time series foundation model" —
       https://tech.preferred.jp/en/blog/timesfm/ (accessed 2026-05-03)
[^7]: Jonathan Kinlay, "Time Series Foundation Models for Financial Markets: Kronos and the Rise of Pre-Trained Market Models" — 2026-02 —
       https://jonathankinlay.com/2026/02/time-series-foundation-models-for-financial-markets-kronos-and-the-rise-of-pre-trained-market-models/ (accessed 2026-05-03)
[^8]: Chetalova, "Dependencies and non-stationarity in financial time series" —
       https://duepublico2.uni-due.de/servlets/MCRFileNodeServlet/duepublico_derivate_00040575/chetalova_diss.pdf (accessed 2026-05-03)
[^9]: Pfaff, "Modelling Financial Risks: Fat Tails, Volatility Clustering and Copulae" —
       https://pfaffikus.de/talks/rif/files/rif2010.pdf (accessed 2026-05-03)
[^10]: Mapleridge Capital, "Regime Changes in Non-Stationary Time-Series" —
       https://www.cityu.edu.hk/rcms/WIA2009/problems/ChangePoints-Mapleridge%20Capital%20Corporation.pdf (accessed 2026-05-03)
[^11]: Goncalves, Alexandre & Lima, "ARIMA and LSTM: A Comparative Analysis of Financial Time Series Forecasting" —
       https://repec.eae.fea.usp.br/documentos/Goncalves_Alexandre_Lima_13WP.pdf (accessed 2026-05-03)
[^12]: SHS Web of Conferences, "A Comparative Analysis of ARIMA-GARCH, LSTM, and Integrated Models" —
       https://www.shs-conferences.org/articles/shsconf/pdf/2024/16/shsconf_edma2024_02008.pdf (accessed 2026-05-03)
[^13]: NIU Honors Capstone, "Application of Time Series Models (ARIMA, GARCH, ARMA-GARCH) for Stock Market Forecasting" —
       https://huskiecommons.lib.niu.edu/cgi/viewcontent.cgi?article=1176&context=studentengagement-honorscapstones (accessed 2026-05-03)
[^14]: Emerging Investigators, "Stock price prediction: Long short-term memory vs. Autoformer and TimesFM" —
       https://emerginginvestigators.org/articles/24-228/pdf (accessed 2026-05-03)
[^15]: Hugging Face, "google/timesfm-2.5-200m-pytorch" —
       https://huggingface.co/google/timesfm-2.5-200m-pytorch (accessed 2026-05-03)
[^16]: AI Horizon Forecast (Kafritsas), "TimesFM: Google's Foundation Model For Time-Series Forecasting" —
       https://aihorizonforecast.substack.com/p/timesfm-googles-foundation-model (accessed 2026-05-03)
[^17]: Google Research (LinkedIn), "TimesFM-2.5 release announcement" —
       https://www.linkedin.com/posts/googleresearch_we-just-released-the-weights-of-timesfm-25-activity-7373417923991875584-6zdI (accessed 2026-05-03)
[^18]: Yossi Matias (LinkedIn), "TimesFM-2.5: A New Standard for Zero-Shot Forecasting" —
       https://www.linkedin.com/posts/yossimatias_we-have-released-timesfm-25-the-new-leader-activity-7373505442469097473-Buy_ (accessed 2026-05-03)
[^19]: YUV.AI, "TimesFM: Zero-Shot Time-Series Forecasting Without Training Data" —
       https://yuv.ai/blog/timesfm (accessed 2026-05-03)
[^20]: GitHub, "google-research/timesfm" —
       https://github.com/google-research/timesfm (accessed 2026-05-03)
[^21]: Hugging Face, "google/timesfm-2.5-200m-transformers" —
       https://huggingface.co/google/timesfm-2.5-200m-transformers (accessed 2026-05-03)
[^22]: Hugging Face Transformers, "TimesFM model docs" —
       https://huggingface.co/docs/transformers/model_doc/timesfm (accessed 2026-05-03)
[^23]: Bing Info, "Building a Scalable and Intelligent Real-Time Inference System with FastAPI" —
       https://binginfo.in/building-a-scalable-and-intelligent-real-time-inference-system-with-fastapi/ (accessed 2026-05-03)
[^24]: TimeCopilot, "First-Place Results on the GIFT-Eval Benchmark" —
       https://timecopilot.dev/experiments/gift-eval/ (accessed 2026-05-03)
