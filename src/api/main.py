"""FastAPI application for derivatives pricing."""

import json
import logging
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

from .models import PricingRequest, PricingResult, ErrorResponse
from .handlers import price_option
from .market_data import (
    get_spot_price,
    get_dividend_yield,
    get_risk_free_rate,
    get_historical_volatility,
    get_dividend_info,
)
from ..data.movers import get_movers_payload
from .agent_router import router as agent_router
from .esmm_router import router as esmm_router

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Custom CORS middleware - more reliable than CORSMiddleware
class CustomCORSMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Get the origin from the request
        origin = request.headers.get("origin")

        # For preflight requests, return CORS headers immediately
        if request.method == "OPTIONS":
            return Response(
                status_code=200,
                headers={
                    "Access-Control-Allow-Origin": origin or "*",
                    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS, PATCH",
                    "Access-Control-Allow-Headers": "Content-Type, Authorization",
                    "Access-Control-Max-Age": "3600",
                },
            )

        # For regular requests, add CORS headers to response
        response = await call_next(request)
        response.headers["Access-Control-Allow-Origin"] = origin or "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return response


# Create FastAPI app
app = FastAPI(
    title="Derivatives Pricing API",
    description="REST API for pricing derivatives (options, barriers, etc.)",
    version="1.0.0",
)

# Add custom CORS middleware FIRST (before routes)
app.add_middleware(CustomCORSMiddleware)

# Mount the multi-agent structuring co-pilot router under /api/agent/*
app.include_router(agent_router)
app.include_router(esmm_router)


@app.on_event("startup")
async def _init_market_intelligence() -> None:
    """Initialise the RAG layer once at process start.

    Resolves the singleton (no-op when MARKET_INTEL_ENABLED=0 or when heavy
    deps are missing) and exposes it on `app.state.market_intel`. Agents
    read the same singleton via `get_market_intelligence()` so this is also
    available off the request — `app.state` is just a convenient hook for
    request handlers that want the reference.
    """
    try:
        from src.agents.market_intelligence import get_market_intelligence
    except Exception as exc:  # noqa: BLE001
        logger.info("MarketIntelligence module not importable: %s", exc)
        app.state.market_intel = None
        return

    mi = get_market_intelligence()
    app.state.market_intel = mi
    if mi is None:
        logger.info("MarketIntelligence is OFF (MARKET_INTEL_ENABLED=0 or unavailable).")
    else:
        try:
            count = mi.count()
        except Exception:  # noqa: BLE001
            count = "?"
        logger.info("MarketIntelligence ON — corpus has %s documents.", count)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/api/market/spot-price")
async def fetch_spot_price(ticker: str):
    """
    Fetch current spot price for a ticker.

    Args:
        ticker: Stock ticker symbol (e.g., 'SPY', 'QQQ')

    Returns:
        JSON with spot_price and ticker
    """
    logger.info(f"Fetching spot price for {ticker}")
    price = get_spot_price(ticker.upper())

    if price is None:
        raise HTTPException(
            status_code=400,
            detail=f"Could not fetch spot price for ticker {ticker}. Please enter manually.",
        )

    return {"ticker": ticker.upper(), "spot_price": price}


@app.get("/api/market/dividend-yield")
async def fetch_dividend_yield(ticker: str):
    """
    Fetch dividend yield for a ticker.

    Args:
        ticker: Stock ticker symbol

    Returns:
        JSON with dividend_yield and ticker
    """
    logger.info(f"Fetching dividend yield for {ticker}")
    yield_val = get_dividend_yield(ticker.upper())
    return {"ticker": ticker.upper(), "dividend_yield": yield_val}


@app.get("/api/market/risk-free-rate")
async def fetch_risk_free_rate(days_to_expiration: int):
    """
    Fetch current US Treasury yield matching option expiration.

    Args:
        days_to_expiration: Days until option expires

    Returns:
        JSON with risk_free_rate and days_to_expiration
    """
    logger.info(f"Fetching risk-free rate for {days_to_expiration} days")
    rate = get_risk_free_rate(days_to_expiration)
    return {"days_to_expiration": days_to_expiration, "risk_free_rate": rate}


@app.get("/api/market/historical-volatility")
async def fetch_historical_volatility(ticker: str, lookback_days: int = 252):
    """
    Calculate historical volatility from price data.

    Args:
        ticker: Stock ticker symbol
        lookback_days: Number of days to look back (default 252 = 1 year)

    Returns:
        JSON with volatility and ticker
    """
    logger.info(f"Calculating historical volatility for {ticker}")
    volatility = get_historical_volatility(ticker.upper(), lookback_days)

    if volatility is None:
        raise HTTPException(
            status_code=400,
            detail=f"Could not calculate volatility for {ticker}",
        )

    return {
        "ticker": ticker.upper(),
        "volatility": volatility,
        "lookback_days": lookback_days,
    }


@app.get("/api/market/dividend-info")
async def fetch_dividend_info(ticker: str):
    """
    Fetch dividend information for a stock.

    Args:
        ticker: Stock ticker symbol

    Returns:
        JSON with next_dividend_date and next_dividend_amount
    """
    logger.info(f"Fetching dividend info for {ticker}")
    info = get_dividend_info(ticker.upper())
    return {"ticker": ticker.upper(), **info}


@app.get("/api/market/movers")
async def fetch_movers(universe: str = "default"):
    """
    Fetch top gainers, losers, most-volatile, and major indices for the
    Vol Desk dashboard.

    Returns a snapshot payload (cached 60s) with indices and ranked stock lists.
    """
    logger.info(f"Fetching movers payload (universe={universe})")
    try:
        payload = get_movers_payload(universe=universe)
        return payload
    except Exception as e:
        logger.error(f"Failed to build movers payload: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not fetch movers")


_BRIEFING_PATH = Path(__file__).parent.parent.parent / "research" / "briefing.json"


@app.get("/api/briefing")
async def get_briefing():
    """
    Return the latest macro/equity briefing produced by the research agent.

    Reads ``research/briefing.json`` from the repo root on every call (no
    caching — the file is regenerated at most once per morning).  Returns
    HTTP 503 when the file does not yet exist.
    """
    if not _BRIEFING_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail="briefing not yet generated",
        )
    try:
        return json.loads(_BRIEFING_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("Failed to read briefing.json: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Could not read briefing file")


@app.post("/api/price", response_model=PricingResult)
async def price(request: PricingRequest) -> PricingResult:
    """
    Price an option given market and strike parameters.

    Returns option price, Greeks, and HTML report.
    """
    try:
        logger.info(f"Pricing request: {request.option_type} on {request.underlying}")
        result = price_option(request)
        logger.info(f"Pricing complete: ${result.price:.4f}")
        return result

    except ValueError as e:
        logger.error(f"Validation error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "message": "Derivatives Pricing API",
        "version": "1.0.0",
        "endpoints": {
            "health": "/health",
            "pricing": "POST /api/price",
        },
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
