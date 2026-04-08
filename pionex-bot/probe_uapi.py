"""Probe /uapi/v1/ futures endpoints on Pionex."""
from client import PionexClient, PionexAPIError
from config import Config

cfg = Config()
client = PionexClient(cfg)

# Now we know futures uses /uapi/v1/ prefix
test_paths = [
    # Account & Positions
    ("GET", "/uapi/v1/account/positions", {"symbol": "BTC_USDT_PERP"}, "Get Active Positions"),
    ("GET", "/uapi/v1/account/positions", {}, "Get All Positions"),
    ("GET", "/uapi/v1/account/balance", {}, "Futures Balance"),
    ("GET", "/uapi/v1/account/balances", {}, "Futures Balances"),
    ("GET", "/uapi/v1/account/detail", {}, "Account Detail"),
    ("GET", "/uapi/v1/account/historicalPositions", {"symbol": "BTC_USDT_PERP"}, "Historical Positions"),

    # Leverage
    ("GET", "/uapi/v1/account/leverage", {"symbol": "BTC_USDT_PERP"}, "Get Leverage"),
    ("GET", "/uapi/v1/trade/leverage", {"symbol": "BTC_USDT_PERP"}, "Get Leverage v2"),

    # Position Mode
    ("GET", "/uapi/v1/account/positionMode", {}, "Get Position Mode"),

    # Margin
    ("GET", "/uapi/v1/account/marginType", {"symbol": "BTC_USDT_PERP"}, "Get Margin Type"),

    # Orders
    ("GET", "/uapi/v1/trade/openOrders", {"symbol": "BTC_USDT_PERP"}, "Open Orders"),
    ("GET", "/uapi/v1/trade/allOrders", {"symbol": "BTC_USDT_PERP"}, "All Orders"),

    # Funding
    ("GET", "/uapi/v1/account/fundingFee", {"symbol": "BTC_USDT_PERP"}, "Get Funding Fee"),
    ("GET", "/uapi/v1/market/fundingRate", {"symbol": "BTC_USDT_PERP"}, "Funding Rate"),
]

print("Probing /uapi/v1/ futures endpoints...\n")
for method, path, params, name in test_paths:
    try:
        data = client._request(method, path, params=dict(params), signed=True)
        print(f"  [OK] {name}")
        print(f"       {method} {path}")
        if isinstance(data, dict):
            for k, v in list(data.items())[:5]:
                val = str(v)[:80]
                print(f"       {k}: {val}")
        elif isinstance(data, list):
            print(f"       items: {len(data)}")
            if data:
                print(f"       sample keys: {list(data[0].keys())[:8]}")
        print()
    except PionexAPIError as e:
        if e.code != "UNKNOWN":
            print(f"  [!!] {name}: {e.code} - {e.message}")
            print(f"       {method} {path}")
            print()

client.close()
print("Done.")
