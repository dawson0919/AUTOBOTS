"""Test real futures order placement and cancellation.
Places a LIMIT BUY far below market price, then immediately cancels it.
"""
from client import PionexClient, PionexAPIError
from config import Config

cfg = Config()
client = PionexClient(cfg)
SYM = "BTC_USDT_PERP"

# 1. Get current price
ticker = client.get_ticker(SYM)
price = float(ticker.get("close", 0))
print(f"Current BTC price: ${price:.1f}")

# 2. Get symbol constraints
symbols = client.get_symbols(symbol=SYM)
s = symbols[0] if symbols else {}
min_size = s.get("minSizeLimit", "0.0001")
print(f"Min size: {min_size}")
print(f"Base step: {s.get('baseStep')}")
print(f"Quote step: {s.get('quoteStep')}")

# 3. Get leverage
levs = client.get_leverage(SYM)
if levs:
    print(f"Leverage: {levs[0].get('leverage')}x")

# 4. Place a LIMIT BUY at 50% below market (will NOT fill)
safe_price = str(round(price * 0.5, 1))  # 50% below market
test_size = min_size  # minimum size

print(f"\nPlacing test order: BUY {test_size} {SYM} LIMIT @ ${safe_price}")
print("(This is 50% below market - will NOT fill)")

try:
    result = client.new_futures_order(
        symbol=SYM,
        side="BUY",
        order_type="LIMIT",
        size=test_size,
        price=safe_price,
    )
    order_id = result.get("orderId", "")
    print(f"ORDER PLACED! orderId={order_id}")

    # 5. Verify it shows in open orders
    orders = client.get_futures_open_orders(SYM)
    print(f"Open orders: {len(orders)}")
    for o in orders:
        print(f"  id={o.get('orderId')} side={o.get('side')} price={o.get('price')} "
              f"size={o.get('size')} status={o.get('status')}")

    # 6. Cancel it immediately
    print(f"\nCancelling order {order_id}...")
    cancel = client.cancel_futures_order(SYM, str(order_id))
    print(f"Cancelled successfully!")

    # 7. Verify cancelled
    orders_after = client.get_futures_open_orders(SYM)
    print(f"Open orders after cancel: {len(orders_after)}")

except PionexAPIError as e:
    print(f"API Error: {e.code} - {e.message}")
except Exception as e:
    print(f"Error: {e}")

# 8. Check positions (should be unchanged)
positions = client.get_active_positions(SYM)
print(f"\nActive positions: {len(positions)}")

client.close()
print("\n=== REAL ORDER TEST COMPLETE ===")
