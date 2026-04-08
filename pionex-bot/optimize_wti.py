"""
三刀流 參數優化器 v2 — WTI_USDT_PERP
======================================
新增：硬止損 + 移動停利 (Trailing Take-Profit)

Usage:
    python optimize_wti.py
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from itertools import product
from pathlib import Path

import httpx
import numpy as np

# ── Fixed Config ────────────────────────────────────
SYMBOL = "WTI_USDT_PERP"
INVESTMENT = 50.0
COMMISSION = 0.0005
SLIPPAGE = 0.001

# ── Parameter Search Space ──────────────────────────
PARAM_GRID = {
    "interval": ["60M", "4H"],
    "ma_fast": [5, 7, 10, 14],
    "ma_mid": [20, 25, 30],
    "ma_slow": [50, 60, 80, 99],
    "min_strength": [2, 3],
    "leverage": [2, 3, 5],
    "stop_loss_pct": [8, 10, 15, 20],          # 硬止損 %
    "trailing_start_pct": [0, 10, 15, 20],     # 移動停利啟動門檻 % (0=off)
    "trailing_step_pct": [5, 8, 10],           # 移動停利回撤容忍 %
}


# ── Data Fetching ───────────────────────────────────

def fetch_klines(interval: str) -> list[dict]:
    base_url = "https://api.pionex.com"
    all_klines = {}
    end_time = None

    for _ in range(6):
        params = {"symbol": SYMBOL, "interval": interval, "limit": "500"}
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
                all_klines[k["time"]] = k
            oldest = klines[-1]["time"]
            if end_time == oldest:
                break
            end_time = oldest
        except Exception:
            break

    return sorted(all_klines.values(), key=lambda x: x["time"])


# ── Strategy ────────────────────────────────────────

def sma(closes: list[float], period: int) -> float:
    if len(closes) < period:
        return 0.0
    return float(np.mean(closes[-period:]))


def evaluate(closes: list[float], fast: int, mid: int, slow: int) -> tuple[str, int]:
    if len(closes) < slow + 1:
        return ("no_trend", 0)

    mf, mm, ms = sma(closes, fast), sma(closes, mid), sma(closes, slow)
    fam, mas, fas = mf > mm, mm > ms, mf > ms
    bull = sum([fam, mas, fas])
    bear = 3 - bull

    if bull == 3:
        return ("long", 3)
    if bull == 2 and fam:
        return ("long", 2)
    if fam and bear >= 2:
        return ("long", 1)
    if bear == 3:
        return ("short", 3)
    if bear == 2 and not fam:
        return ("short", 2)
    if not fam and bull >= 2:
        return ("short", 1)
    return ("no_trend", 0)


# ── Backtest Engine ─────────────────────────────────

@dataclass
class Result:
    total_pnl: float = 0.0
    trades: int = 0
    wins: int = 0
    max_dd: float = 0.0
    sharpe: float = 0.0
    profit_factor: float = 0.0
    avg_pnl: float = 0.0
    win_rate: float = 0.0
    sl_exits: int = 0
    trail_exits: int = 0
    signal_exits: int = 0


def run_backtest(
    klines: list[dict],
    ma_fast: int, ma_mid: int, ma_slow: int,
    min_strength: int, leverage: int,
    stop_loss_pct: float,
    trailing_start_pct: float,
    trailing_step_pct: float,
) -> Result:
    result = Result()
    balance = 500.0
    peak_bal = balance
    returns = []

    # Position state
    active_trend: str | None = None
    entry_price: float = 0.0
    peak_unreal_pct: float = 0.0  # peak unrealized P&L % for trailing stop
    closes: list[float] = []

    for k in klines:
        c = float(k["close"])
        closes.append(c)

        if len(closes) < ma_slow + 2:
            continue

        trend, strength = evaluate(closes, ma_fast, ma_mid, ma_slow)

        if active_trend:
            # Calculate unrealized P&L %
            if active_trend == "long":
                unreal_pct = (c - entry_price) / entry_price * leverage * 100
            else:
                unreal_pct = (entry_price - c) / entry_price * leverage * 100

            # Track peak unrealized for trailing stop
            if unreal_pct > peak_unreal_pct:
                peak_unreal_pct = unreal_pct

            # ── Exit conditions ──
            exit_reason = None

            # 1. Hard stop loss
            if unreal_pct <= -stop_loss_pct:
                exit_reason = "sl"

            # 2. Trailing take-profit
            if trailing_start_pct > 0 and peak_unreal_pct >= trailing_start_pct:
                drawback = peak_unreal_pct - unreal_pct
                if drawback >= trailing_step_pct:
                    exit_reason = "trail"

            # 3. Signal reversal
            if exit_reason is None:
                if active_trend == "long" and trend == "short" and strength >= min_strength:
                    exit_reason = "signal"
                elif active_trend == "short" and trend == "long" and strength >= min_strength:
                    exit_reason = "signal"

            if exit_reason:
                xp = c * (1 + SLIPPAGE if active_trend == "short" else 1 - SLIPPAGE)
                if active_trend == "long":
                    rpct = (xp - entry_price) / entry_price
                else:
                    rpct = (entry_price - xp) / entry_price
                lpct = rpct * leverage
                comm = INVESTMENT * leverage * COMMISSION * 2
                pnl = INVESTMENT * lpct - comm

                balance += pnl
                returns.append(pnl)
                result.total_pnl += pnl
                result.trades += 1
                if pnl > 0:
                    result.wins += 1
                if exit_reason == "sl":
                    result.sl_exits += 1
                elif exit_reason == "trail":
                    result.trail_exits += 1
                else:
                    result.signal_exits += 1

                if balance > peak_bal:
                    peak_bal = balance
                dd = (peak_bal - balance) / peak_bal if peak_bal > 0 else 0
                if dd > result.max_dd:
                    result.max_dd = dd

                active_trend = None
                entry_price = 0.0
                peak_unreal_pct = 0.0

        # ── Open position ──
        if not active_trend and strength >= min_strength and trend != "no_trend":
            entry_price = c * (1 + SLIPPAGE if trend == "long" else 1 - SLIPPAGE)
            active_trend = trend
            peak_unreal_pct = 0.0

    # Close remaining position
    if active_trend:
        c = float(klines[-1]["close"])
        if active_trend == "long":
            rpct = (c - entry_price) / entry_price
        else:
            rpct = (entry_price - c) / entry_price
        lpct = rpct * leverage
        comm = INVESTMENT * leverage * COMMISSION * 2
        pnl = INVESTMENT * lpct - comm
        balance += pnl
        returns.append(pnl)
        result.total_pnl += pnl
        result.trades += 1
        if pnl > 0:
            result.wins += 1
        result.signal_exits += 1

    # Metrics
    if returns:
        result.avg_pnl = float(np.mean(returns))
        std_r = float(np.std(returns))
        result.sharpe = result.avg_pnl / std_r * np.sqrt(len(returns)) if std_r > 0 else 0
        gross_win = sum(r for r in returns if r > 0)
        gross_loss = abs(sum(r for r in returns if r < 0))
        result.profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")
    if result.trades > 0:
        result.win_rate = result.wins / result.trades * 100

    return result


# ── Scoring ─────────────────────────────────────────

def score(params: dict, r: Result) -> float:
    """Composite score: profit * factor / drawdown penalty."""
    if r.total_pnl <= 0:
        return r.total_pnl  # negative stays negative
    dd_penalty = 1 + r.max_dd * 10
    return r.total_pnl * r.profit_factor / dd_penalty


# ── Main ────────────────────────────────────────────

def main():
    print("=" * 80)
    print("  三刀流 參數優化器 v2 — WTI 原油")
    print("  (硬止損 + 移動停利 Trailing TP)")
    print("=" * 80)

    # Fetch data
    print("\n  Fetching market data...")
    data_cache = {}
    for interval in PARAM_GRID["interval"]:
        print(f"    {interval}...", end=" ", flush=True)
        klines = fetch_klines(interval)
        data_cache[interval] = klines
        print(f"{len(klines)} candles")

    # Generate combinations
    keys = list(PARAM_GRID.keys())
    values = list(PARAM_GRID.values())
    combos = list(product(*values))
    total = len(combos)

    print(f"\n  Testing {total} parameter combinations...\n")

    results = []
    skipped = 0

    for idx, combo in enumerate(combos):
        params = dict(zip(keys, combo))

        # Skip invalid
        if params["ma_fast"] >= params["ma_mid"] or params["ma_mid"] >= params["ma_slow"]:
            skipped += 1
            continue
        # If trailing off, skip duplicate trailing_step combos
        if params["trailing_start_pct"] == 0 and params["trailing_step_pct"] != 5:
            skipped += 1
            continue

        klines = data_cache[params["interval"]]
        if len(klines) < params["ma_slow"] + 10:
            skipped += 1
            continue

        r = run_backtest(
            klines,
            ma_fast=params["ma_fast"], ma_mid=params["ma_mid"], ma_slow=params["ma_slow"],
            min_strength=params["min_strength"], leverage=params["leverage"],
            stop_loss_pct=params["stop_loss_pct"],
            trailing_start_pct=params["trailing_start_pct"],
            trailing_step_pct=params["trailing_step_pct"],
        )

        if r.trades >= 3:
            results.append((params, r))

        if (idx + 1) % 500 == 0:
            print(f"    Progress: {idx+1}/{total} ({len(results)} valid)", flush=True)

    print(f"\n  Completed: {len(results)} valid / {total - skipped} tested / {skipped} skipped")

    if not results:
        print("  No valid results!")
        sys.exit(1)

    results.sort(key=lambda x: score(x[0], x[1]), reverse=True)

    # ── Top 20 ──
    print("\n" + "=" * 140)
    print(f"  {'#':>2}  {'Int':>3}  {'MA':>10}  {'Str':>3}  {'Lev':>3}  "
          f"{'SL%':>4}  {'Trail':>8}  {'Step':>4}  "
          f"{'Trades':>6}  {'WR%':>5}  {'PnL':>9}  {'DD%':>5}  {'PF':>5}  "
          f"{'Sharpe':>6}  {'SL':>3}  {'Tr':>3}  {'Sig':>3}  {'Score':>8}")
    print("  " + "-" * 136)

    for rank, (p, r) in enumerate(results[:20], 1):
        ma = f"{p['ma_fast']}/{p['ma_mid']}/{p['ma_slow']}"
        trail = f"{p['trailing_start_pct']}%" if p['trailing_start_pct'] > 0 else "OFF"
        step = f"{p['trailing_step_pct']}%" if p['trailing_start_pct'] > 0 else "-"
        s = score(p, r)
        print(
            f"  {rank:2d}  {p['interval']:>3}  {ma:>10}  {p['min_strength']:>3}  {p['leverage']:>2}x  "
            f"{p['stop_loss_pct']:>3}%  {trail:>8}  {step:>4}  "
            f"{r.trades:>6}  {r.win_rate:>4.0f}%  {r.total_pnl:>+8.2f}  {r.max_dd*100:>4.1f}%  {r.profit_factor:>5.2f}  "
            f"{r.sharpe:>6.2f}  {r.sl_exits:>3}  {r.trail_exits:>3}  {r.signal_exits:>3}  {s:>8.2f}"
        )

    # ── Best detail ──
    bp, br = results[0]
    print("\n" + "=" * 80)
    print("  BEST PARAMETERS")
    print("=" * 80)
    print(f"    Interval:          {bp['interval']}")
    print(f"    MA Periods:        {bp['ma_fast']} / {bp['ma_mid']} / {bp['ma_slow']}")
    print(f"    Min Strength:      {bp['min_strength']}")
    print(f"    Leverage:          {bp['leverage']}x")
    print(f"    Hard Stop Loss:    {bp['stop_loss_pct']}%")
    if bp['trailing_start_pct'] > 0:
        print(f"    Trailing TP Start: {bp['trailing_start_pct']}%")
        print(f"    Trailing TP Step:  {bp['trailing_step_pct']}%")
    else:
        print(f"    Trailing TP:       OFF")
    print(f"    ────────────────────────")
    print(f"    Total PnL:         {br.total_pnl:+.2f} USDT ({br.total_pnl/500*100:+.1f}%)")
    print(f"    Trades:            {br.trades}")
    print(f"    Win Rate:          {br.win_rate:.1f}%")
    print(f"    Max Drawdown:      {br.max_dd*100:.1f}%")
    print(f"    Profit Factor:     {br.profit_factor:.2f}")
    print(f"    Sharpe:            {br.sharpe:.2f}")
    print(f"    Exits - SL: {br.sl_exits}  Trail: {br.trail_exits}  Signal: {br.signal_exits}")
    print("=" * 80)

    # ── Trailing vs No-Trailing comparison ──
    trail_on = [x for x in results if x[0]["trailing_start_pct"] > 0 and x[1].total_pnl > 0]
    trail_off = [x for x in results if x[0]["trailing_start_pct"] == 0 and x[1].total_pnl > 0]
    print(f"\n  Trailing TP comparison:")
    print(f"    With trailing:    {len(trail_on)} profitable combos, best PnL {trail_on[0][1].total_pnl:+.2f}" if trail_on else "    With trailing:    0 profitable")
    print(f"    Without trailing: {len(trail_off)} profitable combos, best PnL {trail_off[0][1].total_pnl:+.2f}" if trail_off else "    Without trailing: 0 profitable")

    # Save
    output = []
    for p, r in results[:20]:
        output.append({
            "params": p,
            "pnl": round(r.total_pnl, 2),
            "trades": r.trades,
            "win_rate": round(r.win_rate, 1),
            "max_dd": round(r.max_dd * 100, 1),
            "profit_factor": round(r.profit_factor, 2),
            "sharpe": round(r.sharpe, 2),
            "exits": {"sl": r.sl_exits, "trail": r.trail_exits, "signal": r.signal_exits},
        })
    out_path = Path("optimize_wti_result.json")
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"\n  Top 20 saved to: {out_path}")


if __name__ == "__main__":
    main()
