"""Quick strategy signal test - runs one tick immediately."""
from client import PionexClient
from config import Config
from strategy import MACrossStrategy, Signal
from risk import RiskManager

cfg = Config()
client = PionexClient(cfg)
strategy = MACrossStrategy(cfg)
risk = RiskManager(cfg=cfg)

SYM = cfg.SYMBOL
INTERVAL = cfg.KLINE_INTERVAL
needed = cfg.SLOW_MA_PERIOD + 5

print(f"Symbol: {SYM}")
print(f"Interval: {INTERVAL}")
print(f"MA fast={cfg.FAST_MA_PERIOD}, slow={cfg.SLOW_MA_PERIOD}")
print()

# Fetch klines
klines = client.get_klines(SYM, INTERVAL, limit=needed)
print(f"Got {len(klines)} candles")

# Show last few closes
closes = [float(k["close"]) for k in klines]
print(f"\nLast 10 closes:")
for i, c in enumerate(closes[-10:]):
    print(f"  [{len(closes)-10+i}] {c:.1f}")

# Compute MAs manually
import numpy as np
fast_ma = float(np.mean(closes[-cfg.FAST_MA_PERIOD:]))
slow_ma = float(np.mean(closes[-cfg.SLOW_MA_PERIOD:]))
print(f"\nMA fast({cfg.FAST_MA_PERIOD}): {fast_ma:.2f}")
print(f"MA slow({cfg.SLOW_MA_PERIOD}): {slow_ma:.2f}")
print(f"Diff: {fast_ma - slow_ma:.2f} ({'FAST > SLOW (bullish)' if fast_ma > slow_ma else 'FAST < SLOW (bearish)'})")

# Run strategy twice to detect crossover (need prev state)
# First call sets _prev values
sig1 = strategy.evaluate(klines[:-1])  # all but last
sig2 = strategy.evaluate(klines)       # all including last
print(f"\nSignal (prev candle): {sig1.value}")
print(f"Signal (current):     {sig2.value}")

# Check risk
print(f"\nCan open position: {risk.can_open_position()}")
print(f"Position open: {risk.position.is_open}")

current_price = closes[-1]
print(f"\nCurrent price: {current_price:.1f}")
if sig2 == Signal.BUY:
    size = risk.calculate_size(current_price)
    print(f"[DRY RUN] Would BUY {size} {SYM} @ {current_price:.1f}")
elif sig2 == Signal.SELL:
    size = risk.calculate_size(current_price)
    print(f"[DRY RUN] Would SELL {size} {SYM} @ {current_price:.1f}")
else:
    print("Signal: HOLD - no action")

client.close()
