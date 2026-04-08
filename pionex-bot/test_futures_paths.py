"""Probe for correct futures API paths."""
from client import PionexClient, PionexAPIError
from config import Config

cfg = Config()
client = PionexClient(cfg)

# Try various possible futures endpoint patterns
test_paths = [
    # Order endpoints
    ("GET", "/api/v1/future/openOrders", {"symbol": "BTC_USDT_PERP"}),
    ("GET", "/api/v1/futures/openOrders", {"symbol": "BTC_USDT_PERP"}),
    ("GET", "/api/v1/future/trade/openOrders", {"symbol": "BTC_USDT_PERP"}),
    ("GET", "/api/v1/contract/openOrders", {"symbol": "BTC_USDT_PERP"}),
    ("GET", "/api/v1/swap/openOrders", {"symbol": "BTC_USDT_PERP"}),

    # Account / Position endpoints
    ("GET", "/api/v1/future/account/balance", {}),
    ("GET", "/api/v1/future/balance", {}),
    ("GET", "/api/v1/future/positions", {"symbol": "BTC_USDT_PERP"}),
    ("GET", "/api/v1/future/position", {"symbol": "BTC_USDT_PERP"}),
    ("GET", "/api/v1/futures/positions", {"symbol": "BTC_USDT_PERP"}),
    ("GET", "/api/v1/contract/positions", {"symbol": "BTC_USDT_PERP"}),

    # Leverage
    ("GET", "/api/v1/future/leverage", {"symbol": "BTC_USDT_PERP"}),
    ("GET", "/api/v1/futures/leverage", {"symbol": "BTC_USDT_PERP"}),
    ("GET", "/api/v1/contract/leverage", {"symbol": "BTC_USDT_PERP"}),

    # Funding
    ("GET", "/api/v1/future/fundingFee", {"symbol": "BTC_USDT_PERP"}),
    ("GET", "/api/v1/futures/fundingRate", {"symbol": "BTC_USDT_PERP"}),
    ("GET", "/api/v1/market/fundingRate", {"symbol": "BTC_USDT_PERP"}),

    # V2 patterns
    ("GET", "/api/v2/trade/openOrders", {"symbol": "BTC_USDT_PERP"}),
    ("GET", "/api/v2/future/openOrders", {"symbol": "BTC_USDT_PERP"}),
    ("GET", "/api/v2/account/balances", {}),
]

print("Probing futures API paths...\n")
for method, path, params in test_paths:
    try:
        data = client._request(method, path, params=dict(params), signed=True)
        print(f"  OK  {method} {path}")
        if isinstance(data, dict):
            keys = list(data.keys())[:5]
            print(f"      keys: {keys}")
        elif isinstance(data, list):
            print(f"      list items: {len(data)}")
    except PionexAPIError as e:
        status = "!!!" if e.code != "UNKNOWN" else "---"
        if e.code != "UNKNOWN":
            print(f"  {status} {method} {path} => {e.code}: {e.message}")
        # Skip UNKNOWN (404-like) silently
    except Exception as e:
        pass

client.close()
print("\nDone.")
