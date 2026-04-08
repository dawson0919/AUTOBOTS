"""Check 2025 April seasonal performance for S&P500, BTC, ETH"""
import json, urllib.request, datetime as dt, sys
sys.stdout.reconfigure(encoding='utf-8')

def fetch(symbol, name):
    start = int(dt.datetime(2025, 1, 1).timestamp())
    end = int(dt.datetime(2025, 12, 31).timestamp())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1={start}&period2={end}&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    r = data["chart"]["result"][0]
    pm = {}
    for ts, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]):
        if c: pm[dt.datetime.fromtimestamp(ts).date()] = c
    print(f"{name}: {len(pm)} days, {min(pm.keys())} ~ {max(pm.keys())}")
    return pm

def nearest(pm, d, direction=1):
    for i in range(10):
        c = d + dt.timedelta(days=i * direction)
        if c in pm: return c
    return None

print("Fetching 2025 data...")
sp = fetch("%5EGSPC", "S&P 500")
btc = fetch("BTC-USD", "BTC")
eth = fetch("ETH-USD", "ETH")

print()
print("=" * 80)
print("  2025 April Seasonal Check: Apr 1 Entry")
print("=" * 80)
print()

for name, pm in [("S&P 500", sp), ("BTC", btc), ("ETH", eth)]:
    entry_d = nearest(pm, dt.date(2025, 4, 1))
    if not entry_d:
        print(f"  {name}: No April data available yet")
        continue
    entry_p = pm[entry_d]

    # Current/latest price
    latest_d = max(pm.keys())
    latest_p = pm[latest_d]
    latest_ret = ((latest_p / entry_p) - 1) * 100

    print(f"  {name}:")
    print(f"    Entry:   {entry_d}  @ {entry_p:,.2f}")
    print(f"    Latest:  {latest_d}  @ {latest_p:,.2f}  ({'+' if latest_ret>=0 else ''}{latest_ret:.2f}%)")

    # Check each exit
    for label, m, d in [("Apr 30", 4, 30), ("May 30", 5, 30), ("Jun 30", 6, 30)]:
        try:
            exit_d = nearest(pm, dt.date(2025, m, d), direction=-1)
            if exit_d:
                exit_p = pm[exit_d]
                ret = ((exit_p / entry_p) - 1) * 100
                win = "WIN" if ret > 0 else "LOSS"
                print(f"    -> {label}: {exit_d} @ {exit_p:,.2f}  ({'+' if ret>=0 else ''}{ret:.2f}%) [{win}]")
            else:
                print(f"    -> {label}: data not yet available")
        except:
            print(f"    -> {label}: data not yet available")
    print()

# YTD context
print("  2025 YTD Context:")
for name, pm in [("S&P 500", sp), ("BTC", btc), ("ETH", eth)]:
    jan1 = nearest(pm, dt.date(2025, 1, 2))
    latest_d = max(pm.keys())
    if jan1:
        ytd = ((pm[latest_d] / pm[jan1]) - 1) * 100
        print(f"    {name}: Jan 2 @ {pm[jan1]:,.2f} -> {latest_d} @ {pm[latest_d]:,.2f}  YTD: {'+' if ytd>=0 else ''}{ytd:.2f}%")
