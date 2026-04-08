"""Test spot order placement (LIMIT far below market, then cancel)."""
from client import PionexClient, PionexAPIError
from config import Config

cfg = Config()
client = PionexClient(cfg)
SYM = "BTC_USDT"

# 1. Get current price & symbol info
ticker = client.get_ticker(SYM)
price = float(ticker.get("close", 0))
symbols = client.get_symbols(symbol=SYM)
s = symbols[0] if symbols else {}
print(f"BTC/USDT: ${price:.1f}")
print(f"Min size: {s.get('minTradeSize')}")

# 2. Place LIMIT BUY far below market (won't fill)
safe_price = str(round(price * 0.5, 2))
test_size = s.get("minTradeSize", "0.000001")
print(f"\nPlacing SPOT test: BUY {test_size} {SYM} LIMIT @ ${safe_price}")

try:
    result = client.new_order(
        symbol=SYM,
        side="BUY",
        order_type="LIMIT",
        size=test_size,
        price=safe_price,
    )
    order_id = result.get("orderId", "")
    print(f"SPOT ORDER OK! orderId={order_id}")

    # Cancel
    client.cancel_order(SYM, str(order_id))
    print(f"Cancelled: {order_id}")

except PionexAPIError as e:
    print(f"Spot Error: {e.code} - {e.message}")

# 3. Test futures order for comparison
print(f"\nPlacing FUTURES test: BUY 0.0001 BTC_USDT_PERP LIMIT @ ${safe_price}")
try:
    result = client.new_futures_order(
        symbol="BTC_USDT_PERP",
        side="BUY",
        order_type="LIMIT",
        size="0.0001",
        price=safe_price,
    )
    order_id = result.get("orderId", "")
    print(f"FUTURES ORDER OK! orderId={order_id}")
    client.cancel_futures_order("BTC_USDT_PERP", str(order_id))
    print(f"Cancelled: {order_id}")
except PionexAPIError as e:
    print(f"Futures Error: {e.code} - {e.message}")

client.close()
print("\n=== DONE ===")
