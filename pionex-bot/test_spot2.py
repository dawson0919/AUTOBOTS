"""Test spot order with proper notional amount."""
from client import PionexClient, PionexAPIError

client = PionexClient()

# Check min amount
symbols = client.get_symbols(symbol="BTC_USDT")
s = symbols[0]
print(f"minAmount: {s.get('minAmount')}")
print(f"minTradeSize: {s.get('minTradeSize')}")

# Try with bigger size that meets min notional
price = float(client.get_ticker("BTC_USDT").get("close", 0))
safe_price = str(round(price * 0.5, 2))
size = "0.001"  # 0.001 * 35000 = ~35 USDT
print(f"Testing: BUY {size} @ {safe_price} (notional ~${float(size)*float(safe_price):.1f})")

try:
    result = client.new_order("BTC_USDT", "BUY", "LIMIT", size=size, price=safe_price)
    oid = result.get("orderId", "")
    print(f"SPOT ORDER SUCCESS! orderId={oid}")
    client.cancel_order("BTC_USDT", str(oid))
    print("Cancelled OK")
except PionexAPIError as e:
    print(f"Error: {e.code} - {e.message}")

client.close()
