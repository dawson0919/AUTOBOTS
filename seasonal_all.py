"""Complete Seasonal Analysis: S&P 500, BTC, ETH
   Entry: April 1 -> Hold to Apr 30 / May 30 / Jun 30 / Jul 15
   Including 2025 actual results
"""
import json, urllib.request, datetime as dt, sys
sys.stdout.reconfigure(encoding='utf-8')

def fetch(symbol, name, start_year):
    start = int(dt.datetime(start_year, 1, 1).timestamp())
    end = int(dt.datetime(2026, 1, 1).timestamp())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1={start}&period2={end}&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    r = data["chart"]["result"][0]
    pm = {}
    for ts, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]):
        if c: pm[dt.datetime.fromtimestamp(ts).date()] = c
    print(f"  {name}: {len(pm)} days ({min(pm.keys())} ~ {max(pm.keys())})")
    return pm

def nearest(pm, d, direction=1):
    for i in range(10):
        c = d + dt.timedelta(days=i * direction)
        if c in pm: return c
    return None

print("Fetching data...")
sp = fetch("%5EGSPC", "S&P 500", 2015)
btc = fetch("BTC-USD", "BTC", 2015)
eth = fetch("ETH-USD", "ETH", 2016)

# Exit targets
exits = [
    ("Apr 30", 4, 30),
    ("May 30", 5, 30),
    ("Jun 30", 6, 30),
    ("Jul 15", 7, 15),
]

def analyze_asset(name, pm, year_range):
    print()
    print("=" * 130)
    print(f"  {name}: April 1 Entry -> Hold to Various Exits ({min(year_range)}-{max(year_range)})")
    print("=" * 130)

    # Header
    exit_labels = [e[0] for e in exits]
    hdr = f"{'Year':>6} | {'Entry':>10} {'Price':>10} |"
    for el in exit_labels:
        hdr += f" {el:>9} {'Return':>8} |"
    print(hdr)
    print("-" * 130)

    all_results = []
    for year in year_range:
        entry_d = nearest(pm, dt.date(year, 4, 1))
        if not entry_d:
            continue
        entry_p = pm[entry_d]

        row = {"year": year, "entry_price": entry_p}
        line = f"{year:>6} | {str(entry_d)[5:]:>10} {entry_p:>10.1f} |"

        for label, m, d in exits:
            exit_d = nearest(pm, dt.date(year, m, d), direction=-1)
            if exit_d and exit_d in pm:
                exit_p = pm[exit_d]
                ret = ((exit_p / entry_p) - 1) * 100
                row[label] = ret
                sign = "+" if ret >= 0 else ""
                line += f" {exit_p:>9.1f} {sign}{ret:>6.1f}% |"
            else:
                row[label] = None
                line += f" {'N/A':>9} {'N/A':>7} |"

        all_results.append(row)
        # Highlight 2025
        if year == 2025:
            print(f"  >>> {line} <<< 2025")
        else:
            print(f"      {line}")

    print("-" * 130)

    # Summary table
    print(f"\n  {name} WIN RATE SUMMARY")
    print(f"  {'Exit':>12} | {'Win Rate':>12} | {'Avg Return':>12} | {'Avg Win':>10} | {'Avg Loss':>10} | {'Max Gain':>10} | {'Max Loss':>10} | {'Median':>8}")
    print(f"  {'-'*12}-+-{'-'*12}-+-{'-'*12}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*8}")

    for label, _, _ in exits:
        rets = [r[label] for r in all_results if r.get(label) is not None]
        if not rets: continue
        wins = [r for r in rets if r > 0]
        losses = [r for r in rets if r <= 0]
        avg = sum(rets) / len(rets)
        avg_w = sum(wins) / len(wins) if wins else 0
        avg_l = sum(losses) / len(losses) if losses else 0
        sorted_r = sorted(rets)
        median = sorted_r[len(sorted_r)//2]
        wr = f"{len(wins)}/{len(rets)}={len(wins)/len(rets)*100:.0f}%"
        print(f"  {label:>12} | {wr:>12} | {avg:>+10.2f}% | {avg_w:>+8.2f}% | {avg_l:>+8.2f}% | {max(rets):>+8.2f}% | {min(rets):>+8.2f}% | {median:>+6.2f}%")

    return all_results

sp_r = analyze_asset("S&P 500", sp, range(2015, 2026))
btc_r = analyze_asset("BTC", btc, range(2015, 2026))
eth_r = analyze_asset("ETH", eth, range(2018, 2026))

# ============================================================
# Cross-asset comparison table
# ============================================================
print()
print("=" * 90)
print("  CROSS-ASSET WIN RATE COMPARISON (Apr 1 Entry)")
print("=" * 90)
print()
print(f"  {'Asset':>12} | {'-> Apr 30':>14} | {'-> May 30':>14} | {'-> Jun 30':>14} | {'-> Jul 15':>14}")
print(f"  {'-'*12}-+-{'-'*14}-+-{'-'*14}-+-{'-'*14}-+-{'-'*14}")

for name, results in [("S&P 500", sp_r), ("BTC", btc_r), ("ETH", eth_r)]:
    cells = []
    for label, _, _ in exits:
        rets = [r[label] for r in results if r.get(label) is not None]
        if not rets:
            cells.append("N/A")
            continue
        w = sum(1 for r in rets if r > 0)
        avg = sum(rets) / len(rets)
        cells.append(f"{w}/{len(rets)}={w/len(rets)*100:.0f}% {avg:+.1f}%")
    print(f"  {name:>12} | {cells[0]:>14} | {cells[1]:>14} | {cells[2]:>14} | {cells[3]:>14}")

# 2025 specific
print()
print("=" * 90)
print("  2025 ACTUAL RESULTS (Apr 1 Entry)")
print("=" * 90)
print()
print(f"  {'Asset':>12} | {'-> Apr 30':>14} | {'-> May 30':>14} | {'-> Jun 30':>14} | {'-> Jul 15':>14}")
print(f"  {'-'*12}-+-{'-'*14}-+-{'-'*14}-+-{'-'*14}-+-{'-'*14}")

for name, results in [("S&P 500", sp_r), ("BTC", btc_r), ("ETH", eth_r)]:
    r25 = [r for r in results if r["year"] == 2025]
    if not r25: continue
    r = r25[0]
    cells = []
    for label, _, _ in exits:
        v = r.get(label)
        if v is not None:
            win = "W" if v > 0 else "L"
            cells.append(f"{v:+.2f}% [{win}]")
        else:
            cells.append("N/A")
    print(f"  {name:>12} | {cells[0]:>14} | {cells[1]:>14} | {cells[2]:>14} | {cells[3]:>14}")

# 2026 prediction context
print()
print("=" * 90)
print("  2026 CONTEXT: Today is Mar 31 - April Entry Window Opening")
print("=" * 90)
print()

for name, pm in [("S&P 500", sp), ("BTC", btc), ("ETH", eth)]:
    latest_d = max(pm.keys())
    latest_p = pm[latest_d]
    # YTD
    jan_d = nearest(pm, dt.date(2026, 1, 2))
    if jan_d:
        ytd = ((latest_p / pm[jan_d]) - 1) * 100
        print(f"  {name:>12}: Current {latest_p:>10,.2f} (as of {latest_d})  YTD: {ytd:+.2f}%")

# Monthly returns for April & May
print()
print("=" * 90)
print("  MONTHLY RETURNS: April & May Individual Performance")
print("=" * 90)

for name, pm, yr in [("S&P 500", sp, range(2015, 2026)), ("BTC", btc, range(2015, 2026)), ("ETH", eth, range(2018, 2026))]:
    print(f"\n  {name}")
    for month, mname in [(4, "April"), (5, "May")]:
        rets = []
        for year in yr:
            sd = nearest(pm, dt.date(year, month, 1))
            if month < 12:
                last = dt.date(year, month + 1, 1) - dt.timedelta(days=1)
            else:
                last = dt.date(year, 12, 31)
            ed = nearest(pm, last, direction=-1)
            if sd and ed and sd in pm and ed in pm:
                ret = ((pm[ed] / pm[sd]) - 1) * 100
                rets.append((year, ret))
        if rets:
            w = sum(1 for _, r in rets if r > 0)
            avg = sum(r for _, r in rets) / len(rets)
            details = " ".join(f"{y}:{'+'if r>=0 else ''}{r:.0f}%" for y, r in rets)
            print(f"    {mname:>5}: Win {w}/{len(rets)}={w/len(rets)*100:.0f}% | Avg {avg:+.1f}% | {details}")

print("\n\nDone.")
