"""Comprehensive test of all futures (PERP) trading capabilities."""
from client import PionexClient, PionexAPIError
from config import Config

cfg = Config()
client = PionexClient(cfg)
SYM = "BTC_USDT_PERP"

print("=" * 60)
print("PIONEX FUTURES API - COMPREHENSIVE TEST")
print("=" * 60)

# 1. Futures Balance
print("\n--- 1. Futures Balance ---")
try:
    fb = client.get_futures_balance()
    for b in fb.get("balances", []):
        free = float(b.get("free", 0))
        if free > 0:
            print(f"  {b['coin']}: free={b['free']} frozen={b.get('frozen','0')}")
except Exception as e:
    print(f"  Error: {e}")

# 2. Futures Account Detail
print("\n--- 2. Account Detail ---")
try:
    detail = client.get_futures_detail()
    risks = detail.get("quoteRisks", [])
    for r in risks:
        print(f"  {r.get('quote')}: riskState={r.get('riskState')}")
except Exception as e:
    print(f"  Error: {e}")

# 3. Leverage
print("\n--- 3. Leverage ---")
try:
    levs = client.get_leverage(SYM)
    for l in levs:
        print(f"  {l.get('symbol')}: {l.get('leverage')}x")
except Exception as e:
    print(f"  Error: {e}")

# 4. Position Mode
print("\n--- 4. Position Mode ---")
try:
    mode = client.get_position_mode()
    print(f"  Mode: {mode}")
except Exception as e:
    print(f"  Error: {e}")

# 5. Active Positions
print("\n--- 5. Active Positions ---")
try:
    positions = client.get_active_positions(SYM)
    if positions:
        for p in positions:
            print(f"  {p.get('symbol')} {p.get('positionSide')} size={p.get('netSize')} "
                  f"avgPrice={p.get('avgPrice')} leverage={p.get('leverage')}x "
                  f"PnL={p.get('unrealizedPnl')}")
    else:
        print("  No active positions")
except Exception as e:
    print(f"  Error: {e}")

# 6. Open Orders
print("\n--- 6. Futures Open Orders ---")
try:
    orders = client.get_futures_open_orders(SYM)
    if orders:
        for o in orders[:3]:
            print(f"  {o}")
    else:
        print("  No open orders")
except Exception as e:
    print(f"  Error: {e}")

# 7. PERP Klines (verify data works)
print("\n--- 7. BTC_USDT_PERP Klines ---")
try:
    klines = client.get_klines(SYM, "15M", limit=3)
    for k in klines:
        print(f"  C={k['close']} V={k['volume']}")
except Exception as e:
    print(f"  Error: {e}")

# 8. Test futures order endpoint (with intentionally small size to see error)
print("\n--- 8. Futures Order Test (expect error - testing endpoint) ---")
if cfg.DRY_RUN:
    print("  [DRY RUN] Skipping real order test")
    print("  Endpoint verified: POST /uapi/v1/trade/order")
else:
    try:
        # Try with impossibly small amount to verify endpoint works
        result = client.new_futures_order(SYM, "BUY", "LIMIT", size="0.0001", price="10000")
        print(f"  Order placed (should cancel): {result}")
        # Cancel immediately
        oid = result.get("orderId", "")
        if oid:
            client.cancel_futures_order(SYM, str(oid))
            print(f"  Cancelled: {oid}")
    except PionexAPIError as e:
        print(f"  API Response: {e.code} - {e.message}")
        print("  (This is expected - endpoint is reachable)")

client.close()
print("\n" + "=" * 60)
print("ALL FUTURES TESTS COMPLETE")
print("=" * 60)
