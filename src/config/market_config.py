"""Configuration for Yahoo Finance market data fetching."""

import os
from typing import Dict

# Default values
MARKET_DATA_CACHE_TTL = 3600  # 1 hour in seconds
MARKET_DATA_TIMEOUT = 10  # seconds
MARKET_DATA_MAX_RETRIES = 3
MARKET_DATA_RETRY_BACKOFF = 2  # exponential backoff multiplier: 2s, 4s, 8s

# Override from environment variables if set
MARKET_DATA_CACHE_TTL = int(os.getenv("MARKET_DATA_CACHE_TTL", MARKET_DATA_CACHE_TTL))
MARKET_DATA_TIMEOUT = int(os.getenv("MARKET_DATA_TIMEOUT", MARKET_DATA_TIMEOUT))
MARKET_DATA_MAX_RETRIES = int(os.getenv("MARKET_DATA_MAX_RETRIES", MARKET_DATA_MAX_RETRIES))
MARKET_DATA_RETRY_BACKOFF = int(os.getenv("MARKET_DATA_RETRY_BACKOFF", MARKET_DATA_RETRY_BACKOFF))


def get_config() -> Dict[str, int]:
    """Get current market data configuration.

    Returns:
        Dict with current configuration values
    """
    return {
        "cache_ttl_seconds": MARKET_DATA_CACHE_TTL,
        "timeout_seconds": MARKET_DATA_TIMEOUT,
        "max_retries": MARKET_DATA_MAX_RETRIES,
        "retry_backoff_multiplier": MARKET_DATA_RETRY_BACKOFF,
    }


def print_config() -> None:
    """Print current configuration (useful for debugging)."""
    config = get_config()
    print("Market Data Configuration:")
    print(f"  Cache TTL: {config['cache_ttl_seconds']}s")
    print(f"  Timeout: {config['timeout_seconds']}s")
    print(f"  Max Retries: {config['max_retries']}")
    print(f"  Retry Backoff: {config['retry_backoff_multiplier']}x")
