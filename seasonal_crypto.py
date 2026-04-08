"""BTC & ETH Seasonal Statistics: April Entry Analysis (10 Years)"""
import json
import urllib.request
import datetime as dt
import sys

sys.stdout.reconfigure(encoding='utf-8')

def fetch_yahoo(symbol, display_name, start_year, end_year):
    start = int(dt.datetime(start_year, 1, 1).timestamp())
    end = int(dt.datetime(end_year + 1, 1, 1).timestamp())
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?period1={start}&period2={end}&interval=1d"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    print(f"Fetching {display_name} data ({start_year}-{end_year})...")
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())

    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]

    price_map = {}
    for ts, c in zip(timestamps, closes):
        if c is not None:
            d = dt.datetime.fromtimestamp(ts).date()
            price_map[d] = c
    return price_map

def find_nearest(price_map, target_date, direction=1, max_days=10):
    for i in range(max_days):
        check = target_date + dt.timedelta(days=i * direction)
        if check in price_map:
            return check
    return None

def analyze(display_name, price_map, years):
    print()
    print("=" * 110)
    print(f"  {display_name} Seasonal Analysis: Long on ~April 1, Hold to Month-End")
    print("=" * 110)

    header = f"{'Year':>6} | {'Entry':>12} {'Price':>12} | {'Apr30':>12} {'Return':>9} | {'May30':>12} {'Return':>9} | {'Jun30':>12} {'Return':>9}"
    print(header)
    print("-" * 110)

    results = []
    for year in years:
        entry_date = find_nearest(price_map, dt.date(year, 4, 1))
        if not entry_date:
            continue
        entry_price = price_map[entry_date]

        exit_apr = find_nearest(price_map, dt.date(year, 4, 30), direction=-1)
        exit_may = find_nearest(price_map, dt.date(year, 5, 30), direction=-1)
        exit_jun = find_nearest(price_map, dt.date(year, 6, 30), direction=-1)

        apr_price = price_map.get(exit_apr) if exit_apr else None
        may_price = price_map.get(exit_may) if exit_may else None
        jun_price = price_map.get(exit_jun) if exit_jun else None

        apr_ret = ((apr_price / entry_price) - 1) * 100 if apr_price else None
        may_ret = ((may_price / entry_price) - 1) * 100 if may_price else None
        jun_ret = ((jun_price / entry_price) - 1) * 100 if jun_price else None

        results.append({"year": year, "apr_ret": apr_ret, "may_ret": may_ret, "jun_ret": jun_ret})

        def fmt_ret(r):
            if r is None: return "      N/A"
            return f"{'+'if r>=0 else ''}{r:7.2f}%"
        def fmt_price(p):
            if p is None: return "       N/A"
            if p > 1000: return f"{p:>11.1f}"
            return f"{p:>11.2f}"

        ep = f"{entry_price:>11.1f}" if entry_price > 1000 else f"{entry_price:>11.2f}"
        print(f"{year:>6} | {str(entry_date):>12} {ep} | {fmt_price(apr_price)} {fmt_ret(apr_ret)} | {fmt_price(may_price)} {fmt_ret(may_ret)} | {fmt_price(jun_price)} {fmt_ret(jun_ret)}")

    print("-" * 110)

    # Win rate summary
    print(f"\n  {display_name} WIN RATE SUMMARY ({len(results)} Years)")
    print("  " + "-" * 56)

    for label, key in [("Apr 1 -> Apr 30", "apr_ret"), ("Apr 1 -> May 30", "may_ret"), ("Apr 1 -> Jun 30", "jun_ret")]:
        rets = [r[key] for r in results if r[key] is not None]
        if not rets:
            continue
        wins = [r for r in rets if r > 0]
        losses = [r for r in rets if r <= 0]
        avg = sum(rets) / len(rets)
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0

        print(f"\n  {label}:")
        print(f"    Win Rate:     {len(wins)}/{len(rets)} = {len(wins)/len(rets)*100:.0f}%")
        print(f"    Avg Return:   {avg:+.2f}%")
        print(f"    Avg Win:      {avg_win:+.2f}%")
        print(f"    Avg Loss:     {avg_loss:+.2f}%")
        print(f"    Max Gain:     {max(rets):+.2f}%")
        print(f"    Max Loss:     {min(rets):+.2f}%")

    # Monthly pattern
    print(f"\n  {display_name} Monthly Returns")
    print("  " + "-" * 56)
    for month, mname in [(4, "April"), (5, "May")]:
        print(f"\n  {mname}:")
        rets = []
        for year in years:
            sd = find_nearest(price_map, dt.date(year, month, 1))
            if month < 12:
                last = dt.date(year, month + 1, 1) - dt.timedelta(days=1)
            else:
                last = dt.date(year, 12, 31)
            ed = find_nearest(price_map, last, direction=-1)
            if sd and ed and sd in price_map and ed in price_map:
                ret = ((price_map[ed] / price_map[sd]) - 1) * 100
                rets.append(ret)
                print(f"    {year}: {'+'if ret>=0 else ''}{ret:.2f}% [{'W'if ret>0 else 'L'}]")
        if rets:
            w = sum(1 for r in rets if r > 0)
            print(f"    ---")
            print(f"    Win Rate: {w}/{len(rets)} = {w/len(rets)*100:.0f}%  |  Avg: {sum(rets)/len(rets):+.2f}%")

    return results

# ============================================================
# Fetch data
# ============================================================
btc_map = fetch_yahoo("BTC-USD", "BTC", 2015, 2024)
eth_map = fetch_yahoo("ETH-USD", "ETH", 2016, 2024)

print(f"BTC data: {len(btc_map)} days ({min(btc_map.keys())} ~ {max(btc_map.keys())})")
print(f"ETH data: {len(eth_map)} days ({min(eth_map.keys())} ~ {max(eth_map.keys())})")

# ============================================================
# Analyze
# ============================================================
btc_results = analyze("BTC", btc_map, range(2015, 2025))
eth_results = analyze("ETH", eth_map, range(2016, 2025))

# ============================================================
# Cross-asset comparison
# ============================================================
print()
print("=" * 70)
print("  CROSS-ASSET COMPARISON: Apr 1 Entry Win Rates")
print("=" * 70)
print()
print(f"  {'':>20} | {'-> Apr 30':>12} | {'-> May 30':>12} | {'-> Jun 30':>12}")
print(f"  {'-'*20}-+-{'-'*12}-+-{'-'*12}-+-{'-'*12}")

for name, results, n in [("S&P 500", None, 10), ("BTC", btc_results, 10), ("ETH", eth_results, 9)]:
    if results is None:
        # Hardcoded from previous run
        print(f"  {'S&P 500':>20} | {'70%':>12} | {'70%':>12} | {'90%':>12}")
        continue
    for label, key in [("", "apr_ret")]:
        pass
    rets_apr = [r["apr_ret"] for r in results if r["apr_ret"] is not None]
    rets_may = [r["may_ret"] for r in results if r["may_ret"] is not None]
    rets_jun = [r["jun_ret"] for r in results if r["jun_ret"] is not None]
    w_apr = f"{sum(1 for r in rets_apr if r>0)}/{len(rets_apr)} = {sum(1 for r in rets_apr if r>0)/len(rets_apr)*100:.0f}%" if rets_apr else "N/A"
    w_may = f"{sum(1 for r in rets_may if r>0)}/{len(rets_may)} = {sum(1 for r in rets_may if r>0)/len(rets_may)*100:.0f}%" if rets_may else "N/A"
    w_jun = f"{sum(1 for r in rets_jun if r>0)}/{len(rets_jun)} = {sum(1 for r in rets_jun if r>0)/len(rets_jun)*100:.0f}%" if rets_jun else "N/A"
    print(f"  {name:>20} | {w_apr:>12} | {w_may:>12} | {w_jun:>12}")

print()
print("Done.")
