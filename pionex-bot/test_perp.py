"""Test PERP/futures trading capabilities on Pionex."""
from client import PionexClient, PionexAPIError
from config import Config

cfg = Config()
client = PionexClient(cfg)

# 1. Check BTC_USDT_PERP symbol details
print("=== 1. BTC_USDT_PERP Symbol Info ===")
try:
    symbols = client.get_symbols(symbol="BTC_USDT_PERP")
    if symbols:
        s = symbols[0]
        for k, v in s.items():
            print(f"  {k}: {v}")
    else:
        print("  Symbol not found!")
except Exception as e:
    print(f"  Error: {e}")

# 2. Get PERP ticker price
print()
print("=== 2. BTC_USDT_PERP Ticker ===")
try:
    ticker = client.get_ticker("BTC_USDT_PERP")
    if ticker:
        for k, v in ticker.items():
            print(f"  {k}: {v}")
    else:
        print("  No ticker data")
except Exception as e:
    print(f"  Error: {e}")

# 3. Get PERP klines
print()
print("=== 3. BTC_USDT_PERP Klines (last 3) ===")
try:
    klines = client.get_klines("BTC_USDT_PERP", "15M", limit=3)
    for k in klines:
        print(f"  O={k['open']} H={k['high']} L={k['low']} C={k['close']} V={k['volume']}")
except Exception as e:
    print(f"  Error: {e}")

# 4. Get PERP book ticker (bid/ask)
print()
print("=== 4. BTC_USDT_PERP Book Ticker ===")
try:
    bt = client.get_book_ticker("BTC_USDT_PERP")
    if bt:
        for k, v in bt.items():
            print(f"  {k}: {v}")
    else:
        print("  No book ticker data")
except Exception as e:
    print(f"  Error: {e}")

# 5. Check open orders on PERP
print()
print("=== 5. BTC_USDT_PERP Open Orders ===")
try:
    orders = client.get_open_orders("BTC_USDT_PERP")
    print(f"  Open orders: {len(orders)}")
    for o in orders[:3]:
        print(f"  {o}")
except Exception as e:
    print(f"  Error: {e}")

# 6. Try to check futures-specific endpoints (from docs site)
print()
print("=== 6. Futures-Specific Endpoints Test ===")

# Try futures balance
futures_paths = [
    ("GET", "/api/v1/account/futureBalances", "Futures Balance"),
    ("GET", "/api/v1/account/positions", "Active Positions"),
    ("GET", "/api/v1/account/activePositions", "Active Positions v2"),
    ("GET", "/api/v1/trade/leverage", "Get Leverage"),
]

for method, path, name in futures_paths:
    try:
        params = {"symbol": "BTC_USDT_PERP"}
        data = client._request(method, path, params=params, signed=True)
        print(f"  {name} ({path}): OK")
        if isinstance(data, dict):
            for k, v in list(data.items())[:5]:
                print(f"    {k}: {v}")
        elif isinstance(data, list):
            print(f"    Items: {len(data)}")
    except PionexAPIError as e:
        print(f"  {name} ({path}): {e.code} - {e.message}")
    except Exception as e:
        print(f"  {name} ({path}): {e}")

# 7. DRY RUN: simulate placing a PERP order (DO NOT actually place)
print()
print("=== 7. DRY RUN Order Simulation ===")
try:
    ticker = client.get_ticker("BTC_USDT_PERP")
    price = float(ticker.get("close", 0))
    print(f"  Current price: {price}")
    print(f"  Would BUY 0.001 BTC_USDT_PERP MARKET")
    print(f"  DRY_RUN={cfg.DRY_RUN} - NOT placing real order")
except Exception as e:
    print(f"  Error: {e}")

client.close()
print()
print("=== PERP TEST COMPLETE ===")
