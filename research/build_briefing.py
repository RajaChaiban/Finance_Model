"""research/build_briefing.py — Morning Macro & Equity Briefing builder.

Produces research/briefing.json with macro rates, equity indices, sector ETFs,
vol regime, and market headlines.  Runnable standalone:

    python research/build_briefing.py

Idempotent — overwrites the file each run.
No new heavy deps: uses httpx, requests, yfinance (all in requirements.txt).
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Dependency imports — graceful fallbacks
# ---------------------------------------------------------------------------
try:
    import httpx
    _HTTPX = True
except ImportError:
    _HTTPX = False

try:
    import requests as _requests
    _REQUESTS = True
except ImportError:
    _REQUESTS = False

try:
    import yfinance as yf
    _YF = True
except ImportError:
    _YF = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
OUTPUT_PATH = Path(__file__).parent / "briefing.json"

FRED_CSV_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="
FRED_API_BASE = "https://api.stlouisfed.org/fred/series/observations"

MACRO_SERIES: List[Dict[str, str]] = [
    {"series_id": "DGS10", "label": "10Y Treasury",       "unit": "%"},
    {"series_id": "DGS2",  "label": "2Y Treasury",        "unit": "%"},
    {"series_id": "T10Y2Y","label": "10Y-2Y Spread",      "unit": "%"},
    {"series_id": "DFF",   "label": "Fed Funds Rate",     "unit": "%"},
    {"series_id": "SOFR",  "label": "SOFR",               "unit": "%"},
    {"series_id": "VIXCLS","label": "VIX (FRED)",         "unit": "index"},
]

INDEX_SYMBOLS: List[Dict[str, str]] = [
    {"symbol": "^GSPC",  "name": "S&P 500"},
    {"symbol": "^IXIC",  "name": "NASDAQ Composite"},
    {"symbol": "^DJI",   "name": "Dow Jones Industrial Average"},
    {"symbol": "^RUT",   "name": "Russell 2000"},
]

SECTOR_ETFS: List[Dict[str, str]] = [
    {"symbol": "XLK",  "sector": "Technology"},
    {"symbol": "XLF",  "sector": "Financials"},
    {"symbol": "XLE",  "sector": "Energy"},
    {"symbol": "XLV",  "sector": "Health Care"},
    {"symbol": "XLY",  "sector": "Consumer Discretionary"},
    {"symbol": "XLP",  "sector": "Consumer Staples"},
    {"symbol": "XLI",  "sector": "Industrials"},
    {"symbol": "XLU",  "sector": "Utilities"},
    {"symbol": "XLRE", "sector": "Real Estate"},
    {"symbol": "XLB",  "sector": "Materials"},
    {"symbol": "XLC",  "sector": "Communication Services"},
]

TODAY = "2026-05-03"
TITLE = "US Macro & Equity Briefing — May 3, 2026"

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(url: str, timeout: float = 8.0) -> Optional[bytes]:
    """GET a URL; return raw bytes or None on failure."""
    if _HTTPX:
        try:
            r = httpx.get(url, timeout=timeout, follow_redirects=True)
            r.raise_for_status()
            return r.content
        except Exception as exc:
            print(f"  [httpx] GET {url} failed: {exc}", file=sys.stderr)
    if _REQUESTS:
        try:
            r = _requests.get(url, timeout=timeout)
            r.raise_for_status()
            return r.content
        except Exception as exc:
            print(f"  [requests] GET {url} failed: {exc}", file=sys.stderr)
    return None


def _get_json(url: str, timeout: float = 8.0) -> Optional[Any]:
    raw = _get(url, timeout)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"  JSON parse error for {url}: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# FRED fetchers
# ---------------------------------------------------------------------------

def _fred_via_csv(series_id: str) -> Tuple[Optional[str], Optional[str]]:
    """Fetch latest observation from FRED public CSV (no API key needed).

    Returns (date_str, value_str) or (None, None).
    """
    url = FRED_CSV_BASE + series_id
    raw = _get(url)
    if raw is None:
        return None, None
    try:
        lines = raw.decode("utf-8").strip().splitlines()
        # Header is "DATE,VALUE"; skip it and blank/dot rows
        data_lines = [l for l in lines[1:] if l.strip() and not l.endswith(",") and not l.endswith(".")]
        if not data_lines:
            return None, None
        # Last non-empty line is most recent
        last = data_lines[-1]
        parts = last.split(",")
        if len(parts) < 2 or parts[1].strip() == ".":
            return None, None
        return parts[0].strip(), parts[1].strip()
    except Exception as exc:
        print(f"  CSV parse error for {series_id}: {exc}", file=sys.stderr)
        return None, None


def _fred_via_api(series_id: str, api_key: str) -> Tuple[Optional[str], Optional[str]]:
    """Fetch via FRED JSON API (requires key)."""
    params = f"series_id={series_id}&api_key={api_key}&file_type=json&sort_order=desc&limit=5"
    payload = _get_json(f"{FRED_API_BASE}?{params}")
    if not payload:
        return None, None
    for ob in payload.get("observations", []):
        val = (ob.get("value") or "").strip()
        if val and val != ".":
            return ob.get("date", ""), val
    return None, None


def fetch_macro_rows(sources_log: List[Dict]) -> List[Dict]:
    """Fetch all MACRO_SERIES and return list of macro rows."""
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    rows: List[Dict] = []
    now_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    prev_values: Dict[str, float] = {}  # for delta computation

    for spec in MACRO_SERIES:
        sid = spec["series_id"]
        label = spec["label"]
        unit = spec["unit"]
        date_str, val_str = None, None

        # Try API first if key available
        if api_key:
            date_str, val_str = _fred_via_api(sid, api_key)
            method = "FRED API"
        if not val_str:
            date_str, val_str = _fred_via_csv(sid)
            method = "FRED CSV"

        fetch_status = now_ts if val_str else f"failed: could not retrieve {sid}"
        sources_log.append({
            "name": f"FRED:{sid}",
            "url": FRED_CSV_BASE + sid,
            "fetched_at": fetch_status,
        })

        if not val_str:
            print(f"  WARN: could not fetch {sid}", file=sys.stderr)
            continue

        try:
            num = float(val_str)
        except ValueError:
            num = None

        # Format display value
        if num is not None:
            display = f"{num:.2f}{'%' if unit == '%' else ''}"
        else:
            display = val_str

        # Compute 1-day delta placeholder (FRED CSV gives us latest; full delta
        # would need the prior day's value — we fetch the second-to-last row).
        delta_str = "n/a"
        if num is not None and sid in ("DGS10", "DGS2", "T10Y2Y"):
            # Try to get prev-day from CSV
            url = FRED_CSV_BASE + sid
            raw = _get(url)
            if raw:
                try:
                    lines = raw.decode("utf-8").strip().splitlines()
                    data_lines = [l for l in lines[1:] if l.strip() and "." not in l.split(",")[-1]]
                    if len(data_lines) >= 2:
                        prev_val = float(data_lines[-2].split(",")[1].strip())
                        delta_bps = round((num - prev_val) * 100, 1)
                        sign = "+" if delta_bps >= 0 else ""
                        delta_str = f"{sign}{delta_bps} bps"
                except Exception:
                    pass

        # Build desk context
        context = _macro_context(sid, num)

        rows.append({
            "label": label,
            "series_id": sid,
            "value": display,
            "delta_1d": delta_str,
            "as_of": date_str or TODAY,
            "context": context,
        })

    return rows


def _macro_context(series_id: str, val: Optional[float]) -> str:
    """One-line desk takeaway for a macro series."""
    if val is None:
        return "No data available."
    ctxmap = {
        "DGS10": (
            "Above 4.5%: rate pressure on long-duration equity."
            if val > 4.5
            else "Below 4%: supportive backdrop for risk assets."
            if val < 4.0
            else f"10Y at {val:.2f}% — watch for Fed pivot signals."
        ),
        "DGS2": (
            f"2Y at {val:.2f}% — front-end still pricing restrictive Fed."
            if val > 4.5
            else f"2Y at {val:.2f}% — easing expectations building."
        ),
        "T10Y2Y": (
            "Curve inverted — recession risk elevated; watch credit spreads."
            if val < 0
            else f"Curve +{val:.2f}% — steepening; financials may outperform."
        ),
        "DFF": (
            f"FFR at {val:.2f}% — still in restrictive zone."
            if val > 4.0
            else f"FFR at {val:.2f}% — easing cycle underway."
        ),
        "SOFR": (
            f"SOFR at {val:.2f}% — near FFR; overnight collateral market stable."
        ),
        "VIXCLS": (
            "VIX > 25: elevated vol — buy vol protection, reduce short gamma."
            if val > 25
            else "VIX < 15: low-vol regime — carry strategies favoured."
            if val < 15
            else f"VIX at {val:.1f} — normal vol; balanced risk exposure."
        ),
    }
    return ctxmap.get(series_id, f"Value: {val}")


# ---------------------------------------------------------------------------
# yfinance equity fetchers
# ---------------------------------------------------------------------------

def _yf_download_batch(symbols: List[str]) -> Dict[str, Dict]:
    """Download 5d price history for a list of symbols. Returns dict keyed by symbol."""
    if not _YF:
        return {}
    result: Dict[str, Dict] = {}
    try:
        tickers = yf.Tickers(" ".join(symbols))
        for sym in symbols:
            try:
                t = tickers.tickers[sym]
                hist = t.history(period="5d")
                if hist.empty:
                    continue
                closes = hist["Close"].dropna()
                if len(closes) < 2:
                    continue
                last = float(closes.iloc[-1])
                prev = float(closes.iloc[-2])
                chg_pct = round((last - prev) / prev * 100, 2)
                result[sym] = {"last": last, "change_pct": chg_pct}
            except Exception as exc:
                print(f"  yf error for {sym}: {exc}", file=sys.stderr)
    except Exception as exc:
        print(f"  yf batch error: {exc}", file=sys.stderr)
    return result


def _ytd_pct(symbol: str) -> Optional[float]:
    """Compute YTD % change for a symbol."""
    if not _YF:
        return None
    try:
        t = yf.Ticker(symbol)
        hist = t.history(start="2025-12-31", period="ytd")
        if hist.empty or len(hist) < 2:
            return None
        closes = hist["Close"].dropna()
        first = float(closes.iloc[0])
        last = float(closes.iloc[-1])
        return round((last - first) / first * 100, 1)
    except Exception:
        return None


def fetch_equity_indices(sources_log: List[Dict]) -> List[Dict]:
    """Fetch index data via yfinance."""
    now_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    syms = [i["symbol"] for i in INDEX_SYMBOLS]
    prices = _yf_download_batch(syms)

    sources_log.append({
        "name": "yfinance (indices)",
        "url": "https://finance.yahoo.com",
        "fetched_at": now_ts if prices else "failed: yfinance returned no data",
    })

    rows: List[Dict] = []
    for spec in INDEX_SYMBOLS:
        sym = spec["symbol"]
        info = prices.get(sym)
        if not info:
            continue
        ytd = _ytd_pct(sym)
        rows.append({
            "symbol": sym,
            "name": spec["name"],
            "level": round(info["last"], 2),
            "change_pct": info["change_pct"],
            "ytd_pct": ytd if ytd is not None else "n/a",
        })
    return rows


def fetch_vol_data(sources_log: List[Dict]) -> List[Dict]:
    """Fetch VIX via yfinance."""
    now_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    prices = _yf_download_batch(["^VIX"])

    sources_log.append({
        "name": "yfinance (^VIX)",
        "url": "https://finance.yahoo.com/quote/%5EVIX",
        "fetched_at": now_ts if prices else "failed: yfinance returned no VIX data",
    })

    vix_info = prices.get("^VIX")
    if not vix_info:
        return []

    level = vix_info["last"]
    if level > 30:
        regime = "stressed"
    elif level > 20:
        regime = "elevated"
    elif level > 15:
        regime = "normal"
    else:
        regime = "low"

    return [{
        "symbol": "^VIX",
        "level": round(level, 2),
        "change_pct": vix_info["change_pct"],
        "regime": regime,
    }]


def fetch_sector_movers(sources_log: List[Dict]) -> List[Dict]:
    """Fetch sector ETF data and annotate with driver heuristics."""
    now_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    syms = [e["symbol"] for e in SECTOR_ETFS]
    prices = _yf_download_batch(syms)

    sources_log.append({
        "name": "yfinance (sector ETFs)",
        "url": "https://finance.yahoo.com",
        "fetched_at": now_ts if prices else "failed: yfinance returned no sector data",
    })

    rows: List[Dict] = []
    for spec in SECTOR_ETFS:
        sym = spec["symbol"]
        info = prices.get(sym)
        if not info:
            continue
        chg = info["change_pct"]
        driver = _sector_driver(spec["sector"], chg)
        rows.append({
            "sector": spec["sector"],
            "etf": sym,
            "change_pct": chg,
            "driver": driver,
        })

    # Sort by abs change descending so the biggest movers surface first
    rows.sort(key=lambda r: abs(r["change_pct"]), reverse=True)
    return rows


def _sector_driver(sector: str, chg_pct: float) -> str:
    direction = "rallied" if chg_pct >= 0 else "sold off"
    mag = abs(chg_pct)
    drivers = {
        "Technology":              f"AI/semis {'led gains' if chg_pct >= 0 else 'dragged'} ({chg_pct:+.1f}%); rate sensitivity a headwind." ,
        "Financials":              f"Banks {direction} ({chg_pct:+.1f}%); curve shape and credit-spread moves in focus.",
        "Energy":                  f"Crude oil price action drove energy {direction} ({chg_pct:+.1f}%).",
        "Health Care":             f"Biotech/pharma {direction} ({chg_pct:+.1f}%); FDA calendar and managed-care in focus.",
        "Consumer Discretionary":  f"Consumer spending outlook pushed discretionary {direction} ({chg_pct:+.1f}%).",
        "Consumer Staples":        f"Defensives {'bid' if chg_pct >= 0 else 'underperformed'} ({chg_pct:+.1f}%); risk-off {'rotation' if chg_pct >= 0 else 'outflows'}.",
        "Industrials":             f"PMI/capex tone sent industrials {direction} ({chg_pct:+.1f}%).",
        "Utilities":               f"Rate-sensitive utilities {direction} ({chg_pct:+.1f}%); rate move in focus.",
        "Real Estate":             f"REIT sector {direction} ({chg_pct:+.1f}%); rate sensitivity key.",
        "Materials":               f"Commodity prices and China demand signals drove materials {direction} ({chg_pct:+.1f}%).",
        "Communication Services":  f"Ad spend and streaming sentiment sent comms {direction} ({chg_pct:+.1f}%).",
    }
    return drivers.get(sector, f"{sector} {direction} {chg_pct:+.1f}%.")


# ---------------------------------------------------------------------------
# Headlines
# ---------------------------------------------------------------------------

def _parse_rss_items(raw: bytes, source_name: str, base_url: str) -> List[Dict]:
    """Parse an RSS/Atom feed and return headline dicts."""
    import re
    headlines: List[Dict] = []
    try:
        text = raw.decode("utf-8", errors="replace")
        # Match <item> blocks
        items = re.findall(r"<item>(.*?)</item>", text, re.DOTALL)
        if not items:
            # Atom <entry> fallback
            items = re.findall(r"<entry>(.*?)</entry>", text, re.DOTALL)
        for item in items:
            title_m = re.search(r"<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", item, re.DOTALL)
            link_m  = re.search(r"<link[^>]*>([^<]+)</link>|<link[^>]+href=['\"]([^'\"]+)['\"]", item)
            date_m  = re.search(r"<pubDate>(.*?)</pubDate>|<published>(.*?)</published>|<updated>(.*?)</updated>", item)
            if not title_m:
                continue
            title = title_m.group(1).strip()
            if len(title) < 10:
                continue
            url = ""
            if link_m:
                url = (link_m.group(1) or link_m.group(2) or "").strip()
            pub_date = TODAY
            if date_m:
                raw_date = next(g for g in date_m.groups() if g)
                # Try to parse common RSS date formats
                for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
                    try:
                        dt = datetime.strptime(raw_date.strip(), fmt)
                        pub_date = dt.strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        pass
            headlines.append({
                "title": title,
                "source": source_name,
                "url": url or base_url,
                "published": pub_date,
            })
            if len(headlines) >= 6:
                break
    except Exception as exc:
        print(f"  RSS parse error ({source_name}): {exc}", file=sys.stderr)
    return headlines


def fetch_headlines(sources_log: List[Dict]) -> List[Dict]:
    """Fetch market headlines: yfinance → Yahoo RSS → MarketWatch RSS → CNBC RSS."""
    now_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    headlines: List[Dict] = []

    # ---- 1. yfinance news — try both old and new API shapes ----
    if _YF:
        try:
            spx = yf.Ticker("^GSPC")
            # yfinance >= 0.2.40 wraps news in a different structure
            raw_news = spx.news or []
            for item in raw_news[:12]:
                # New shape: item["content"]["title"], item["content"]["provider"]["displayName"], etc.
                content = item.get("content") or item
                title = (
                    content.get("title")
                    or item.get("title")
                    or ""
                )
                # URL: new shape has canonicalUrl.url or clickThroughUrl.url
                url = (
                    (content.get("canonicalUrl") or {}).get("url")
                    or (content.get("clickThroughUrl") or {}).get("url")
                    or item.get("link")
                    or item.get("url")
                    or "https://finance.yahoo.com"
                )
                # Published: new shape has pubDate as ISO string
                pub_raw = (
                    content.get("pubDate")
                    or item.get("providerPublishTime")
                    or item.get("publishedAt")
                    or 0
                )
                if isinstance(pub_raw, str):
                    try:
                        pub_date = pub_raw[:10]  # "2026-05-03T..."
                    except Exception:
                        pub_date = TODAY
                elif isinstance(pub_raw, (int, float)) and pub_raw:
                    pub_date = datetime.fromtimestamp(pub_raw, tz=timezone.utc).strftime("%Y-%m-%d")
                else:
                    pub_date = TODAY

                # Source name
                provider = content.get("provider") or {}
                source_name = (
                    provider.get("displayName")
                    or item.get("publisher")
                    or (item.get("source") or {}).get("name")
                    or "Yahoo Finance"
                )
                if title and len(title) > 10:
                    headlines.append({
                        "title": title,
                        "source": source_name,
                        "url": url,
                        "published": pub_date,
                    })
                if len(headlines) >= 6:
                    break
            sources_log.append({
                "name": "yfinance news (^GSPC)",
                "url": "https://finance.yahoo.com/quote/%5EGSPC/news",
                "fetched_at": now_ts if headlines else "failed: no news items returned",
            })
        except Exception as exc:
            print(f"  yf news error: {exc}", file=sys.stderr)
            sources_log.append({
                "name": "yfinance news (^GSPC)",
                "url": "https://finance.yahoo.com/quote/%5EGSPC/news",
                "fetched_at": f"failed: {exc}",
            })

    # ---- 2. Yahoo Finance RSS feeds ----
    RSS_FEEDS = [
        ("Yahoo Finance Markets RSS", "https://finance.yahoo.com/news/rssindex", "Yahoo Finance"),
        ("MarketWatch Top Stories RSS", "https://feeds.content.dowjones.io/public/rss/mw_topstories", "MarketWatch"),
        ("CNBC Top News RSS", "https://www.cnbc.com/id/100003114/device/rss/rss.html", "CNBC"),
        ("Seeking Alpha Market News RSS", "https://seekingalpha.com/market_currents.xml", "Seeking Alpha"),
    ]

    for feed_name, feed_url, feed_source in RSS_FEEDS:
        if len(headlines) >= 6:
            break
        raw = _get(feed_url, timeout=8.0)
        if raw:
            new_items = _parse_rss_items(raw, feed_source, feed_url)
            # Deduplicate by title
            for h in new_items:
                if not any(x["title"] == h["title"] for x in headlines):
                    headlines.append(h)
                if len(headlines) >= 6:
                    break
            sources_log.append({
                "name": feed_name,
                "url": feed_url,
                "fetched_at": now_ts if new_items else f"failed: no items parsed from RSS",
            })
        else:
            sources_log.append({
                "name": feed_name,
                "url": feed_url,
                "fetched_at": "failed: HTTP request returned no data",
            })

    return headlines[:6]


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------

def build_summary(
    macro_rows: List[Dict],
    indices: List[Dict],
    vol_rows: List[Dict],
    sectors: List[Dict],
) -> str:
    """Compose a trader-voiced 3-5 sentence narrative from live data."""
    parts: List[str] = []

    # Rate regime
    dgs10 = next((r for r in macro_rows if r["series_id"] == "DGS10"), None)
    dff   = next((r for r in macro_rows if r["series_id"] == "DFF"),   None)
    spread= next((r for r in macro_rows if r["series_id"] == "T10Y2Y"),None)

    rate_str = ""
    if dgs10:
        rate_str = f"10Y Treasuries sit at {dgs10['value']}"
    if dff:
        rate_str += f" against a Fed Funds Rate of {dff['value']}"
    if spread:
        spread_val = spread["value"]
        curve_tone = "curve is inverted — recessionary signal in play" if "-" in spread_val else "curve is mildly positive"
        rate_str += f"; the yield {curve_tone}."
    if rate_str:
        parts.append(rate_str.strip())

    # Vol regime
    if vol_rows:
        v = vol_rows[0]
        lvl = v["level"]
        reg = v["regime"]
        if reg == "low":
            parts.append(f"Implied vol is compressed — VIX at {lvl:.1f} — short-gamma and carry strategies are in favour; tail hedges are cheap.")
        elif reg == "normal":
            parts.append(f"VIX at {lvl:.1f} reflects a balanced vol regime; no strong directional bias in implied vol.")
        elif reg == "elevated":
            parts.append(f"VIX at {lvl:.1f} signals elevated anxiety; risk managers should be trimming short-gamma exposure.")
        else:
            parts.append(f"VIX at {lvl:.1f} — stressed regime; macro hedges warranted and skew is likely rich.")

    # Equity tone
    spx = next((i for i in indices if i["symbol"] == "^GSPC"), None)
    if spx:
        chg = spx["change_pct"]
        lvl = spx["level"]
        tone = "risk-on" if chg > 0 else "risk-off"
        parts.append(
            f"S&P 500 at {lvl:,.0f} ({chg:+.2f}% on the day) reflects a {tone} tape; "
            f"YTD return stands at {spx.get('ytd_pct', 'n/a')}%."
        )

    # Sector dominance
    if sectors:
        top_gainer = max(sectors, key=lambda s: s["change_pct"])
        top_loser  = min(sectors, key=lambda s: s["change_pct"])
        parts.append(
            f"Sector rotation: {top_gainer['sector']} leads ({top_gainer['change_pct']:+.1f}%), "
            f"while {top_loser['sector']} lags ({top_loser['change_pct']:+.1f}%); "
            "watch option flow in the lagging sector for mean-reversion setups."
        )

    return "  ".join(parts) if parts else "Data partially unavailable — check source failures in the sources array."


def build_themes(
    macro_rows: List[Dict],
    vol_rows: List[Dict],
    sectors: List[Dict],
) -> List[str]:
    """Generate 3-6 short desk-watch bullets."""
    themes: List[str] = []

    # Rate theme
    dgs10 = next((r for r in macro_rows if r["series_id"] == "DGS10"), None)
    if dgs10:
        try:
            v = float(dgs10["value"].rstrip("%"))
            if v > 4.5:
                themes.append(f"Rates: 10Y at {v:.2f}% — duration drag persists; watch for NFP/CPI to reprice cuts.")
            elif v < 4.0:
                themes.append(f"Rates: 10Y below 4% — risk-asset tailwind; curve steepening trade viable.")
            else:
                themes.append(f"Rates: 10Y at {v:.2f}% — range-bound; key support/resistance at 4.0/4.5%.")
        except ValueError:
            pass

    # Spread / curve
    spread = next((r for r in macro_rows if r["series_id"] == "T10Y2Y"), None)
    if spread:
        try:
            sv = float(spread["value"].rstrip("%"))
            if sv < 0:
                themes.append("Yield curve inverted — bear-steepener risk; financials NIM headwind.")
            else:
                themes.append(f"Yield curve +{sv:.2f}% — steepening supports bank stocks and reflation trades.")
        except ValueError:
            pass

    # Vol theme
    if vol_rows:
        v = vol_rows[0]
        lvl = v["level"]
        reg = v["regime"]
        if reg in ("low", "normal"):
            themes.append(f"Vol regime: VIX {lvl:.1f} — consider long-dated variance swaps as tail hedge at low premium.")
        else:
            themes.append(f"Vol regime: VIX {lvl:.1f} elevated — reduce short-vol book, neutralise delta.")

    # Sector themes from top movers
    if sectors:
        top = sectors[0]  # biggest mover
        themes.append(f"Sector focus: {top['sector']} ({top['etf']} {top['change_pct']:+.1f}%) — {top['driver']}")

    # Carry / risk-off
    themes.append("Event risk: monitor Fed speak and any surprise macro prints for vol spike triggers.")
    themes.append("Skew: if VIX < 16, downside puts are cheap — accumulate 1M S&P 500 put spreads into strength.")

    return themes[:6]


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_briefing() -> Dict[str, Any]:
    as_of = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"Building briefing as of {as_of} ...")

    sources_log: List[Dict] = []

    # 1. Macro
    print("  Fetching macro (FRED) ...")
    macro_rows = fetch_macro_rows(sources_log)
    print(f"  Got {len(macro_rows)} macro rows.")

    # 2. Equity indices
    print("  Fetching equity indices (yfinance) ...")
    indices = fetch_equity_indices(sources_log)
    print(f"  Got {len(indices)} indices.")

    # 3. VIX
    print("  Fetching VIX (yfinance) ...")
    vol_rows = fetch_vol_data(sources_log)
    print(f"  Got {len(vol_rows)} vol rows.")

    # 4. Sectors
    print("  Fetching sector ETFs (yfinance) ...")
    sectors = fetch_sector_movers(sources_log)
    print(f"  Got {len(sectors)} sector rows.")

    # 5. Headlines
    print("  Fetching headlines ...")
    headlines = fetch_headlines(sources_log)
    print(f"  Got {len(headlines)} headlines.")

    # 6. Synthesis
    summary = build_summary(macro_rows, indices, vol_rows, sectors)
    themes = build_themes(macro_rows, vol_rows, sectors)

    briefing: Dict[str, Any] = {
        "as_of": as_of,
        "title": TITLE,
        "summary": summary,
        "macro": macro_rows,
        "equity": {
            "indices": indices,
            "vol": vol_rows,
            "sector_movers": sectors,
        },
        "headlines": headlines,
        "themes": themes,
        "sources": sources_log,
    }

    return briefing


def main() -> None:
    briefing = build_briefing()

    # Validate JSON round-trip before writing
    json_str = json.dumps(briefing, indent=2, ensure_ascii=False)
    try:
        json.loads(json_str)  # validate
    except json.JSONDecodeError as exc:
        print(f"FATAL: produced invalid JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    OUTPUT_PATH.write_text(json_str, encoding="utf-8")
    print(f"\nWrote {OUTPUT_PATH}")
    print(f"  Macro rows    : {len(briefing['macro'])}")
    print(f"  Index rows    : {len(briefing['equity']['indices'])}")
    print(f"  Vol rows      : {len(briefing['equity']['vol'])}")
    print(f"  Sector rows   : {len(briefing['equity']['sector_movers'])}")
    print(f"  Headlines     : {len(briefing['headlines'])}")
    print(f"  Themes        : {len(briefing['themes'])}")

    # Print any failed sources
    failed = [s for s in briefing["sources"] if "failed" in s.get("fetched_at", "")]
    if failed:
        print("\n  Failed sources:")
        for s in failed:
            print(f"    {s['name']}: {s['fetched_at']}")


if __name__ == "__main__":
    main()
