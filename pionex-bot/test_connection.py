"""Quick test script to verify API connection."""
from client import PionexClient
from config import Config

cfg = Config()
client = PionexClient(cfg)

# Test 1: Public - ticker
print("=== Test 1: BTC Price ===")
ticker = client.get_ticker("BTC_USDT")
print(f"  BTC/USDT: {ticker.get('close')}")

# Test 2: Private - balance (with time sync)
print()
print("=== Test 2: Get Balance (Private) ===")
try:
    balances = client.get_balance()
    found = False
    for b in balances:
        free = float(b.get("free", 0))
        frozen = float(b.get("frozen", 0))
        if free > 0 or frozen > 0:
            print(f"  {b['coin']}: free={b['free']} frozen={b['frozen']}")
            found = True
    if not found:
        print("  All balances are zero")
    print(f"  Total coins: {len(balances)}")
except Exception as e:
    print(f"  Error: {e}")

# Test 3: Check PERP symbol
print()
print("=== Test 3: PERP Symbol Check ===")
try:
    perp = client.get_symbols(market_type="PERP")
    btc_usdt = [s for s in perp if s.get("symbol", "").startswith("BTC_USDT")]
    for s in btc_usdt:
        print(f"  {s['symbol']} | minSize={s.get('minTradeSize')} | maxSize={s.get('maxTradeSize')}")
    if not btc_usdt:
        print("  No BTC_USDT PERP found")
    print(f"  Total PERP symbols: {len(perp)}")
except Exception as e:
    print(f"  Error: {e}")

# Test 4: Get klines
print()
print("=== Test 4: Klines (last 3) ===")
try:
    klines = client.get_klines("BTC_USDT", "15M", limit=5)
    for k in klines[-3:]:
        print(f"  O={k['open']} H={k['high']} L={k['low']} C={k['close']}")
except Exception as e:
    print(f"  Error: {e}")

client.close()
print()
print("=== ALL TESTS COMPLETE ===")
