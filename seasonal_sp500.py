"""S&P 500 Seasonal Statistics: April Entry Analysis (10 Years)"""
import json
import urllib.request
import datetime as dt
from collections import defaultdict

# Fetch S&P 500 data from Yahoo Finance API (^GSPC)
# 10 years: 2015-2024
start = int(dt.datetime(2015, 1, 1).timestamp())
end = int(dt.datetime(2025, 1, 1).timestamp())

url = (
    f"https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC"
    f"?period1={start}&period2={end}&interval=1d"
)
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

print("Fetching S&P 500 data (2015-2024)...")
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read())

result = data["chart"]["result"][0]
timestamps = result["timestamp"]
closes = result["indicators"]["quote"][0]["close"]

# Build date->close map
price_map = {}
for ts, c in zip(timestamps, closes):
    if c is not None:
        d = dt.datetime.fromtimestamp(ts).date()
        price_map[d] = c

def find_nearest_trading_day(target_date, direction=1, max_days=10):
    """Find nearest trading day (direction: 1=forward, -1=backward)"""
    for i in range(max_days):
        check = target_date + dt.timedelta(days=i * direction)
        if check in price_map:
            return check
    return None

print(f"Data points: {len(price_map)}")
print(f"Date range: {min(price_map.keys())} to {max(price_map.keys())}")
print()

# ============================================================
# Analysis 1: April 1 entry -> hold to Apr 30 / May 30 / Jun 30
# ============================================================
years = range(2015, 2025)  # 10 years

results = []
header = f"{'Year':>6} | {'Entry Date':>12} {'Entry Price':>12} | {'Apr30':>12} {'Return':>8} | {'May30':>12} {'Return':>8} | {'Jun30':>12} {'Return':>8}"
print("=" * len(header))
print("  S&P 500 Seasonal Analysis: Long on ~April 1, Hold to Month-End")
print("=" * len(header))
print(header)
print("-" * len(header))

for year in years:
    # Entry: nearest trading day to April 1
    entry_date = find_nearest_trading_day(dt.date(year, 4, 1))
    if not entry_date:
        continue
    entry_price = price_map[entry_date]

    # Exit dates
    exit_apr = find_nearest_trading_day(dt.date(year, 4, 30), direction=-1)
    exit_may = find_nearest_trading_day(dt.date(year, 5, 30), direction=-1)
    exit_jun = find_nearest_trading_day(dt.date(year, 6, 30), direction=-1)

    apr_price = price_map.get(exit_apr) if exit_apr else None
    may_price = price_map.get(exit_may) if exit_may else None
    jun_price = price_map.get(exit_jun) if exit_jun else None

    apr_ret = ((apr_price / entry_price) - 1) * 100 if apr_price else None
    may_ret = ((may_price / entry_price) - 1) * 100 if may_price else None
    jun_ret = ((jun_price / entry_price) - 1) * 100 if jun_price else None

    results.append({
        "year": year,
        "entry_date": entry_date,
        "entry_price": entry_price,
        "apr_ret": apr_ret,
        "may_ret": may_ret,
        "jun_ret": jun_ret,
        "apr_price": apr_price,
        "may_price": may_price,
        "jun_price": jun_price,
    })

    def fmt_ret(r):
        if r is None:
            return "   N/A"
        sign = "+" if r >= 0 else ""
        return f"{sign}{r:6.2f}%"

    def fmt_price(p):
        return f"{p:>10.2f}" if p else "       N/A"

    print(f"{year:>6} | {str(entry_date):>12} {entry_price:>10.2f}   | {fmt_price(apr_price)} {fmt_ret(apr_ret)} | {fmt_price(may_price)} {fmt_ret(may_ret)} | {fmt_price(jun_price)} {fmt_ret(jun_ret)}")

print("-" * len(header))

# ============================================================
# Win Rate Calculation
# ============================================================
print()
print("=" * 60)
print("  WIN RATE SUMMARY (10 Years: 2015-2024)")
print("=" * 60)

for label, key in [("Apr 1 -> Apr 30", "apr_ret"), ("Apr 1 -> May 30", "may_ret"), ("Apr 1 -> Jun 30", "jun_ret")]:
    rets = [r[key] for r in results if r[key] is not None]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    avg = sum(rets) / len(rets) if rets else 0
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    max_gain = max(rets) if rets else 0
    max_loss = min(rets) if rets else 0

    print(f"\n  {label}:")
    print(f"    Win Rate:     {len(wins)}/{len(rets)} = {len(wins)/len(rets)*100:.0f}%")
    print(f"    Avg Return:   {avg:+.2f}%")
    print(f"    Avg Win:      {avg_win:+.2f}%")
    print(f"    Avg Loss:     {avg_loss:+.2f}%")
    print(f"    Max Gain:     {max_gain:+.2f}%")
    print(f"    Max Loss:     {max_loss:+.2f}%")

# ============================================================
# Monthly Pattern: April and May individually
# ============================================================
print()
print("=" * 60)
print("  MONTHLY PATTERN: April & May Individual Returns")
print("=" * 60)

for month, month_name in [(4, "April"), (5, "May")]:
    print(f"\n  {month_name} Monthly Returns:")
    rets = []
    for year in years:
        # First trading day of month
        start_d = find_nearest_trading_day(dt.date(year, month, 1))
        # Last trading day of month
        if month == 12:
            last_day = dt.date(year, 12, 31)
        else:
            last_day = dt.date(year, month + 1, 1) - dt.timedelta(days=1)
        end_d = find_nearest_trading_day(last_day, direction=-1)

        if start_d and end_d and start_d in price_map and end_d in price_map:
            ret = ((price_map[end_d] / price_map[start_d]) - 1) * 100
            rets.append(ret)
            sign = "+" if ret >= 0 else ""
            win_mark = "W" if ret > 0 else "L"
            print(f"    {year}: {sign}{ret:.2f}% [{win_mark}]")

    if rets:
        wins = sum(1 for r in rets if r > 0)
        avg = sum(rets) / len(rets)
        print(f"    ---")
        print(f"    Win Rate: {wins}/{len(rets)} = {wins/len(rets)*100:.0f}%")
        print(f"    Avg Return: {avg:+.2f}%")

print()
print("Analysis complete.")
