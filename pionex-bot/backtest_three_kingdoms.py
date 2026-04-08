"""
三刀流 v6 — Three Kingdoms Backtest Engine
============================================
Exact match of PineScript v6 state machine logic.
Compound equity (percent_of_equity=100%), flip-only exit.

Usage:
    python backtest_three_kingdoms.py                          # Default BTC 4H
    BT_SYMBOL=ETH_USDT_PERP python backtest_three_kingdoms.py  # ETH
    BT_INTERVAL=4H python backtest_three_kingdoms.py           # 4H
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np

# ── Config ──────────────────────────────────────────
SYMBOL = os.getenv("BT_SYMBOL", "BTC_USDT_PERP")
INTERVAL = os.getenv("BT_INTERVAL", "4H")
INITIAL_CAPITAL = float(os.getenv("BT_CAPITAL", "10000"))
COMMISSION = float(os.getenv("BT_COMM", "0.0"))
SLIPPAGE = float(os.getenv("BT_SLIP", "0.0"))
LEVERAGE = float(os.getenv("BT_LEV", "1"))

# 3 Kingdoms MA params
LIU_BEI = int(os.getenv("BT_LB", "200"))    # Liu Bei — direction
GUAN_YU = int(os.getenv("BT_GY", "50"))     # Guan Yu — attack
ZHANG_FEI = int(os.getenv("BT_ZF", "20"))   # Zhang Fei — confirm

# Signal filter
DISABLE_BOUNCE = os.getenv("BT_NO_BOUNCE", "1") == "1"  # Default: disabled
MA_DIST_PCT = float(os.getenv("BT_DIST", "2"))           # Default: 2%

_INTERVAL_MAP = {"1M":1,"5M":5,"15M":15,"30M":30,"60M":60,"4H":240,"8H":480,"1D":1440}
INTERVAL_MINUTES = _INTERVAL_MAP.get(INTERVAL, 60)


# ── Data ────────────────────────────────────────────

def fetch_klines() -> list[dict]:
    base_url = "https://api.pionex.com"
    all_k = {}
    end_time = None
    for _ in range(40):
        params = {"symbol": SYMBOL, "interval": INTERVAL, "limit": "500"}
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


# ── Strategy — exact PineScript v6 state machine ────

def sma(closes: list[float], period: int) -> float:
    if len(closes) < period:
        return 0.0
    return float(np.mean(closes[-period:]))


# Signal codes: 0=HOLD, 1=MAIN_LONG, 2=MAIN_SHORT, 3=CORR_SHORT, 4=BOUNCE_LONG
SIG_NAMES = {0: "HOLD", 1: "MAIN_LONG", 2: "MAIN_SHORT", 3: "CORR_SHORT", 4: "BOUNCE_LONG"}
SIG_DIR = {0: 0, 1: 1, 2: -1, 3: -1, 4: 1}  # 1=long, -1=short, 0=hold


def calc_raw_signal(price: float, lb: float, gy: float, zf: float) -> int:
    """Calculate raw signal for current bar — matches PineScript exactly."""
    is_bull = price > lb
    is_bear = price < lb

    gy_dist = abs(price - gy) / gy * 100 if gy > 0 else 0
    far_enough = MA_DIST_PCT == 0 or gy_dist >= MA_DIST_PCT

    above_gy = price > gy and far_enough
    below_gy = price < gy and far_enough

    if is_bull and above_gy and price > zf:
        return 1   # MAIN_LONG
    elif is_bear and below_gy and price < zf:
        return 2   # MAIN_SHORT
    elif not DISABLE_BOUNCE and is_bear and above_gy:
        return 4   # BOUNCE_LONG
    elif is_bull and below_gy:
        return 3   # CORR_SHORT
    return 0       # HOLD


# ── Backtest ────────────────────────────────────────

@dataclass
class Trade:
    entry_time: int
    exit_time: int
    direction: str        # "long" | "short"
    entry_price: float
    exit_price: float
    pnl_pct: float        # percent return (no leverage)
    signal_name: str      # MAIN_LONG, MAIN_SHORT, CORR_SHORT, BOUNCE_LONG
    exit_reason: str      # "flip" | "end"
    hold_bars: int


def run_backtest(klines: list[dict]) -> tuple[list[Trade], list[float]]:
    """
    PineScript-identical backtest with compound equity.

    Matches: strategy(percent_of_equity=100%, process_orders_on_close=true, pyramiding=0)
    """
    trades: list[Trade] = []
    closes: list[float] = []
    equity_curve: list[float] = [INITIAL_CAPITAL]

    # ── State machine (matches PineScript var int sigState = 0) ──
    sig_state = 0       # persistent: last non-HOLD signal code

    # ── Position state ──
    pos_dir: str | None = None    # "long" | "short" | None
    pos_entry: float = 0.0
    pos_time: int = 0
    pos_bar: int = 0
    pos_signal: str = ""
    equity = INITIAL_CAPITAL
    is_liquidated = False

    for i, k in enumerate(klines):
        c = float(k["close"])
        closes.append(c)

        if len(closes) < LIU_BEI + 1:
            equity_curve.append(equity)
            continue

        # ── Calculate MAs ──
        lb = sma(closes, LIU_BEI)
        gy = sma(closes, GUAN_YU)
        zf = sma(closes, ZHANG_FEI)

        # ── Raw signal (this bar) ──
        raw_sig = calc_raw_signal(c, lb, gy, zf)
        raw_dir = SIG_DIR[raw_sig]

        # ── Previous direction from sigState ──
        prev_dir = SIG_DIR[sig_state]

        # ── Signal changed? (PineScript: rawDir != 0 and rawDir != prevDir) ──
        sig_changed = raw_dir != 0 and raw_dir != prev_dir

        # ── Update sigState (only on non-HOLD) ──
        if raw_sig != 0:
            sig_state = raw_sig

        # ── Trade execution (flip-only) ──
        enter_long = sig_changed and raw_dir == 1
        enter_short = sig_changed and raw_dir == -1

        # Close existing position and open new one (atomic flip)
        if enter_long or enter_short:
            # Close existing position if any
            if pos_dir:
                if pos_dir == "long":
                    rpct = (c - pos_entry) / pos_entry
                else:
                    rpct = (pos_entry - c) / pos_entry

                # Apply commission and leverage
                rpct -= (COMMISSION + SLIPPAGE) * 2
                leveraged_rpct = rpct * LEVERAGE

                if leveraged_rpct <= -1.0:
                    leveraged_rpct = -1.0
                    is_liquidated = True

                trades.append(Trade(
                    entry_time=pos_time, exit_time=k["time"],
                    direction=pos_dir, entry_price=pos_entry, exit_price=c,
                    pnl_pct=leveraged_rpct * 100,
                    signal_name=pos_signal, exit_reason="flip",
                    hold_bars=i - pos_bar,
                ))

                # Compound equity
                equity *= (1 + leveraged_rpct)
                if is_liquidated:
                    break

            # Open new position
            pos_dir = "long" if enter_long else "short"
            pos_entry = c
            pos_time = k["time"]
            pos_bar = i
            pos_signal = SIG_NAMES[raw_sig]

        # Track equity (unrealized)
        if pos_dir:
            if pos_dir == "long":
                unrealized = (c - pos_entry) / pos_entry
            else:
                unrealized = (pos_entry - c) / pos_entry
            
            leveraged_unrealized = (unrealized - (COMMISSION + SLIPPAGE)) * LEVERAGE
            current_equity = equity * (1 + max(-1.0, leveraged_unrealized))
            equity_curve.append(current_equity)
        else:
            equity_curve.append(equity)

    # Close remaining position at end
    if pos_dir and not is_liquidated:
        c = float(klines[-1]["close"])
        if pos_dir == "long":
            rpct = (c - pos_entry) / pos_entry
        else:
            rpct = (pos_entry - c) / pos_entry
        
        rpct -= (COMMISSION + SLIPPAGE) * 2
        leveraged_rpct = rpct * LEVERAGE
        
        if leveraged_rpct <= -1.0:
            leveraged_rpct = -1.0

        trades.append(Trade(
            entry_time=pos_time, exit_time=klines[-1]["time"],
            direction=pos_dir, entry_price=pos_entry, exit_price=c,
            pnl_pct=leveraged_rpct * 100,
            signal_name=pos_signal, exit_reason="end",
            hold_bars=len(klines) - 1 - pos_bar,
        ))
        equity *= (1 + leveraged_rpct)

    return trades, equity_curve


# ── Report ──────────────────────────────────────────

def ts(ms: int) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%m/%d %H:%M")


def print_report(trades: list[Trade], klines: list[dict], equity_curve: list[float]):
    import datetime

    n = len(trades)
    if n == 0:
        print("  No trades. Data may be insufficient.")
        return

    # Final equity
    final_equity = INITIAL_CAPITAL
    for t in trades:
        final_equity *= (1 + t.pnl_pct / 100)
    total_return = (final_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100

    wins = [t for t in trades if t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct <= 0]
    wr = len(wins) / n * 100
    avg_w = np.mean([t.pnl_pct for t in wins]) if wins else 0
    avg_l = np.mean([t.pnl_pct for t in losses]) if losses else 0

    # Gross win/loss for profit factor
    gw = sum(t.pnl_pct for t in wins)
    gl = abs(sum(t.pnl_pct for t in losses))
    pf = gw / gl if gl > 0 else float("inf")

    best = max(trades, key=lambda t: t.pnl_pct)
    worst = min(trades, key=lambda t: t.pnl_pct)
    avg_hold = np.mean([t.hold_bars for t in trades])

    longs = [t for t in trades if t.direction == "long"]
    shorts = [t for t in trades if t.direction == "short"]

    # Max drawdown from equity curve
    peak = INITIAL_CAPITAL
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    days = (klines[-1]["time"] - klines[0]["time"]) / 86400000

    # Signal type breakdown
    main_trades = [t for t in trades if t.signal_name.startswith("MAIN")]
    bounce_trades = [t for t in trades if t.signal_name == "BOUNCE_LONG"]
    corr_trades = [t for t in trades if t.signal_name == "CORR_SHORT"]

    print()
    print("=" * 70)
    print("  三刀流 v6 — Three Kingdoms Backtest Report")
    print("  LiuBei direction → GuanYu attack → ZhangFei confirm")
    print("=" * 70)
    print(f"  Symbol:    {SYMBOL}")
    print(f"  Period:    {ts(klines[0]['time'])} → {ts(klines[-1]['time'])} ({days:.0f} days)")
    print(f"  Candles:   {len(klines)} ({INTERVAL})")
    print(f"  MA:        LiuBei({LIU_BEI}) / GuanYu({GUAN_YU}) / ZhangFei({ZHANG_FEI})")
    print(f"  DIST:      {MA_DIST_PCT}%  |  BounceL: {'OFF' if DISABLE_BOUNCE else 'ON'}")
    print(f"  Capital:   {INITIAL_CAPITAL:.0f} USDT  |  Mode: compound (percent_of_equity=100%)")
    print(f"  Leverage:  {LEVERAGE}x  |  Commission: {COMMISSION*100:.2f}%  |  Slippage: {SLIPPAGE*100:.2f}%")
    print("=" * 70)

    print()
    print("  [ Performance Overview ]")
    print(f"  Initial → Final:   {INITIAL_CAPITAL:.0f} → {final_equity:.2f} USDT ({total_return:+.2f}%)")
    print(f"  Max Drawdown:      {max_dd*100:.2f}%")
    print(f"  Profit Factor:     {pf:.2f}")

    print()
    print("  [ Trade Statistics ]")
    print(f"  Trades: {n}  |  Win Rate: {wr:.1f}% ({len(wins)}W/{len(losses)}L)")
    print(f"  Avg Win: {avg_w:+.2f}%  |  Avg Loss: {avg_l:+.2f}%")
    print(f"  Best: {best.pnl_pct:+.2f}% ({best.direction} {ts(best.entry_time)})")
    print(f"  Worst: {worst.pnl_pct:+.2f}% ({worst.direction} {ts(worst.entry_time)})")
    print(f"  Avg Hold: {avg_hold:.0f} bars ({avg_hold * INTERVAL_MINUTES / 60:.1f}h)")

    print()
    print("  [ Long/Short Analysis ]")
    for label, group in [("Long", longs), ("Short", shorts)]:
        if group:
            gw_grp = sum(1 for t in group if t.pnl_pct > 0)
            gpnl = 1.0
            for t in group:
                gpnl *= (1 + t.pnl_pct / 100)
            gpnl_pct = (gpnl - 1) * 100
            print(f"  {label}:  {len(group)} trades | WR {gw_grp/len(group)*100:.0f}% | Compound {gpnl_pct:+.2f}%")

    print()
    print("  [ Signal Types ]")
    for label, group in [("Main Trend", main_trades), ("Bounce Long", bounce_trades), ("Correction", corr_trades)]:
        if group:
            gw_grp = sum(1 for t in group if t.pnl_pct > 0)
            print(f"  {label:14s} {len(group):3d} trades | WR {gw_grp/len(group)*100:.0f}% | Avg {np.mean([t.pnl_pct for t in group]):+.2f}%")

    # Trade list
    print()
    sig_labels = {
        "MAIN_LONG": "MainL", "MAIN_SHORT": "MainS",
        "BOUNCE_LONG": "BncL", "CORR_SHORT": "CorrS",
    }
    print(f"  {'#':>3}  {'Signal':>6}  {'Dir':>5}  {'Entry':>12}  {'EntryP':>10}  {'ExitP':>10}  {'%':>8}  {'Exit':>4}  {'Hold':>5}")
    print("  " + "-" * 75)
    for i, t in enumerate(trades, 1):
        d = "LONG" if t.direction == "long" else "SHORT"
        m = "+" if t.pnl_pct > 0 else " "
        sl = sig_labels.get(t.signal_name, t.signal_name[:6])
        el = t.exit_reason
        hrs = t.hold_bars * INTERVAL_MINUTES / 60
        print(
            f"  {i:3d}  {sl:>6}  {d:>5}  {ts(t.entry_time):>12}  "
            f"${t.entry_price:>9.2f}  ${t.exit_price:>9.2f}  "
            f"{m}{t.pnl_pct:>7.2f}%  {el:>4}  {hrs:>4.0f}h"
        )
    print()
    print("=" * 70)

    # Save JSON
    output = {
        "strategy": "three_kingdoms_v6",
        "symbol": SYMBOL, "interval": INTERVAL,
        "ma": {"liu_bei": LIU_BEI, "guan_yu": GUAN_YU, "zhang_fei": ZHANG_FEI},
        "dist_pct": MA_DIST_PCT, "bounce_disabled": DISABLE_BOUNCE,
        "initial_capital": INITIAL_CAPITAL,
        "leverage": LEVERAGE,
        "commission": COMMISSION, "slippage": SLIPPAGE,
        "days": round(days), "candles": len(klines),
        "final_equity": round(final_equity, 2),
        "return_pct": round(total_return, 2),
        "trades": n, "win_rate": round(wr, 1),
        "max_dd_pct": round(max_dd * 100, 2),
        "profit_factor": round(pf, 2),
    }
    out_path = Path(f"backtest_3k_{SYMBOL.split('_')[0].lower()}.json")
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"  Saved: {out_path}")


# ── Main ────────────────────────────────────────────

def main():
    print("三刀流 v6 — Three Kingdoms Backtest")
    print(f"LiuBei({LIU_BEI}) / GuanYu({GUAN_YU}) / ZhangFei({ZHANG_FEI})")
    print(f"DIST={MA_DIST_PCT}% | BounceL={'OFF' if DISABLE_BOUNCE else 'ON'}")
    print("-" * 45)

    print(f"Fetching {SYMBOL} {INTERVAL} klines...")
    klines = fetch_klines()

    if len(klines) < LIU_BEI + 10:
        print(f"ERROR: Insufficient data ({len(klines)} candles, need {LIU_BEI + 10}+)")
        print(f"  Hint: LiuBei needs {LIU_BEI} MA periods")
        sys.exit(1)

    import datetime
    start = datetime.datetime.fromtimestamp(klines[0]["time"] / 1000)
    end = datetime.datetime.fromtimestamp(klines[-1]["time"] / 1000)
    print(f"Loaded {len(klines)} candles: {start} → {end}")

    trades, equity_curve = run_backtest(klines)
    print_report(trades, klines, equity_curve)


if __name__ == "__main__":
    main()
