"""Unit tests for market data fetching with retry and caching."""

import time
import unittest
from src.data.market_data import (
    MarketDataCache,
    fetch_market_params,
)


class TestMarketDataCache(unittest.TestCase):
    """Test MarketDataCache functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.cache = MarketDataCache(ttl_seconds=1)  # 1 second TTL for testing

    def test_cache_set_and_get(self):
        """Test basic cache set and get."""
        data = {"spot_price": 100.0, "volatility_30d": 0.15}
        self.cache.set("test_key", data)

        retrieved = self.cache.get("test_key")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["spot_price"], 100.0)

    def test_cache_expiration(self):
        """Test cache expires after TTL."""
        data = {"spot_price": 100.0}
        self.cache.set("test_key", data)

        # Data should be available immediately
        self.assertIsNotNone(self.cache.get("test_key"))

        # Wait for TTL to expire
        time.sleep(1.1)

        # Data should be expired
        self.assertIsNone(self.cache.get("test_key"))

    def test_cache_miss(self):
        """Test cache returns None for missing keys."""
        self.assertIsNone(self.cache.get("nonexistent"))

    def test_cache_clear(self):
        """Test cache clear."""
        self.cache.set("key1", {"data": 1})
        self.cache.set("key2", {"data": 2})

        self.assertIsNotNone(self.cache.get("key1"))
        self.assertIsNotNone(self.cache.get("key2"))

        self.cache.clear()

        self.assertIsNone(self.cache.get("key1"))
        self.assertIsNone(self.cache.get("key2"))


class TestFetchMarketParams(unittest.TestCase):
    """Test fetch_market_params function."""

    def test_fetch_disabled(self):
        """Test fetch_market_params returns empty dict when auto_fetch=False."""
        result = fetch_market_params("SPY", auto_fetch=False)

        self.assertIsNotNone(result)
        self.assertIsNone(result["spot_price"])
        self.assertIsNone(result["dividend_yield"])
        self.assertIsNone(result["volatility_30d"])
        self.assertIsNone(result["volatility_90d"])
        self.assertEqual(result["source"], "fallback")

    def test_fetch_structure(self):
        """Test fetch_market_params returns correct structure."""
        result = fetch_market_params("SPY", auto_fetch=False)

        required_keys = [
            "spot_price",
            "dividend_yield",
            "volatility_30d",
            "volatility_90d",
            "source"
        ]
        for key in required_keys:
            self.assertIn(key, result, f"Missing required key: {key}")

    def test_fetch_source_field(self):
        """Test fetch_market_params includes source field."""
        result = fetch_market_params("SPY", auto_fetch=False)
        self.assertIn("source", result)
        self.assertIn(result["source"], ["api", "cache", "fallback"])


class TestMarketDataIntegration(unittest.TestCase):
    """Integration tests for market data fetching."""

    def test_fetch_returns_correct_structure(self):
        """Test fetch_market_params returns correct data structure."""
        result = fetch_market_params("SPY", auto_fetch=False)

        required_keys = ["spot_price", "dividend_yield", "volatility_30d", "volatility_90d", "source"]
        for key in required_keys:
            self.assertIn(key, result, f"Missing required key: {key}")

    def test_fetch_with_zero_timeout(self):
        """Test fetch accepts zero timeout."""
        result = fetch_market_params("SPY", auto_fetch=False, timeout=0)
        self.assertIsNotNone(result)

    def test_fetch_with_zero_retries(self):
        """Test fetch accepts zero retries."""
        result = fetch_market_params("SPY", auto_fetch=False, max_retries=0)
        self.assertIsNotNone(result)

    def test_fetch_all_none_values(self):
        """Test fetch with auto_fetch=False returns all None market values."""
        result = fetch_market_params("SPY", auto_fetch=False)

        self.assertIsNone(result["spot_price"])
        self.assertIsNone(result["dividend_yield"])
        self.assertIsNone(result["volatility_30d"])
        self.assertIsNone(result["volatility_90d"])


if __name__ == "__main__":
    unittest.main()
