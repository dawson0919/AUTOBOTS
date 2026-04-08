"""
三刀流 v6 — Parameter Optimizer
================================
Fetch data once, sweep all parameter combinations in memory.

Usage:
    BT_SYMBOL=BTC_USDT_PERP python optimize_three_kingdoms.py
    BT_SYMBOL=ETH_USDT_PERP python optimize_three_kingdoms.py
"""
from __future__ import annotations
import os, sys, itertools, json
from dataclasses import dataclass, asdict
import httpx
import numpy as np

SYMBOL = os.getenv("BT_SYMBOL", "BTC_USDT_PERP")
INTERVAL = os.getenv("BT_INTERVAL", "4H")
INITIAL_CAPITAL = 10000.0

_INTERVAL_MAP = {"1H":60,"1M":1,"5M":5,"15M":15,"30M":30,"60M":60,"4H":240,"8H":480,"1D":1440}
# Pionex API interval names (e.g. "1H" -> "60M")
_API_INTERVAL = {"1H":"60M"}
API_INTERVAL = _API_INTERVAL.get(INTERVAL, INTERVAL)
INTERVAL_MINUTES = _INTERVAL_MAP.get(INTERVAL, 60)

LEVERAGE = float(os.getenv("BT_LEV", "1"))
COMMISSION = float(os.getenv("BT_COMM", "0"))
SLIPPAGE = float(os.getenv("BT_SLIP", "0"))

# ── Parameter Grid ──
GRID = {
    "liu_bei":   [150, 200, 250, 300, 400],
    "guan_yu":   [40, 50, 60, 80, 100],
    "zhang_fei": [10, 20, 30, 40, 50],
    "dist_pct":  [1.0, 2.0, 3.0, 5.0],
    "no_bounce": [True, False],
}

def fetch_klines() -> list[dict]:
    base_url = "https://api.pionex.com"
    all_k = {}
    end_time = None
    for _ in range(40):
        params = {"symbol": SYMBOL, "interval": API_INTERVAL, "limit": "500"}
        if end_time:
            params["endTime"] = str(end_time)
        try:
            resp = httpx.get(f"{base_url}/api/v1/market/klines", params=params, timeout=15)
            data = resp.json()
            if not data.get("result"):
                break
            klines = data.get("data", {}).get("klines", [])
            if not klines:
                break
            for k in klines:
                all_k[k["time"]] = k
            oldest = klines[-1]["time"]
            if end_time == oldest:
                break
            end_time = oldest
        except Exception:
            break
    return sorted(all_k.values(), key=lambda x: x["time"])


def sma_array(closes: np.ndarray, period: int) -> np.ndarray:
    """Compute SMA for entire array using cumsum for speed."""
    if len(closes) < period:
        return np.full(len(closes), np.nan)
    cumsum = np.cumsum(closes)
    cumsum = np.insert(cumsum, 0, 0)
    sma = (cumsum[period:] - cumsum[:-period]) / period
    result = np.full(len(closes), np.nan)
    result[period-1:] = sma
    return result


@dataclass
class Result:
    liu_bei: int
    guan_yu: int
    zhang_fei: int
    dist_pct: float
    no_bounce: bool
    return_pct: float
    max_dd: float
    profit_factor: float
    trades: int
    win_rate: float


def run_single(closes: np.ndarray, lb_p: int, gy_p: int, zf_p: int,
               dist_pct: float, no_bounce: bool) -> Result | None:
    """Run one parameter set — vectorized where possible, loop for state machine."""
    n = len(closes)
    if n < lb_p + 1:
        return None

    # Precompute all SMAs
    lb_arr = sma_array(closes, lb_p)
    gy_arr = sma_array(closes, gy_p)
    zf_arr = sma_array(closes, zf_p)

    # State machine (must be sequential)
    sig_state = 0
    equity = INITIAL_CAPITAL
    pos_dir = 0  # 0=flat, 1=long, -1=short
    pos_entry = 0.0
    peak_eq = INITIAL_CAPITAL
    max_dd = 0.0

    wins_pct = []
    losses_pct = []
    trade_count = 0
    is_liquidated = False

    start_bar = lb_p  # need all MAs valid

    for i in range(start_bar, n):
        c = closes[i]
        lb = lb_arr[i]
        gy = gy_arr[i]
        zf = zf_arr[i]

        if np.isnan(lb) or np.isnan(gy) or np.isnan(zf):
            continue

        # ── Raw signal ──
        is_bull = c > lb
        is_bear = c < lb
        gy_dist = abs(c - gy) / gy * 100 if gy > 0 else 0
        far_enough = dist_pct == 0 or gy_dist >= dist_pct
        above_gy = c > gy and far_enough
        below_gy = c < gy and far_enough

        raw_sig = 0
        if is_bull and above_gy and c > zf:
            raw_sig = 1   # MAIN_LONG
        elif is_bear and below_gy and c < zf:
            raw_sig = 2   # MAIN_SHORT
        elif not no_bounce and is_bear and above_gy:
            raw_sig = 4   # BOUNCE_LONG
        elif is_bull and below_gy:
            raw_sig = 3   # CORR_SHORT

        # Direction
        if raw_sig in (1, 4):
            raw_dir = 1
        elif raw_sig in (2, 3):
            raw_dir = -1
        else:
            raw_dir = 0

        if sig_state in (1, 4):
            prev_dir = 1
        elif sig_state in (2, 3):
            prev_dir = -1
        else:
            prev_dir = 0

        sig_changed = raw_dir != 0 and raw_dir != prev_dir

        if raw_sig != 0:
            sig_state = raw_sig

        # ── Trade execution ──
        if sig_changed:
            # Close existing
            if pos_dir != 0:
                if pos_dir == 1:
                    rpct = (c - pos_entry) / pos_entry
                else:
                    rpct = (pos_entry - c) / pos_entry

                rpct -= (COMMISSION + SLIPPAGE) * 2
                leveraged_rpct = rpct * LEVERAGE
                if leveraged_rpct <= -1.0:
                    leveraged_rpct = -1.0
                    is_liquidated = True

                equity *= (1 + leveraged_rpct)
                trade_count += 1
                if leveraged_rpct > 0:
                    wins_pct.append(leveraged_rpct)
                else:
                    losses_pct.append(leveraged_rpct)

                if is_liquidated:
                    break

            # Open new
            if raw_dir == 1:
                pos_dir = 1
            else:
                pos_dir = -1
            pos_entry = c

        # Track drawdown (unrealized)
        if pos_dir != 0:
            if pos_dir == 1:
                ur = (c - pos_entry) / pos_entry
            else:
                ur = (pos_entry - c) / pos_entry
            
            leveraged_ur = (ur - (COMMISSION + SLIPPAGE)) * LEVERAGE
            cur_eq = equity * (1 + max(-1.0, leveraged_ur))
        else:
            cur_eq = equity

        if cur_eq > peak_eq:
            peak_eq = cur_eq
        dd = (peak_eq - cur_eq) / peak_eq if peak_eq > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # Close remaining
    if pos_dir != 0:
        c = closes[-1]
        if pos_dir == 1:
            rpct = (c - pos_entry) / pos_entry
        else:
            rpct = (pos_entry - c) / pos_entry
        rpct -= (COMMISSION + SLIPPAGE) * 2
        leveraged_rpct = rpct * LEVERAGE
        if leveraged_rpct <= -1.0:
            leveraged_rpct = -1.0
            is_liquidated = True

        equity *= (1 + leveraged_rpct)
        trade_count += 1
        if leveraged_rpct > 0:
            wins_pct.append(leveraged_rpct)
        else:
            losses_pct.append(leveraged_rpct)

    if trade_count == 0:
        return None

    total_return = (equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    gw = sum(wins_pct) if wins_pct else 0
    gl = abs(sum(losses_pct)) if losses_pct else 0
    pf = gw / gl if gl > 0 else (99.0 if gw > 0 else 0)
    wr = len(wins_pct) / trade_count * 100

    return Result(
        liu_bei=lb_p, guan_yu=gy_p, zhang_fei=zf_p,
        dist_pct=dist_pct, no_bounce=no_bounce,
        return_pct=round(total_return, 2),
        max_dd=round(max_dd * 100, 2),
        profit_factor=round(pf, 2),
        trades=trade_count,
        win_rate=round(wr, 1),
    )


def main():
    print(f"三刀流 v6 Parameter Optimizer")
    print(f"Symbol: {SYMBOL}  |  Interval: {INTERVAL}  |  Leverage: {LEVERAGE}x")
    print(f"Commission: {COMMISSION*100:.2f}%  |  Slippage: {SLIPPAGE*100:.2f}%")
    print("=" * 70)

    print(f"Fetching {SYMBOL} {INTERVAL} klines...")
    klines = fetch_klines()
    if len(klines) < 310:
        print(f"ERROR: Only {len(klines)} candles, need 310+")
        sys.exit(1)

    closes = np.array([float(k["close"]) for k in klines])
    days = (klines[-1]["time"] - klines[0]["time"]) / 86400000
    print(f"Loaded {len(klines)} candles ({days:.0f} days)")

    # Generate all combinations
    combos = list(itertools.product(
        GRID["liu_bei"], GRID["guan_yu"], GRID["zhang_fei"],
        GRID["dist_pct"], GRID["no_bounce"]
    ))
    # Filter invalid: liu_bei > guan_yu > zhang_fei
    combos = [(lb, gy, zf, d, nb) for lb, gy, zf, d, nb in combos if lb > gy > zf]

    total = len(combos)
    print(f"Testing {total} parameter combinations...")
    print()

    results: list[Result] = []
    for idx, (lb, gy, zf, dist, nb) in enumerate(combos):
        if idx % 500 == 0 and idx > 0:
            print(f"  Progress: {idx}/{total} ({idx/total*100:.0f}%)")
        r = run_single(closes, lb, gy, zf, dist, nb)
        if r and r.trades >= 5:  # minimum 5 trades
            results.append(r)

    print(f"\nCompleted: {len(results)} valid results (>=5 trades)")
    print()

    # ── Sort by return ──
    results.sort(key=lambda r: r.return_pct, reverse=True)

    print("=" * 95)
    print(f"  TOP 30 by RETURN — {SYMBOL} {INTERVAL}")
    print("=" * 95)
    print(f"  {'#':>3}  {'LB':>4}  {'GY':>3}  {'ZF':>3}  {'DIST':>5}  {'Bnc':>3}  {'Return':>9}  {'MaxDD':>7}  {'PF':>5}  {'Trades':>6}  {'WR':>5}")
    print("  " + "-" * 88)
    for i, r in enumerate(results[:30], 1):
        bnc = "OFF" if r.no_bounce else "ON"
        ret_color = "+" if r.return_pct > 0 else " "
        print(
            f"  {i:3d}  {r.liu_bei:4d}  {r.guan_yu:3d}  {r.zhang_fei:3d}  {r.dist_pct:5.1f}  {bnc:>3}  "
            f"{ret_color}{r.return_pct:>8.2f}%  {r.max_dd:>6.2f}%  {r.profit_factor:>5.2f}  {r.trades:>6d}  {r.win_rate:>4.1f}%"
        )

    # ── Sort by Sharpe-like (return / max_dd) ──
    results_risk = [r for r in results if r.max_dd > 0]
    results_risk.sort(key=lambda r: r.return_pct / r.max_dd, reverse=True)

    print()
    print("=" * 95)
    print(f"  TOP 30 by RISK-ADJUSTED (Return/MaxDD) — {SYMBOL} {INTERVAL}")
    print("=" * 95)
    print(f"  {'#':>3}  {'LB':>4}  {'GY':>3}  {'ZF':>3}  {'DIST':>5}  {'Bnc':>3}  {'Return':>9}  {'MaxDD':>7}  {'Ret/DD':>7}  {'PF':>5}  {'Trades':>6}  {'WR':>5}")
    print("  " + "-" * 95)
    for i, r in enumerate(results_risk[:30], 1):
        bnc = "OFF" if r.no_bounce else "ON"
        ratio = r.return_pct / r.max_dd if r.max_dd > 0 else 0
        print(
            f"  {i:3d}  {r.liu_bei:4d}  {r.guan_yu:3d}  {r.zhang_fei:3d}  {r.dist_pct:5.1f}  {bnc:>3}  "
            f"+{r.return_pct:>8.2f}%  {r.max_dd:>6.2f}%  {ratio:>7.2f}  {r.profit_factor:>5.2f}  {r.trades:>6d}  {r.win_rate:>4.1f}%"
        )

    # ── Current v6 baseline ──
    v6 = [r for r in results if r.liu_bei == 200 and r.guan_yu == 50 and r.zhang_fei == 20 and r.dist_pct == 2.0 and r.no_bounce]
    if v6:
        print()
        print("=" * 95)
        print(f"  CURRENT v6 BASELINE: LB=200 GY=50 ZF=20 DIST=2.0 BounceL=OFF")
        r = v6[0]
        print(f"  Return: {r.return_pct:+.2f}%  |  MaxDD: {r.max_dd:.2f}%  |  PF: {r.profit_factor:.2f}  |  Trades: {r.trades}  |  WR: {r.win_rate:.1f}%")
        # Find rank
        rank_ret = next((i+1 for i, x in enumerate(results) if x is r), "?")
        rank_risk = next((i+1 for i, x in enumerate(results_risk) if x is r), "?")
        print(f"  Rank: #{rank_ret} by return  |  #{rank_risk} by risk-adjusted")
        print("=" * 95)


    # ── Save JSON for presentation ──
    top_return = results[:5] if results else []
    top_risk = results_risk[:5] if results_risk else []
    out = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "candles": len(klines),
        "days": round(days),
        "total_combos": total,
        "valid_results": len(results),
        "top_by_return": [asdict(r) for r in top_return],
        "top_by_risk_adjusted": [asdict(r) for r in top_risk],
        "v6_baseline": asdict(v6[0]) if v6 else None,
    }
    out_path = os.path.join(os.path.dirname(__file__), "output", f"optim_{SYMBOL}.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
