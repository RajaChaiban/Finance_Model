# Free Data APIs for the Vol Desk RAG Corpus

_Research date: 2026-05-03 • Depth: medium • Search: Tavily_

## TL;DR

Of the 10 providers researched, **3 are free with no caveats** (SEC EDGAR, CBOE public CSVs, yfinance), **4 have meaningful free tiers** (Alpha Vantage, Twelve Data, Tiingo, Tradier sandbox), **2 are not useful for the co-pilot** (Polygon free has no options; NewsAPI free is localhost-only), and **1 is dead** (IEX Cloud shut down Aug 31 2024). For your RAG enrichment, sign up in this order: SEC EDGAR (free, no signup), CBOE (free, no signup), Alpha Vantage (free key, options + earnings), Tiingo (free key, EOD prices), Twelve Data (free key, 800 req/day macro).

## Key findings

- **Free, no key needed** — SEC EDGAR (10 req/sec rate limit, all filings since 1993, must declare User-Agent header) [^1][^2]; CBOE VIX/SPX historical CSVs at `cboe.com/tradable-products/vix/` and `datahub.io/core/finance-vix` [^3][^4]; yfinance (Yahoo Finance scraper, already used in your repo) [^5][^6].
- **Free key, daily-quota tiers worth signing up for** — Alpha Vantage (25 calls/day, 5/min — includes `REALTIME_OPTIONS`, `HISTORICAL_OPTIONS`, `NEWS_SENTIMENT`, `EARNINGS_CALENDAR`, `IPO_CALENDAR`) [^7][^8]; Twelve Data Basic (800 calls/day, 3 markets — no US options on free) [^9]; Tiingo Starter (500 symbols/mo, 1000 calls/day, EOD prices + 30yr history; **news + fundamentals are paid**) [^10][^11]; Tradier sandbox (free with delayed/simulated data; live data is $10/mo or free with brokerage account) [^12][^13].
- **Polygon.io free tier is rate-limited (5 calls/min) and explicitly excludes options data** — options requires the $79/mo Options plan [^14][^15]. For your use case the free tier is not useful.
- **NewsAPI.org free tier is restricted to localhost / development only** (100 req/day, no production use); production starts at $449/mo [^16]. Better free news alternatives below.
- **IEX Cloud shut down Aug 31 2024** [^17][^18][^19]. Don't sign up.
- **Bonus finds the question didn't ask about, but you should know** — Finnhub (~60 calls/min on free, US quotes + earnings calendar + news sentiment) [^20]; Financial Modeling Prep (250 req/day free, includes SEC EDGAR-derived fundamentals) [^21]; GNews / Currents API / NewsData.io (genuine free news with 100–600 req/day, less restrictive than NewsAPI.org) [^16].

## Details

### Polygon.io — paid for options [^14][^15]

Free: 5 calls/min, **stocks only — no options on free tier**.
Paid: $29/mo Stocks Starter; **options requires the +$79/mo Options plan on top** ($108/mo total for retail-tier options chains). Tick-level history, websocket streaming, full chains with Greeks, S3 bulk downloads on paid tiers. Best raw infrastructure in the retail-accessible price range.
**Verdict for the co-pilot**: skip free; pay $108/mo if you commit. [Sign up](https://polygon.io/pricing)

### SEC EDGAR — free, no key, ★★★★★ [^1][^2]

Free, no signup. Hard rate limit: **10 requests/second per IP**. Must declare a `User-Agent: <name> <email>` header (SEC will block requests without it). Coverage: every filing since 1993 — 10-K, 10-Q, **8-K, Form D**, 13F, S-1, DEF 14A, 424B4. Real-time stream via the SEC's RSS endpoint. Form D filings are the gold mine for structured-product term-sheet precedents (the gap your RAG audit flagged).
**Verdict**: Tier 1 priority. Build `scripts/ingest_edgar_termsheets.py` filtering 8-K and Form D for "structured note" / "autocallable" / "buffered" → emit `doc_type="deal"` with the filing date as `as_of`. [Direct access](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data) — no signup.

### CBOE public data — free, no key, ★★★★★ [^3][^4][^22]

Free, no signup. Daily VIX term structure published at `cboe.com/tradable-products/vix/term-structure/` (table of all VIX futures by expiration). Historical VIX index time series (since 1990) at `cboe.com/tradable_products/vix/vix_historical_data` and `datahub.io/core/finance-vix` (CSV download, updated daily). DataShop is the paid product for custom historical options data — not needed for vol regime context.
**Verdict**: Tier 1 priority. Daily cron to fetch VIX term-structure CSV → emit `doc_type="market_window"` with `as_of=today`. Closes the "≥30 dated market_windows" floor instantly.

### Yahoo Finance — official API dead, yfinance scraper alive [^5][^6]

The official Yahoo Finance API was shut down. The community-maintained `yfinance` Python library scrapes the unofficial endpoints — already used in your repo at `src/data/market_data.py`. Options chains accessible via `yf.Ticker("SPY").option_chain(expiry)`. Fragile (Yahoo changes endpoints periodically) but free and works today.
**Verdict**: already wired for the smile builder; tap the same data into MI as a daily `market_window` doc per ticker. Highest "free dollars on the floor."

### Alpha Vantage — free key, ★★★★ [^7][^8]

Free: **25 API calls/day, 5/min**. Free tier includes a real options endpoint: `REALTIME_OPTIONS`, `HISTORICAL_OPTIONS` (since 2008), plus `NEWS_SENTIMENT`, `EARNINGS_CALENDAR`, `IPO_CALENDAR`, `INSIDER_TRANSACTIONS`, `INSTITUTIONAL_HOLDINGS`. Paid plans start $49.99/mo for 75 calls/min. The 25/day cap is tight but enough for one daily morning enrichment of ~5 tickers.
**Verdict**: Tier 1. Free key signup is genuinely free, no card. [Sign up](https://www.alphavantage.co/support/#api-key)

### NewsAPI.org — free is localhost only [^16]

Free tier explicitly restricted to "development on localhost" — 100 requests/day, no production use. Production starts at $449/mo. Not useful for a daily ingestion cron.
**Verdict**: skip. Use **GNews**, **Currents API** (600 req/day free), or **NewsData.io** (200 req/day free) instead — all listed as genuine free alternatives.

### Twelve Data — free 800 req/day, no options on free [^9][^23]

Free Basic: **800 API calls/day, 8 credits/min, 3 markets**. Real-time US stocks, fundamentals, options — **all gated to paid Grow tier ($29/mo)**. Free tier covers EOD global equities + macro indicators (GDP, inflation, unemployment) but not options chains.
**Verdict**: Tier 2 — useful for macro context augmenting your FRED ingester. Sign up for the macro indicators alone. [Sign up](https://twelvedata.com/pricing)

### IEX Cloud — DEAD [^17][^18][^19]

Shut down **Aug 31, 2024**. All endpoints disabled. Don't sign up. Migration paths: Alpha Vantage, Tiingo, Polygon, or community drop-in shims like Apify's "iex-cloud-replacement" actor. Multiple search results confirm — three independent sources [^17][^18][^19].

### Tradier — free sandbox, $10/mo for live data [^12][^13]

Sandbox account: free, delayed/simulated data — useful for testing your code, **not useful for live RAG ingestion** (the data isn't real). Real market data is $10/mo add-on, **or bundled free with a Tradier brokerage account**. Options chains include basic Greeks; no advanced analytics (no GEX, no vol surface).
**Verdict**: only worth it if you'd open a Tradier brokerage account anyway (US-only, options-friendly). Otherwise skip — Alpha Vantage's free options endpoint is a better starting point. [Sign up](https://documentation.tradier.com/)

### Tiingo — free EOD only [^10][^11][^24]

Free Starter: **500 symbols/mo, 50 req/hour, 1000 req/day, 1GB bandwidth/mo, 30+ years EOD history, IEX real-time feed**. **Free tier does NOT include news or fundamentals** — both require Power tier ($30/mo individual; $10/mo per QuantStart's older quote). The free tier is genuinely useful for EOD prices but doesn't help the news/sentiment gap.
**Verdict**: Tier 2 — sign up if you want backup EOD price history. Don't expect news on free. [Sign up](https://www.tiingo.com/about/pricing)

### Bonus: Finnhub — generous free tier [^20]

~60 calls/min on free, includes US quotes, news sentiment, earnings calendar, ESG, fundamentals. Officially maintained (not a wrapper). Often the most generous free tier among the alternatives, though search results are less detailed than the named providers above.
**Verdict**: worth a separate evaluation. [Sign up](https://finnhub.io/)

## Disagreements & open questions

- **Polygon free tier coverage** — One source (api.market via API.market reseller) advertises a "Free Trial: $0 — 7-day trial, 100 API units, no credit card" [^15], conflicting with FlashAlpha's "5 calls/min, no options" [^14]. Resolution: those are different products. Polygon's own free tier (direct signup) is the rate-limited one; api.market is a third-party reseller bundling Polygon. For our purpose, sign up directly with Polygon.
- **Tiingo Power tier price** — newer source (Tiingo's own pricing page, [^11]) lists $30/mo; older QuantStart article [^24] says $10/mo. Use $30/mo as the current figure (2026 pricing).
- **Alpha Vantage free options endpoint coverage** — confirmed by Alpha Vantage's own MCP tool listing [^25] which categorises `REALTIME_OPTIONS` and `HISTORICAL_OPTIONS` under `options_data_apis`, and the documentation explicitly references options endpoints [^8]. (One source, but it's the vendor's own.)

## Sign-up priority order for your RAG corpus

1. **SEC EDGAR** (free, no key) — closes the term-sheet precedent gap. Tier 1.
2. **CBOE VIX/SPX CSVs** (free, no key) — closes the dated market-window gap. Tier 1.
3. **Alpha Vantage** (free key, no card) — adds options/news/earnings/insider data. Tier 1.
4. **Tiingo** (free key) — backup EOD prices, 30yr history. Tier 2.
5. **Twelve Data** (free key) — macro indicators alongside FRED. Tier 2.
6. **Finnhub** (free key) — earnings calendar + news sentiment, generous free tier. Tier 2.
7. **Tradier sandbox** (free) — only if you'd open a brokerage account. Tier 3.
8. **Polygon.io** — skip free; budget $108/mo if/when you commit to options. Tier 3.

Skip entirely: NewsAPI.org (localhost-only free), IEX Cloud (dead), Yahoo official API (dead — use yfinance directly).

## Sources

[^1]: SEC.gov | Accessing EDGAR Data — https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data (accessed 2026-05-03)
[^2]: SEC Applies New Rate Control Limits to EDGAR Websites — https://www.novaworkssoftware.com/blog/archives/781-SEC-Applies-New-Rate-Control-Limits-to-EDGAR-Websites.html (accessed 2026-05-03)
[^3]: VIX Term Structure | Cboe — https://www.cboe.com/tradable-products/vix/term-structure/ (accessed 2026-05-03)
[^4]: CBOE Volatility Index — https://datahub.io/core/finance-vix (accessed 2026-05-03)
[^5]: How To Use The Yahoo Finance API | Market Data — https://www.marketdata.app/alternatives/yahoo-finance-api/ (accessed 2026-05-03)
[^6]: Yahoo Finance Options Data Download with Python yfinance — https://www.macroption.com/yahoo-finance-options-python/ (accessed 2026-05-03)
[^7]: Alpha Vantage API Request Limits — https://www.macroption.com/alpha-vantage-api-limits/ (accessed 2026-05-03)
[^8]: Alpha Vantage API Documentation — https://www.alphavantage.co/documentation/ (accessed 2026-05-03)
[^9]: Individual Pricing - Twelve Data — https://twelvedata.com/pricing (accessed 2026-05-03)
[^10]: Evaluating Data Coverage with Tiingo | QuantStart — https://www.quantstart.com/articles/evaluating-data-coverage-with-tiingo/ (accessed 2026-05-03)
[^11]: Tiingo API Pricing | Tiingo — https://www.tiingo.com/about/pricing (accessed 2026-05-03)
[^12]: Options API Comparison 2026: Tradier, Polygon, Intrinio | FlashAlpha — https://flashalpha.com/articles/options-api-comparison-flashalpha-tradier-polygon-intrinio (accessed 2026-05-03)
[^13]: Build & Trade with Tradier | Developer-Friendly Trading API — https://trade.tradier.com/developer-api/ (accessed 2026-05-03)
[^14]: Best Options Data APIs in 2026 — FlashAlpha — https://flashalpha.com/articles/best-options-data-apis-2026 (accessed 2026-05-03)
[^15]: 5 Best API for Stock Market Data All Over the World [2026] — https://api.market/blog/MagicAPI/stock-market-api/best-api-for-stock-market-data-all-over-the-world-2026 (accessed 2026-05-03)
[^16]: Best News API for Developers (2026) — https://newsmesh.co/best-news-apis (accessed 2026-05-03)
[^17]: IEX Cloud Has Shut Down: Analysis & Migration Guide — https://www.alphavantage.co/iexcloud_shutdown_analysis_and_migration/ (accessed 2026-05-03)
[^18]: ⚡ IEX Cloud Replacement — Apify Actor — https://apify.com/nexgendata/iex-cloud-replacement (accessed 2026-05-03)
[^19]: IEX Cloud is gone. Why? — https://finazon.io/blog/iex-cloud-is-gone-why (accessed 2026-05-03)
[^20]: 12 Best Financial Market APIs for Real-Time Data in 2026 — https://blog.apilayer.com/12-best-financial-market-apis-for-real-time-data-in-2026/ (accessed 2026-05-03)
[^21]: Financial Data APIs Compared (2026) — https://www.ksred.com/the-complete-guide-to-financial-data-apis-building-your-own-stock-market-data-pipeline-in-2025/ (accessed 2026-05-03)
[^22]: Cboe Global Markets — VIX Historical Data — https://www.cboe.com/tradable_products/vix/vix_historical_data (accessed 2026-05-03)
[^23]: FXMacroData vs. Twelve Data — https://fxmacrodata.com/articles/fxmacrodata-vs-twelve-data (accessed 2026-05-03)
[^24]: Best Financial Data APIs in 2026 — https://www.nb-data.com/p/best-financial-data-apis-in-2026 (accessed 2026-05-03)
[^25]: Alpha Vantage MCP for Stock Market Data — https://mcp.alphavantage.co/ (accessed 2026-05-03)
