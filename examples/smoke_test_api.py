"""End-to-end API smoke test against a running uvicorn instance.

Hits POST /api/price once per product type (european/american call/put,
knockout call/put) and prints the resulting price + Greeks. Sanity-checks
that all six product paths through the router are reachable from the API
layer with no exceptions.
"""

import json
import urllib.request

URL = "http://127.0.0.1:8001/api/price"

CASES = [
    {
        "label": "European call",
        "payload": {
            "option_type": "european_call",
            "underlying": "SPY",
            "spot_price": 100.0, "strike_price": 100.0,
            "days_to_expiration": 180,
            "risk_free_rate": 0.05, "volatility": 0.20,
            "dividend_yield": 0.02,
        },
    },
    {
        "label": "European put",
        "payload": {
            "option_type": "european_put",
            "underlying": "SPY",
            "spot_price": 100.0, "strike_price": 100.0,
            "days_to_expiration": 180,
            "risk_free_rate": 0.05, "volatility": 0.20,
            "dividend_yield": 0.02,
        },
    },
    {
        "label": "American call",
        "payload": {
            "option_type": "american_call",
            "underlying": "SPY",
            "spot_price": 100.0, "strike_price": 100.0,
            "days_to_expiration": 180,
            "risk_free_rate": 0.05, "volatility": 0.20,
            "dividend_yield": 0.02,
        },
    },
    {
        "label": "American put",
        "payload": {
            "option_type": "american_put",
            "underlying": "SPY",
            "spot_price": 100.0, "strike_price": 100.0,
            "days_to_expiration": 180,
            "risk_free_rate": 0.05, "volatility": 0.20,
            "dividend_yield": 0.02,
        },
    },
    {
        "label": "Knockout call (down-and-out, B=80)",
        "payload": {
            "option_type": "knockout_call",
            "underlying": "SPY",
            "spot_price": 100.0, "strike_price": 100.0,
            "days_to_expiration": 180,
            "risk_free_rate": 0.05, "volatility": 0.20,
            "dividend_yield": 0.02,
            "barrier_level": 80.0, "barrier_type": "down_and_out",
        },
    },
    {
        "label": "Knockout put (up-and-out, B=120)",
        "payload": {
            "option_type": "knockout_put",
            "underlying": "SPY",
            "spot_price": 100.0, "strike_price": 100.0,
            "days_to_expiration": 180,
            "risk_free_rate": 0.05, "volatility": 0.20,
            "dividend_yield": 0.02,
            "barrier_level": 120.0, "barrier_type": "up_and_out",
        },
    },
]


def call(payload):
    req = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def main():
    print(f"{'Product':<40} {'Price':>10} {'Delta':>8} {'Gamma':>10} {'Method':<25}")
    print("-" * 100)
    for case in CASES:
        try:
            r = call(case["payload"])
            g = r.get("greeks", {})
            print(f"{case['label']:<40} {r['price']:>10.4f} "
                  f"{g.get('delta', 0):>8.4f} {g.get('gamma', 0):>10.6f} "
                  f"{r.get('method', '?'):<25}")
        except Exception as e:
            print(f"{case['label']:<40} FAILED: {e}")


if __name__ == "__main__":
    main()
