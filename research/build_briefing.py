"""research/build_briefing.py — Morning Macro & Equity Briefing builder.

Produces research/briefing.json with macro rates, equity indices, sector ETFs,
vol regime, structurer block, trader block, and market headlines.
Runnable standalone:

    python research/build_briefing.py

Idempotent — overwrites the file each run.
No new heavy deps: uses httpx, requests, yfinance (all in requirements.txt).
"""

from __future__ import annotations

import json
import math
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

# VIXCLS removed — VIX single source of truth is yfinance intraday
MACRO_SERIES: List[Dict[str, str]] = [
    {"series_id": "DGS10", "label": "10Y Treasury",   "unit": "%"},
    {"series_id": "DGS2",  "label": "2Y Treasury",    "unit": "%"},
    {"series_id": "T10Y2Y","label": "10Y-2Y Spread",  "unit": "%"},
    {"series_id": "DFF",   "label": "Fed Funds Rate", "unit": "%"},
    {"series_id": "SOFR",  "label": "SOFR",           "unit": "%"},
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

# Vol term structure tickers
VOL_TERM_STRUCTURE: List[Dict[str, str]] = [
    {"tenor": "9D",  "symbol": "^VIX9D"},
    {"tenor": "30D", "symbol": "^VIX"},
    {"tenor": "3M",  "symbol": "^VIX3M"},
    {"tenor": "6M",  "symbol": "^VIX6M"},
]

# Credit proxies
CREDIT_PROXIES: List[Dict[str, str]] = [
    {"symbol": "HYG", "name": "High Yield Bond ETF"},
    {"symbol": "LQD", "name": "Investment Grade Bond ETF"},
]

# Structurer vol indices — ^ICJ delisted, omit implied_correlation
STRUCTURER_VOL_INDICES: List[Dict[str, str]] = [
    {"symbol": "^SKEW",  "key": "skew_index"},
    {"symbol": "^VVIX",  "key": "vvix"},
    {"symbol": "^MOVE",  "key": "move"},
]

# Trader cross-asset — DX=F not found on Yahoo; use DX-Y.NYB (ICE Dollar Index futures)
CROSS_ASSET: List[Dict[str, str]] = [
    {"symbol": "DX-Y.NYB", "name": "Dollar Index"},
    {"symbol": "CL=F",     "name": "WTI Crude"},
    {"symbol": "GC=F",     "name": "Gold"},
    {"symbol": "ZN=F",     "name": "10Y Note Fut"},
    {"symbol": "BTC-USD",  "name": "Bitcoin"},
]

# Overnight futures
OVERNIGHT_FUTURES: List[Dict[str, str]] = [
    {"symbol": "ES=F", "name": "S&P 500 Futures"},
    {"symbol": "NQ=F", "name": "NASDAQ 100 Futures"},
]

# Global overnight indices
GLOBAL_OVERNIGHT: List[Dict[str, str]] = [
    {"symbol": "^N225",  "name": "Nikkei 225"},
    {"symbol": "^HSI",   "name": "Hang Seng"},
    {"symbol": "^GDAXI", "name": "DAX"},
    {"symbol": "^FTSE",  "name": "FTSE 100"},
]

# RV proxy symbols for vol carry
RV_PROXIES: List[Dict[str, str]] = [
    {"symbol": "^GSPC", "name": "S&P 500", "iv_ticker": "^VIX", "iv_mult": 1.0},
    {"symbol": "QQQ",   "name": "QQQ",     "iv_ticker": "^VXN", "iv_mult": 1.15},
    {"symbol": "IWM",   "name": "IWM",     "iv_ticker": "^RVX", "iv_mult": 1.20},
]

_NOW = datetime.now()
TODAY = _NOW.strftime("%Y-%m-%d")
TITLE = f"US Macro & Equity Briefing — {_NOW.strftime('%B')} {_NOW.day}, {_NOW.year}"

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
# FRED fetchers — now returns (date, value, prev_date, prev_value)
# ---------------------------------------------------------------------------

def _fred_via_csv(series_id: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Fetch latest + second-to-last observation from FRED public CSV.

    Returns (date_str, value_str, prev_date_str, prev_value_str).
    """
    url = FRED_CSV_BASE + series_id
    raw = _get(url)
    if raw is None:
        return None, None, None, None
    try:
        lines = raw.decode("utf-8").strip().splitlines()
        # Filter: skip header, skip lines ending with "." (missing) or blank
        data_lines = [
            l for l in lines[1:]
            if l.strip() and not l.strip().endswith(",") and not l.split(",")[-1].strip() == "."
        ]
        if not data_lines:
            return None, None, None, None
        # Latest row
        last_parts = data_lines[-1].split(",")
        if len(last_parts) < 2 or last_parts[1].strip() == ".":
            return None, None, None, None
        date_str = last_parts[0].strip()
        val_str = last_parts[1].strip()

        # Second-to-last row (previous trading day)
        prev_date_str, prev_val_str = None, None
        if len(data_lines) >= 2:
            prev_parts = data_lines[-2].split(",")
            if len(prev_parts) >= 2 and prev_parts[1].strip() != ".":
                prev_date_str = prev_parts[0].strip()
                prev_val_str = prev_parts[1].strip()

        return date_str, val_str, prev_date_str, prev_val_str
    except Exception as exc:
        print(f"  CSV parse error for {series_id}: {exc}", file=sys.stderr)
        return None, None, None, None


def _fred_via_api(series_id: str, api_key: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Fetch via FRED JSON API (requires key). Returns (date, val, prev_date, prev_val)."""
    params = f"series_id={series_id}&api_key={api_key}&file_type=json&sort_order=desc&limit=5"
    payload = _get_json(f"{FRED_API_BASE}?{params}")
    if not payload:
        return None, None, None, None
    observations = [
        ob for ob in payload.get("observations", [])
        if (ob.get("value") or "").strip() not in ("", ".")
    ]
    if not observations:
        return None, None, None, None
    latest = observations[0]
    prev = observations[1] if len(observations) > 1 else None
    return (
        latest.get("date", ""), latest.get("value", "").strip(),
        (prev.get("date", "") if prev else None),
        (prev.get("value", "").strip() if prev else None),
    )


def fetch_macro_rows(sources_log: List[Dict]) -> List[Dict]:
    """Fetch all MACRO_SERIES and return list of macro rows."""
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    rows: List[Dict] = []
    now_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for spec in MACRO_SERIES:
        sid = spec["series_id"]
        label = spec["label"]
        unit = spec["unit"]
        date_str, val_str, prev_date_str, prev_val_str = None, None, None, None

        if api_key:
            date_str, val_str, prev_date_str, prev_val_str = _fred_via_api(sid, api_key)
        if not val_str:
            date_str, val_str, prev_date_str, prev_val_str = _fred_via_csv(sid)

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

        # Compute 1-day delta — ALWAYS attempt for every series
        delta_str = None
        if num is not None and prev_val_str is not None:
            try:
                prev_num = float(prev_val_str)
                if unit == "%":
                    # rates/spreads: report in bps
                    delta_bps = round((num - prev_num) * 100, 1)
                    sign = "+" if delta_bps >= 0 else ""
                    delta_str = f"{sign}{delta_bps} bps"
                else:
                    # index-like values: report as % change
                    pct = round((num - prev_num) / prev_num * 100, 2) if prev_num != 0 else 0.0
                    sign = "+" if pct >= 0 else ""
                    delta_str = f"{sign}{pct:.2f}%"
            except (ValueError, ZeroDivisionError):
                pass

        if delta_str is None:
            # still no delta — report as null (not "n/a")
            delta_str = None

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
    }
    return ctxmap.get(series_id, f"Value: {val}")


# ---------------------------------------------------------------------------
# yfinance helpers — batched download
# ---------------------------------------------------------------------------

def _yf_batch_prices(symbols: List[str], period: str = "5d") -> Dict[str, Dict]:
    """Batched yfinance download. Returns {symbol: {last, change_pct}} or empty dict.

    yfinance always returns a DataFrame for the Close slice regardless of
    how many symbols are requested — columns are the symbol strings.
    """
    if not _YF or not symbols:
        return {}
    result: Dict[str, Dict] = {}
    try:
        raw = yf.download(symbols, period=period, interval="1d",
                          auto_adjust=True, progress=False, threads=True)
        if raw is None or raw.empty:
            return {}

        # raw["Close"] is always a DataFrame; columns are symbol names.
        # When the top-level index is a MultiIndex (older yfinance), we need
        # to drop one level; with current yfinance it is already flat.
        close = raw["Close"]
        if hasattr(close.columns, "nlevels") and close.columns.nlevels > 1:
            close.columns = close.columns.droplevel(0)

        for sym in symbols:
            try:
                if sym not in close.columns:
                    continue
                series = close[sym].dropna()
                if len(series) < 2:
                    continue
                last_val = float(series.iloc[-1])
                prev_val = float(series.iloc[-2])
                chg_pct = round((last_val - prev_val) / prev_val * 100, 2) if prev_val != 0 else 0.0
                result[sym] = {"last": last_val, "change_pct": chg_pct}
            except Exception as exc:
                print(f"  yf batch parse error for {sym}: {exc}", file=sys.stderr)
    except Exception as exc:
        print(f"  yf batch download error: {exc}", file=sys.stderr)
    return result


def _ytd_pct(symbol: str) -> Optional[float]:
    """Compute YTD % change for a symbol using period='ytd'."""
    if not _YF:
        return None
    try:
        t = yf.Ticker(symbol)
        hist = t.history(period="ytd", auto_adjust=True)
        if hist is None or hist.empty:
            return None
        closes = hist["Close"].dropna()
        if len(closes) < 2:
            return None
        first = float(closes.iloc[0])
        last = float(closes.iloc[-1])
        if first == 0:
            return None
        return round((last - first) / first * 100, 1)
    except Exception as exc:
        print(f"  ytd_pct error for {symbol}: {exc}", file=sys.stderr)
        return None


def _compute_rv(symbol: str, window: int = 30) -> Optional[float]:
    """Compute annualised realised vol from daily log returns × sqrt(252) × 100."""
    if not _YF:
        return None
    try:
        t = yf.Ticker(symbol)
        hist = t.history(period="60d", auto_adjust=True)
        if hist is None or hist.empty:
            return None
        closes = hist["Close"].dropna()
        if len(closes) < window + 1:
            return None
        recent = closes.iloc[-(window + 1):]
        import math as _math
        log_rets = [_math.log(float(recent.iloc[i]) / float(recent.iloc[i - 1]))
                    for i in range(1, len(recent))]
        if not log_rets:
            return None
        mean = sum(log_rets) / len(log_rets)
        var = sum((r - mean) ** 2 for r in log_rets) / len(log_rets)
        rv = _math.sqrt(var * 252) * 100
        return round(rv, 1)
    except Exception as exc:
        print(f"  rv error for {symbol}: {exc}", file=sys.stderr)
        return None


def _session_high_low(symbol: str) -> Tuple[Optional[float], Optional[float]]:
    """Return (session_high, session_low) from 5m bars for current session."""
    if not _YF:
        return None, None
    try:
        t = yf.Ticker(symbol)
        hist = t.history(period="1d", interval="5m", auto_adjust=True)
        if hist is None or hist.empty:
            return None, None
        return round(float(hist["High"].max()), 2), round(float(hist["Low"].min()), 2)
    except Exception as exc:
        print(f"  session_high_low error for {symbol}: {exc}", file=sys.stderr)
        return None, None


# ---------------------------------------------------------------------------
# Equity indices
# ---------------------------------------------------------------------------

def fetch_equity_indices(sources_log: List[Dict]) -> List[Dict]:
    now_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    syms = [i["symbol"] for i in INDEX_SYMBOLS]
    prices = _yf_batch_prices(syms)

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
            print(f"  WARN: no price data for {sym}", file=sys.stderr)
            continue
        ytd = _ytd_pct(sym)
        rows.append({
            "symbol": sym,
            "name": spec["name"],
            "level": round(info["last"], 2),
            "change_pct": info["change_pct"],
            "ytd_pct": ytd,  # null if unavailable, not "n/a"
        })
    return rows


# ---------------------------------------------------------------------------
# VIX — single source of truth (yfinance intraday)
# ---------------------------------------------------------------------------

def fetch_vol_data(sources_log: List[Dict]) -> List[Dict]:
    now_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    prices = _yf_batch_prices(["^VIX"])

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


# ---------------------------------------------------------------------------
# Sector movers — fixed flat-template bug
# ---------------------------------------------------------------------------

def fetch_sector_movers(sources_log: List[Dict]) -> List[Dict]:
    now_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    syms = [e["symbol"] for e in SECTOR_ETFS]
    prices = _yf_batch_prices(syms)

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

    rows.sort(key=lambda r: abs(r["change_pct"]), reverse=True)
    return rows


def _sector_driver(sector: str, chg_pct: float) -> str:
    """Generate driver text. Suppresses action verb when |chg| < 0.10 (flat)."""
    mag = abs(chg_pct)
    if mag < 0.10:
        direction_phrase = "flat (—)"
        is_flat = True
    else:
        direction_phrase = "rallied" if chg_pct > 0 else "sold off"
        is_flat = False

    if is_flat:
        drivers = {
            "Technology":              f"AI/semis flat (—) ({chg_pct:+.1f}%); rate sensitivity a headwind.",
            "Financials":              f"Banks flat (—) ({chg_pct:+.1f}%); curve shape and credit-spread moves in focus.",
            "Energy":                  f"Crude oil price action — energy flat (—) ({chg_pct:+.1f}%).",
            "Health Care":             f"Biotech/pharma flat (—) ({chg_pct:+.1f}%); FDA calendar in focus.",
            "Consumer Discretionary":  f"Consumer spending outlook — discretionary flat (—) ({chg_pct:+.1f}%).",
            "Consumer Staples":        f"Defensives flat (—) ({chg_pct:+.1f}%); sideways price action.",
            "Industrials":             f"PMI/capex tone — industrials flat (—) ({chg_pct:+.1f}%).",
            "Utilities":               f"Rate-sensitive utilities flat (—) ({chg_pct:+.1f}%); rate move in focus.",
            "Real Estate":             f"REIT sector flat (—) ({chg_pct:+.1f}%); rate sensitivity key.",
            "Materials":               f"Commodity signals — materials flat (—) ({chg_pct:+.1f}%).",
            "Communication Services":  f"Ad spend/streaming sentiment — comms flat (—) ({chg_pct:+.1f}%).",
        }
    else:
        drivers = {
            "Technology":              f"AI/semis {'led gains' if chg_pct > 0 else 'dragged'} ({chg_pct:+.1f}%); rate sensitivity a headwind.",
            "Financials":              f"Banks {direction_phrase} ({chg_pct:+.1f}%); curve shape and credit-spread moves in focus.",
            "Energy":                  f"Crude oil price action drove energy {direction_phrase} ({chg_pct:+.1f}%).",
            "Health Care":             f"Biotech/pharma {direction_phrase} ({chg_pct:+.1f}%); FDA calendar and managed-care in focus.",
            "Consumer Discretionary":  f"Consumer spending outlook pushed discretionary {direction_phrase} ({chg_pct:+.1f}%).",
            "Consumer Staples":        f"Defensives {'bid' if chg_pct > 0 else 'underperformed'} ({chg_pct:+.1f}%); risk-off {'rotation' if chg_pct > 0 else 'outflows'}.",
            "Industrials":             f"PMI/capex tone sent industrials {direction_phrase} ({chg_pct:+.1f}%).",
            "Utilities":               f"Rate-sensitive utilities {direction_phrase} ({chg_pct:+.1f}%); rate move in focus.",
            "Real Estate":             f"REIT sector {direction_phrase} ({chg_pct:+.1f}%); rate sensitivity key.",
            "Materials":               f"Commodity prices and China demand signals drove materials {direction_phrase} ({chg_pct:+.1f}%).",
            "Communication Services":  f"Ad spend and streaming sentiment sent comms {direction_phrase} ({chg_pct:+.1f}%).",
        }
    return drivers.get(sector, f"{sector} {direction_phrase} {chg_pct:+.1f}%.")


# ---------------------------------------------------------------------------
# Structurer block
# ---------------------------------------------------------------------------

def fetch_structurer_block(vix_level: Optional[float], sources_log: List[Dict]) -> Dict[str, Any]:
    """Build the structurer block: vol term structure, skew, vvix, move, corr, rv-iv, credit."""
    now_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # --- Vol term structure ---
    vol_ts_syms = [v["symbol"] for v in VOL_TERM_STRUCTURE]
    ts_prices = _yf_batch_prices(vol_ts_syms)
    sources_log.append({
        "name": "yfinance (vol term structure: VIX9D/VIX/VIX3M/VIX6M)",
        "url": "https://finance.yahoo.com",
        "fetched_at": now_ts if ts_prices else "failed: yfinance returned no vol term structure data",
    })

    vol_term_rows: List[Dict] = []
    for spec in VOL_TERM_STRUCTURE:
        info = ts_prices.get(spec["symbol"])
        if info:
            vol_term_rows.append({
                "tenor": spec["tenor"],
                "symbol": spec["symbol"],
                "level": round(info["last"], 2),
                "change_pct": info["change_pct"],
            })
        else:
            print(f"  WARN: no data for vol tenor {spec['symbol']}", file=sys.stderr)
            vol_term_rows.append({
                "tenor": spec["tenor"],
                "symbol": spec["symbol"],
                "level": None,
                "change_pct": None,
            })

    # --- Term structure slope: VIX / VIX3M ---
    vix_30d = next((r["level"] for r in vol_term_rows if r["tenor"] == "30D"), None)
    vix_3m  = next((r["level"] for r in vol_term_rows if r["tenor"] == "3M"),  None)

    if vix_30d is not None and vix_3m is not None and vix_3m != 0:
        ratio = round(vix_30d / vix_3m, 3)
        if ratio < 1.0:
            shape = "contango"
            context = (
                f"VIX ({vix_30d:.1f}) < VIX3M ({vix_3m:.1f}), ratio {ratio:.2f} — "
                "contango; short-vol roll-down is attractive, long calendars favored."
            )
        elif ratio > 1.0:
            shape = "backwardation"
            context = (
                f"VIX ({vix_30d:.1f}) > VIX3M ({vix_3m:.1f}), ratio {ratio:.2f} — "
                "backwardation signals near-term stress; prefer long vol, buy dips in skew."
            )
        else:
            shape = "flat"
            context = f"VIX ({vix_30d:.1f}) ≈ VIX3M ({vix_3m:.1f}), ratio {ratio:.2f} — flat term structure; no clear roll bias."
    else:
        ratio = None
        shape = "unknown"
        context = "Vol term structure data incomplete."

    term_structure_slope = {
        "shape": shape,
        "ratio_30d_3m": ratio,
        "context": context,
    }

    # --- Additional vol indices: SKEW, VVIX, MOVE, ICJ ---
    extra_syms = [v["symbol"] for v in STRUCTURER_VOL_INDICES]
    extra_prices = _yf_batch_prices(extra_syms)
    sources_log.append({
        "name": "yfinance (SKEW/VVIX/MOVE/ICJ)",
        "url": "https://finance.yahoo.com",
        "fetched_at": now_ts if extra_prices else "failed: yfinance returned no SKEW/VVIX/MOVE/ICJ data",
    })

    def _vol_idx_obj(sym: str, key: str) -> Dict[str, Any]:
        info = extra_prices.get(sym)
        level = round(info["last"], 2) if info else None
        chg = info["change_pct"] if info else None
        if not info:
            print(f"  WARN: no data for {sym}", file=sys.stderr)
        return {
            "symbol": sym,
            "level": level,
            "change_pct": chg,
            "context": _vol_idx_context(key, level, chg),
        }

    def _vol_idx_context(key: str, level: Optional[float], chg: Optional[float]) -> str:
        if level is None:
            return "No data."
        if key == "skew_index":
            if level > 145:
                return f"SKEW at {level:.1f} — elevated tail-risk pricing; long crash protection is cheap relative to convexity."
            elif level > 130:
                return f"SKEW at {level:.1f} — moderate; OTM put skew within normal range."
            else:
                return f"SKEW at {level:.1f} — low tail-risk pricing; skew-selling strategies look attractive."
        elif key == "vvix":
            if level > 120:
                return f"VVIX at {level:.1f} — very high vol-of-vol; vega is expensive, reduce long vega positions."
            elif level > 90:
                return f"VVIX at {level:.1f} — elevated vol-of-vol; watch for sudden VIX spikes."
            else:
                return f"VVIX at {level:.1f} — calm vol-of-vol; variance swap carry is stable."
        elif key == "move":
            if level > 120:
                return f"MOVE at {level:.1f} — high rates vol; cross-asset vol spill-over risk elevated."
            elif level > 80:
                return f"MOVE at {level:.1f} — rates market moderately volatile; watch duration positioning."
            else:
                return f"MOVE at {level:.1f} — calm rates market; fixed-income carry intact."
        elif key == "implied_correlation":
            if level > 50:
                return f"Implied correlation at {level:.1f} — risk-off; dispersion trades expensive."
            elif level > 25:
                return f"Implied correlation at {level:.1f} — moderate; index-vs-single-stock vol spread balanced."
            else:
                return f"Implied correlation at {level:.1f} — low; dispersion trade (short index, long single-name vol) attractive."
        return f"Level: {level:.1f}"

    skew_obj   = _vol_idx_obj("^SKEW", "skew_index")
    vvix_obj   = _vol_idx_obj("^VVIX", "vvix")
    move_obj   = _vol_idx_obj("^MOVE", "move")
    # ^ICJ is delisted — emit null object
    corr_obj   = {"symbol": "^ICJ", "level": None, "change_pct": None, "context": "^ICJ delisted — no implied correlation data available."}

    # --- Realised vs implied ---
    rv_iv_rows: List[Dict] = []
    vix_iv = vix_level  # use live VIX as SPX IV proxy

    for proxy in RV_PROXIES:
        sym = proxy["symbol"]
        rv_30d = _compute_rv(sym, window=30)
        rv_10d = _compute_rv(sym, window=10)

        # IV proxy
        iv_proxy_val = None
        if proxy["iv_ticker"] == "^VIX":
            iv_proxy_val = vix_iv
        else:
            iv_info = _yf_batch_prices([proxy["iv_ticker"]]).get(proxy["iv_ticker"])
            if iv_info:
                iv_proxy_val = round(iv_info["last"], 2)
            elif vix_iv is not None:
                iv_proxy_val = round(vix_iv * proxy["iv_mult"], 2)

        spread = round(iv_proxy_val - rv_30d, 1) if (iv_proxy_val is not None and rv_30d is not None) else None

        if iv_proxy_val is not None and rv_30d is not None:
            if iv_proxy_val > rv_30d * 1.15:
                signal = "sell vol"
            elif iv_proxy_val < rv_30d * 1.05:
                signal = "buy vol"
            else:
                signal = "neutral"
        else:
            signal = None

        rv_iv_rows.append({
            "symbol": sym,
            "name": proxy["name"],
            "rv_10d": rv_10d,
            "rv_30d": rv_30d,
            "iv_proxy": iv_proxy_val,
            "spread_vol_pts": spread,
            "signal": signal,
        })

    # --- Credit proxies ---
    credit_syms = [c["symbol"] for c in CREDIT_PROXIES]
    credit_prices = _yf_batch_prices(credit_syms)
    sources_log.append({
        "name": "yfinance (credit proxies: HYG/LQD)",
        "url": "https://finance.yahoo.com",
        "fetched_at": now_ts if credit_prices else "failed: yfinance returned no credit proxy data",
    })

    credit_rows: List[Dict] = []
    for spec in CREDIT_PROXIES:
        info = credit_prices.get(spec["symbol"])
        credit_rows.append({
            "symbol": spec["symbol"],
            "name": spec["name"],
            "level": round(info["last"], 2) if info else None,
            "change_pct": info["change_pct"] if info else None,
        })
        if not info:
            print(f"  WARN: no data for credit proxy {spec['symbol']}", file=sys.stderr)

    return {
        "vol_term_structure": vol_term_rows,
        "term_structure_slope": term_structure_slope,
        "skew_index": skew_obj,
        "vvix": vvix_obj,
        "move": move_obj,
        "implied_correlation": corr_obj,
        "realized_vs_implied": rv_iv_rows,
        "credit_proxy": credit_rows,
    }


# ---------------------------------------------------------------------------
# Trader block
# ---------------------------------------------------------------------------

def fetch_trader_block(vix_level: Optional[float], sources_log: List[Dict]) -> Dict[str, Any]:
    """Build trader block: overnight futures, global indices, cross-asset, vol carry."""
    now_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # --- Overnight futures (ES=F, NQ=F) ---
    fut_syms = [f["symbol"] for f in OVERNIGHT_FUTURES]
    fut_prices = _yf_batch_prices(fut_syms)
    sources_log.append({
        "name": "yfinance (overnight futures: ES/NQ)",
        "url": "https://finance.yahoo.com",
        "fetched_at": now_ts if fut_prices else "failed: yfinance returned no futures data",
    })

    overnight_futures_rows: List[Dict] = []
    for spec in OVERNIGHT_FUTURES:
        info = fut_prices.get(spec["symbol"])
        if info:
            hi, lo = _session_high_low(spec["symbol"])
            overnight_futures_rows.append({
                "symbol": spec["symbol"],
                "name": spec["name"],
                "level": round(info["last"], 2),
                "change_pct": info["change_pct"],
                "session_high": hi,
                "session_low": lo,
            })
        else:
            print(f"  WARN: no data for future {spec['symbol']}", file=sys.stderr)
            overnight_futures_rows.append({
                "symbol": spec["symbol"],
                "name": spec["name"],
                "level": None,
                "change_pct": None,
                "session_high": None,
                "session_low": None,
            })

    # --- Global overnight indices ---
    global_syms = [g["symbol"] for g in GLOBAL_OVERNIGHT]
    global_prices = _yf_batch_prices(global_syms)
    sources_log.append({
        "name": "yfinance (global overnight: Nikkei/HSI/DAX/FTSE)",
        "url": "https://finance.yahoo.com",
        "fetched_at": now_ts if global_prices else "failed: yfinance returned no global index data",
    })

    global_rows: List[Dict] = []
    for spec in GLOBAL_OVERNIGHT:
        info = global_prices.get(spec["symbol"])
        global_rows.append({
            "symbol": spec["symbol"],
            "name": spec["name"],
            "change_pct": info["change_pct"] if info else None,
        })
        if not info:
            print(f"  WARN: no data for global index {spec['symbol']}", file=sys.stderr)

    # --- Cross-asset ---
    cross_syms = [c["symbol"] for c in CROSS_ASSET]
    cross_prices = _yf_batch_prices(cross_syms)
    sources_log.append({
        "name": "yfinance (cross-asset: DX/CL/GC/ZN/BTC)",
        "url": "https://finance.yahoo.com",
        "fetched_at": now_ts if cross_prices else "failed: yfinance returned no cross-asset data",
    })

    cross_rows: List[Dict] = []
    for spec in CROSS_ASSET:
        info = cross_prices.get(spec["symbol"])
        cross_rows.append({
            "symbol": spec["symbol"],
            "name": spec["name"],
            "level": round(info["last"], 2) if info else None,
            "change_pct": info["change_pct"] if info else None,
        })
        if not info:
            print(f"  WARN: no data for cross-asset {spec['symbol']}", file=sys.stderr)

    # --- Vol carry (S&P 500 only for trader block — single row) ---
    vol_carry_rows: List[Dict] = []
    spx_rv_10d = _compute_rv("^GSPC", window=10)
    spx_rv_30d = _compute_rv("^GSPC", window=30)
    iv_proxy   = vix_level

    if iv_proxy is not None and spx_rv_30d is not None:
        if iv_proxy > spx_rv_30d * 1.15:
            carry_signal = "sell premium"
        elif iv_proxy < spx_rv_30d * 1.05:
            carry_signal = "buy premium"
        else:
            carry_signal = "neutral"
    else:
        carry_signal = None

    vol_carry_rows.append({
        "symbol": "^GSPC",
        "name": "S&P 500",
        "rv_10d": spx_rv_10d,
        "rv_30d": spx_rv_30d,
        "iv_proxy": iv_proxy,
        "carry_signal": carry_signal,
    })

    return {
        "overnight_futures": overnight_futures_rows,
        "global_overnight": global_rows,
        "cross_asset": cross_rows,
        "vol_carry": vol_carry_rows,
    }


# ---------------------------------------------------------------------------
# Headlines
# ---------------------------------------------------------------------------

def _parse_rss_items(raw: bytes, source_name: str, base_url: str) -> List[Dict]:
    """Parse an RSS/Atom feed and return headline dicts."""
    import re
    headlines: List[Dict] = []
    try:
        text = raw.decode("utf-8", errors="replace")
        items = re.findall(r"<item>(.*?)</item>", text, re.DOTALL)
        if not items:
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

    if _YF:
        try:
            spx = yf.Ticker("^GSPC")
            raw_news = spx.news or []
            for item in raw_news[:12]:
                content = item.get("content") or item
                title = content.get("title") or item.get("title") or ""
                url = (
                    (content.get("canonicalUrl") or {}).get("url")
                    or (content.get("clickThroughUrl") or {}).get("url")
                    or item.get("link")
                    or item.get("url")
                    or "https://finance.yahoo.com"
                )
                pub_raw = (
                    content.get("pubDate")
                    or item.get("providerPublishTime")
                    or item.get("publishedAt")
                    or 0
                )
                if isinstance(pub_raw, str):
                    pub_date = pub_raw[:10]
                elif isinstance(pub_raw, (int, float)) and pub_raw:
                    pub_date = datetime.fromtimestamp(pub_raw, tz=timezone.utc).strftime("%Y-%m-%d")
                else:
                    pub_date = TODAY
                provider = content.get("provider") or {}
                source_name = (
                    provider.get("displayName")
                    or item.get("publisher")
                    or (item.get("source") or {}).get("name")
                    or "Yahoo Finance"
                )
                if title and len(title) > 10:
                    headlines.append({"title": title, "source": source_name, "url": url, "published": pub_date})
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
            for h in new_items:
                if not any(x["title"] == h["title"] for x in headlines):
                    headlines.append(h)
                if len(headlines) >= 6:
                    break
            sources_log.append({
                "name": feed_name,
                "url": feed_url,
                "fetched_at": now_ts if new_items else "failed: no items parsed from RSS",
            })
        else:
            sources_log.append({
                "name": feed_name,
                "url": feed_url,
                "fetched_at": "failed: HTTP request returned no data",
            })

    return headlines[:6]


# ---------------------------------------------------------------------------
# Synthesis — summary now mentions vol term structure, skew, structurer/trader signals
# ---------------------------------------------------------------------------

def build_summary(
    macro_rows: List[Dict],
    indices: List[Dict],
    vol_rows: List[Dict],
    sectors: List[Dict],
    structurer: Dict[str, Any],
    trader: Dict[str, Any],
) -> str:
    parts: List[str] = []

    # Rate regime
    dgs10 = next((r for r in macro_rows if r["series_id"] == "DGS10"), None)
    dff   = next((r for r in macro_rows if r["series_id"] == "DFF"),   None)
    spread = next((r for r in macro_rows if r["series_id"] == "T10Y2Y"), None)

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

    # Vol regime with term structure and skew
    if vol_rows:
        v = vol_rows[0]
        lvl = v["level"]
        reg = v["regime"]
        ts = structurer.get("term_structure_slope", {})
        skew = structurer.get("skew_index", {})
        skew_lvl = skew.get("level")
        ts_shape = ts.get("shape", "unknown")
        ts_ratio = ts.get("ratio_30d_3m")

        vol_sent = ""
        if reg == "low":
            vol_sent = f"Implied vol compressed — VIX at {lvl:.1f} (regime: low)."
        elif reg == "normal":
            vol_sent = f"VIX at {lvl:.1f} (regime: normal)."
        elif reg == "elevated":
            vol_sent = f"VIX at {lvl:.1f} signals elevated anxiety (regime: elevated)."
        else:
            vol_sent = f"VIX at {lvl:.1f} — stressed regime."

        if ts_ratio is not None:
            vol_sent += f" Term structure is {ts_shape} (30D/3M ratio {ts_ratio:.2f})."
        if skew_lvl is not None:
            skew_desc = "elevated" if skew_lvl > 140 else "moderate" if skew_lvl > 125 else "low"
            vol_sent += f" SKEW at {skew_lvl:.1f} ({skew_desc} tail-risk pricing)."
        parts.append(vol_sent)

    # Equity tone
    spx = next((i for i in indices if i["symbol"] == "^GSPC"), None)
    if spx:
        chg = spx["change_pct"]
        lvl = spx["level"]
        tone = "risk-on" if chg > 0 else "risk-off"
        ytd = spx.get("ytd_pct")
        ytd_str = f"{ytd:+.1f}%" if ytd is not None else "n/a"
        parts.append(
            f"S&P 500 at {lvl:,.0f} ({chg:+.2f}% on the day) reflects a {tone} tape; "
            f"YTD return stands at {ytd_str}."
        )

    # Structurer signal: vol carry
    rv_iv = structurer.get("realized_vs_implied", [])
    spx_rv = next((r for r in rv_iv if r["symbol"] == "^GSPC"), None)
    if spx_rv and spx_rv.get("signal"):
        rv30 = spx_rv.get("rv_30d")
        iv_p = spx_rv.get("iv_proxy")
        signal = spx_rv["signal"]
        if rv30 is not None and iv_p is not None:
            parts.append(
                f"Structurer signal: IV ({iv_p:.1f}) vs RV-30D ({rv30:.1f}) — {signal}."
            )

    # Trader signal: top mover
    if sectors:
        top_gainer = max(sectors, key=lambda s: s["change_pct"])
        top_loser  = min(sectors, key=lambda s: s["change_pct"])
        parts.append(
            f"Sector rotation: {top_gainer['sector']} leads ({top_gainer['change_pct']:+.1f}%), "
            f"while {top_loser['sector']} lags ({top_loser['change_pct']:+.1f}%)."
        )

    return "  ".join(parts) if parts else "Data partially unavailable — check source failures in the sources array."


# ---------------------------------------------------------------------------
# Themes — dynamically generated from actual live data
# ---------------------------------------------------------------------------

def build_themes(
    macro_rows: List[Dict],
    vol_rows: List[Dict],
    sectors: List[Dict],
    structurer: Dict[str, Any],
) -> List[str]:
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

    # Curve theme
    spread = next((r for r in macro_rows if r["series_id"] == "T10Y2Y"), None)
    if spread:
        try:
            sv = float(spread["value"].rstrip("%"))
            if sv < 0:
                themes.append("Yield curve inverted — bear-steepener risk; financials NIM headwind.")
            elif sv > 0.5:
                themes.append(f"Yield curve +{sv:.2f}% — steepening supports bank stocks and reflation trades.")
            else:
                themes.append(f"Yield curve +{sv:.2f}% — mildly positive; monitor for further steepening catalysts.")
        except ValueError:
            pass

    # VIX-level-dependent themes
    if vol_rows:
        lvl = vol_rows[0]["level"]
        reg = vol_rows[0]["regime"]
        if lvl < 15:
            themes.append(f"VIX < 15 ({lvl:.1f}) — low-vol regime; premium-selling carry favorable, downside puts cheap.")
        elif reg in ("low", "normal"):
            themes.append(f"Vol regime: VIX {lvl:.1f} — consider long-dated variance swaps as tail hedge at low premium.")
        else:
            themes.append(f"Vol regime: VIX {lvl:.1f} elevated — reduce short-vol book, neutralise delta.")

    # Term structure theme
    ts = structurer.get("term_structure_slope", {})
    ts_shape = ts.get("shape")
    ts_ratio = ts.get("ratio_30d_3m")
    if ts_shape == "contango" and ts_ratio is not None and ts_ratio < 0.95:
        themes.append(f"Vol term structure contango deep (ratio {ts_ratio:.2f}) — short-vol roll-down attractive; sell 1M vs buy 3M vol.")

    # SKEW theme
    skew = structurer.get("skew_index", {})
    skew_lvl = skew.get("level")
    if skew_lvl is not None:
        if skew_lvl > 145:
            themes.append(f"SKEW at {skew_lvl:.1f} — elevated tail-risk pricing; long crash protection cheaper than meme vol suggests.")
        elif skew_lvl < 120:
            themes.append(f"SKEW at {skew_lvl:.1f} — low tail-risk premium; skew-selling strategies attractive, watch gamma risk.")

    # Top sector theme
    if sectors:
        top = sectors[0]
        themes.append(f"Sector focus: {top['sector']} ({top['etf']} {top['change_pct']:+.1f}%) — {top['driver']}")

    return themes[:6]


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_briefing() -> Dict[str, Any]:
    as_of = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"Building briefing as of {as_of} ...")

    sources_log: List[Dict] = []

    # 1. Macro (FRED CSV, with prev-day delta)
    print("  Fetching macro (FRED) ...")
    macro_rows = fetch_macro_rows(sources_log)
    print(f"  Got {len(macro_rows)} macro rows.")

    # 2. Equity indices
    print("  Fetching equity indices (yfinance) ...")
    indices = fetch_equity_indices(sources_log)
    print(f"  Got {len(indices)} indices.")

    # 3. VIX — single source of truth
    print("  Fetching VIX (yfinance) ...")
    vol_rows = fetch_vol_data(sources_log)
    print(f"  Got {len(vol_rows)} vol rows.")

    vix_level = vol_rows[0]["level"] if vol_rows else None

    # 4. Sectors
    print("  Fetching sector ETFs (yfinance) ...")
    sectors = fetch_sector_movers(sources_log)
    print(f"  Got {len(sectors)} sector rows.")

    # 5. Structurer block
    print("  Building structurer block (yfinance) ...")
    structurer = fetch_structurer_block(vix_level, sources_log)

    # 6. Trader block
    print("  Building trader block (yfinance) ...")
    trader = fetch_trader_block(vix_level, sources_log)

    # 7. Headlines
    print("  Fetching headlines ...")
    headlines = fetch_headlines(sources_log)
    print(f"  Got {len(headlines)} headlines.")

    # 8. Synthesis
    summary = build_summary(macro_rows, indices, vol_rows, sectors, structurer, trader)
    themes = build_themes(macro_rows, vol_rows, sectors, structurer)

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
        "structurer": structurer,
        "trader": trader,
        "headlines": headlines,
        "themes": themes,
        "sources": sources_log,
    }

    return briefing


def main() -> None:
    briefing = build_briefing()

    json_str = json.dumps(briefing, indent=2, ensure_ascii=False)
    try:
        json.loads(json_str)
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
    vts = briefing["structurer"].get("vol_term_structure", [])
    print(f"  Vol term structure tickers: {len(vts)}")
    ta = briefing["trader"]
    print(f"  Trader cross-asset tickers: {len(ta.get('cross_asset', []))}")

    failed = [s for s in briefing["sources"] if "failed" in s.get("fetched_at", "")]
    if failed:
        print("\n  Failed sources:")
        for s in failed:
            print(f"    {s['name']}: {s['fetched_at']}")


if __name__ == "__main__":
    main()
