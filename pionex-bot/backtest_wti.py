"""
三刀流 (Triple Blade) 回測引擎 — WTI_USDT_PERP
================================================
MA(7) / MA(25) / MA(99) 策略回測，模擬網格機器人開倉/平倉。

Usage:
    python backtest_wti.py
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# ── Config ──────────────────────────────────────────
SYMBOL = "WTI_USDT_PERP"
INTERVAL = os.getenv("BT_INTERVAL", "1H")
MA_FAST = 7
MA_MID = 25
MA_SLOW = 99
MIN_STRENGTH = 2          # 最低信號強度才開倉
LEVERAGE = 5
INVESTMENT = 50.0         # USDT per trade
RANGE_PCT = 10.0          # ±% grid range (用於估算 grid profit)
GRID_COUNT = 10
COMMISSION = 0.0005       # 0.05% taker fee (Pionex futures)
SLIPPAGE = 0.001          # 0.1% slippage estimate

# Interval → minutes mapping
_INTERVAL_MAP = {"1M": 1, "5M": 5, "15M": 15, "30M": 30, "60M": 60, "4H": 240, "8H": 480, "1D": 1440}
INTERVAL_MINUTES = _INTERVAL_MAP.get(INTERVAL, 60)


# ── Data Loading ────────────────────────────────────

def load_klines_from_files() -> list[dict]:
    """Load and merge kline data from saved API responses."""
    base = Path(r"C:\Users\User\.claude\projects\c--Users-User-Downloads-autobots\f8f8eaf8-fc8b-4c96-9372-289d6a7dc6b8\tool-results")

    files = [
        "toolu_012gP2CiReKzQ4Rqc9LxGE7F.txt",   # batch 1 (newest)
        "toolu_013W2HnU4FemYMnpYa33Hcn2.txt",   # batch 2
        "toolu_01W9mLLHLhuid24VY3rM84RF.txt",   # batch 3
    ]

    all_klines = {}
    for fname in files:
        fpath = base / fname
        if not fpath.exists():
            continue
        with open(fpath, "r") as f:
            data = json.load(f)
        klines = data.get("data", {}).get("data", {}).get("klines", [])
        for k in klines:
            ts = k["time"]
            if ts not in all_klines:
                all_klines[ts] = k

    # Also load the inline batch 4 data
    batch4_file = base / "mcp-pionex-trade-pionex_market_get_klines-batch4.txt"
    if batch4_file.exists():
        with open(batch4_file, "r") as f:
            data = json.load(f)
        klines = data.get("data", {}).get("data", {}).get("klines", [])
        for k in klines:
            ts = k["time"]
            if ts not in all_klines:
                all_klines[ts] = k

    # Sort by time ascending
    sorted_klines = sorted(all_klines.values(), key=lambda x: x["time"])
    return sorted_klines


def load_klines_from_api() -> list[dict]:
    """Fetch klines directly from Pionex API."""
    import httpx

    base_url = "https://api.pionex.com"
    all_klines = {}
    end_time = None

    for batch in range(6):  # ~6 batches × 500 = ~3000 candles ≈ 31 days
        params = {"symbol": SYMBOL, "interval": INTERVAL, "limit": "500"}
        if end_time:
            params["endTime"] = str(end_time)

        try:
            resp = httpx.get(f"{base_url}/api/v1/market/klines", params=params, timeout=15)
            data = resp.json()
            if not data.get("result"):
                print(f"  API error at batch {batch}: {data.get('message')}")
                break
            klines = data.get("data", {}).get("klines", [])
            if not klines:
                break
            for k in klines:
                ts = k["time"]
                if ts not in all_klines:
                    all_klines[ts] = k
            end_time = klines[-1]["time"]
            print(f"  Batch {batch+1}: {len(klines)} candles fetched")
        except Exception as e:
            print(f"  Fetch error: {e}")
            break

    sorted_klines = sorted(all_klines.values(), key=lambda x: x["time"])
    return sorted_klines


# ── Strategy ────────────────────────────────────────

def sma(closes: list[float], period: int) -> float:
    if len(closes) < period:
        return 0.0
    return float(np.mean(closes[-period:]))


@dataclass
class Signal:
    name: str        # STRONG_LONG, MEDIUM_LONG, etc.
    trend: str       # "long", "short", "no_trend"
    strength: int    # 0-3


def evaluate_signal(closes: list[float]) -> Signal:
    """三刀流 signal evaluation."""
    if len(closes) < MA_SLOW + 1:
        return Signal("HOLD", "no_trend", 0)

    ma_fast = sma(closes, MA_FAST)
    ma_mid = sma(closes, MA_MID)
    ma_slow = sma(closes, MA_SLOW)

    fast_above_mid = ma_fast > ma_mid
    mid_above_slow = ma_mid > ma_slow
    fast_above_slow = ma_fast > ma_slow

    bull = sum([fast_above_mid, mid_above_slow, fast_above_slow])
    bear = 3 - bull

    if bull == 3:
        return Signal("STRONG_LONG", "long", 3)
    elif bull == 2 and fast_above_mid:
        return Signal("MEDIUM_LONG", "long", 2)
    elif fast_above_mid and bear >= 2:
        return Signal("WEAK_LONG", "long", 1)
    elif bear == 3:
        return Signal("STRONG_SHORT", "short", 3)
    elif bear == 2 and not fast_above_mid:
        return Signal("MEDIUM_SHORT", "short", 2)
    elif not fast_above_mid and bull >= 2:
        return Signal("WEAK_SHORT", "short", 1)
    else:
        return Signal("HOLD", "no_trend", 0)


# ── Backtest Engine ─────────────────────────────────

@dataclass
class Trade:
    entry_time: int
    exit_time: int
    direction: str       # "long" or "short"
    entry_price: float
    exit_price: float
    size_usdt: float
    leverage: int
    pnl: float
    pnl_pct: float
    signal_strength: int
    hold_bars: int


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    total_pnl: float = 0.0
    win_count: int = 0
    loss_count: int = 0
    max_drawdown: float = 0.0
    peak_balance: float = 0.0
    equity_curve: list[float] = field(default_factory=list)


def run_backtest(klines: list[dict]) -> BacktestResult:
    """Run triple blade backtest on kline data."""
    result = BacktestResult()
    balance = INVESTMENT * 10  # Starting balance: 500 USDT
    initial_balance = balance
    peak = balance

    # State
    active_trend: str | None = None
    entry_price: float = 0.0
    entry_time: int = 0
    entry_strength: int = 0
    entry_bar: int = 0

    closes: list[float] = []
    prev_signal = Signal("HOLD", "no_trend", 0)

    for i, k in enumerate(klines):
        close = float(k["close"])
        closes.append(close)

        if len(closes) < MA_SLOW + 2:
            result.equity_curve.append(balance)
            continue

        signal = evaluate_signal(closes)

        # ── Close position logic ──
        should_close = False
        if active_trend:
            # Close on reversal signal
            if active_trend == "long" and signal.trend == "short" and signal.strength >= MIN_STRENGTH:
                should_close = True
            elif active_trend == "short" and signal.trend == "long" and signal.strength >= MIN_STRENGTH:
                should_close = True

        if should_close and active_trend:
            # Calculate P&L
            exit_price = close * (1 + SLIPPAGE if active_trend == "short" else 1 - SLIPPAGE)
            if active_trend == "long":
                raw_pnl_pct = (exit_price - entry_price) / entry_price
            else:
                raw_pnl_pct = (entry_price - exit_price) / entry_price

            leveraged_pnl_pct = raw_pnl_pct * LEVERAGE
            commission_cost = INVESTMENT * LEVERAGE * COMMISSION * 2  # entry + exit
            raw_pnl = INVESTMENT * leveraged_pnl_pct - commission_cost

            trade = Trade(
                entry_time=entry_time,
                exit_time=k["time"],
                direction=active_trend,
                entry_price=entry_price,
                exit_price=exit_price,
                size_usdt=INVESTMENT,
                leverage=LEVERAGE,
                pnl=raw_pnl,
                pnl_pct=leveraged_pnl_pct * 100,
                signal_strength=entry_strength,
                hold_bars=i - entry_bar,
            )
            result.trades.append(trade)
            balance += raw_pnl
            result.total_pnl += raw_pnl

            if raw_pnl > 0:
                result.win_count += 1
            else:
                result.loss_count += 1

            # Reset
            active_trend = None
            entry_price = 0
            entry_time = 0

        # ── Open position logic ──
        if not active_trend and signal.strength >= MIN_STRENGTH and signal.trend != "no_trend":
            active_trend = signal.trend
            entry_price = close * (1 + SLIPPAGE if signal.trend == "long" else 1 - SLIPPAGE)
            entry_time = k["time"]
            entry_strength = signal.strength
            entry_bar = i

        # Track equity
        if peak < balance:
            peak = balance
        dd = (peak - balance) / peak if peak > 0 else 0
        if dd > result.max_drawdown:
            result.max_drawdown = dd

        result.equity_curve.append(balance)
        result.peak_balance = peak

    # Close any remaining position at last price
    if active_trend:
        last_close = float(klines[-1]["close"])
        if active_trend == "long":
            raw_pnl_pct = (last_close - entry_price) / entry_price
        else:
            raw_pnl_pct = (entry_price - last_close) / entry_price
        leveraged_pnl_pct = raw_pnl_pct * LEVERAGE
        commission_cost = INVESTMENT * LEVERAGE * COMMISSION * 2
        raw_pnl = INVESTMENT * leveraged_pnl_pct - commission_cost

        trade = Trade(
            entry_time=entry_time,
            exit_time=klines[-1]["time"],
            direction=active_trend,
            entry_price=entry_price,
            exit_price=last_close,
            size_usdt=INVESTMENT,
            leverage=LEVERAGE,
            pnl=raw_pnl,
            pnl_pct=leveraged_pnl_pct * 100,
            signal_strength=entry_strength,
            hold_bars=len(klines) - entry_bar,
        )
        result.trades.append(trade)
        balance += raw_pnl
        result.total_pnl += raw_pnl
        if raw_pnl > 0:
            result.win_count += 1
        else:
            result.loss_count += 1

    return result


# ── Report ──────────────────────────────────────────

def ts_to_str(ts_ms: int) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(ts_ms / 1000).strftime("%m/%d %H:%M")


def print_report(result: BacktestResult, klines: list[dict]):
    total_trades = len(result.trades)
    if total_trades == 0:
        print("No trades executed.")
        return

    win_rate = result.win_count / total_trades * 100
    avg_pnl = result.total_pnl / total_trades
    winners = [t for t in result.trades if t.pnl > 0]
    losers = [t for t in result.trades if t.pnl <= 0]
    avg_win = np.mean([t.pnl for t in winners]) if winners else 0
    avg_loss = np.mean([t.pnl for t in losers]) if losers else 0
    profit_factor = abs(sum(t.pnl for t in winners) / sum(t.pnl for t in losers)) if losers and sum(t.pnl for t in losers) != 0 else float("inf")
    best_trade = max(result.trades, key=lambda t: t.pnl)
    worst_trade = min(result.trades, key=lambda t: t.pnl)
    avg_hold = np.mean([t.hold_bars for t in result.trades])

    long_trades = [t for t in result.trades if t.direction == "long"]
    short_trades = [t for t in result.trades if t.direction == "short"]
    long_pnl = sum(t.pnl for t in long_trades)
    short_pnl = sum(t.pnl for t in short_trades)
    long_wins = sum(1 for t in long_trades if t.pnl > 0)
    short_wins = sum(1 for t in short_trades if t.pnl > 0)

    initial_balance = INVESTMENT * 10
    final_balance = initial_balance + result.total_pnl
    total_return = result.total_pnl / initial_balance * 100

    # Date range
    first_ts = ts_to_str(klines[0]["time"])
    last_ts = ts_to_str(klines[-1]["time"])
    days = (klines[-1]["time"] - klines[0]["time"]) / 86400000

    print()
    print("=" * 65)
    print("  三刀流 (Triple Blade) 回測報告 — WTI 原油")
    print("=" * 65)
    print(f"  交易對:        {SYMBOL}")
    print(f"  時間範圍:      {first_ts} → {last_ts} ({days:.1f} 天)")
    print(f"  K線數量:       {len(klines)} 根 ({INTERVAL})")
    print(f"  MA 參數:       {MA_FAST} / {MA_MID} / {MA_SLOW}")
    print(f"  槓桿:          {LEVERAGE}x")
    print(f"  每筆投入:      {INVESTMENT} USDT")
    print(f"  最低信號強度:  {MIN_STRENGTH}")
    print("=" * 65)

    print()
    print("  [ 績效總覽 ]")
    print(f"  初始資金:      {initial_balance:.2f} USDT")
    print(f"  最終資金:      {final_balance:.2f} USDT")
    print(f"  總損益:        {result.total_pnl:+.2f} USDT ({total_return:+.1f}%)")
    print(f"  最大回撤:      {result.max_drawdown*100:.1f}%")
    print(f"  Profit Factor: {profit_factor:.2f}")

    print()
    print("  [ 交易統計 ]")
    print(f"  總交易數:      {total_trades}")
    print(f"  勝率:          {win_rate:.1f}% ({result.win_count}W / {result.loss_count}L)")
    print(f"  平均損益:      {avg_pnl:+.2f} USDT")
    print(f"  平均獲利:      {avg_win:+.2f} USDT")
    print(f"  平均虧損:      {avg_loss:+.2f} USDT")
    print(f"  最佳交易:      {best_trade.pnl:+.2f} USDT ({best_trade.direction} {ts_to_str(best_trade.entry_time)})")
    print(f"  最差交易:      {worst_trade.pnl:+.2f} USDT ({worst_trade.direction} {ts_to_str(worst_trade.entry_time)})")
    print(f"  平均持倉:      {avg_hold:.0f} 根 K線 ({avg_hold * INTERVAL_MINUTES / 60:.1f} 小時)")

    print()
    print("  [ 多空分析 ]")
    print(f"  做多: {len(long_trades)} 筆 | 勝率 {long_wins/len(long_trades)*100:.0f}% | PnL {long_pnl:+.2f} USDT" if long_trades else "  做多: 0 筆")
    print(f"  做空: {len(short_trades)} 筆 | 勝率 {short_wins/len(short_trades)*100:.0f}% | PnL {short_pnl:+.2f} USDT" if short_trades else "  做空: 0 筆")

    # Signal strength breakdown
    print()
    print("  [ 信號強度分析 ]")
    for s in [2, 3]:
        st = [t for t in result.trades if t.signal_strength == s]
        if st:
            sw = sum(1 for t in st if t.pnl > 0)
            sp = sum(t.pnl for t in st)
            print(f"  強度 {s}: {len(st)} 筆 | 勝率 {sw/len(st)*100:.0f}% | PnL {sp:+.2f} USDT")

    print()
    print("  [ 交易明細 ]")
    print(f"  {'#':>3}  {'方向':>4}  {'開倉時間':>12}  {'入場價':>8}  {'出場價':>8}  {'損益':>9}  {'損益%':>7}  {'強度':>2}  {'持倉':>4}")
    print("  " + "-" * 80)
    for i, t in enumerate(result.trades, 1):
        direction = "LONG" if t.direction == "long" else "SHORT"
        marker = "+" if t.pnl > 0 else " "
        print(
            f"  {i:3d}  {direction:>5}  {ts_to_str(t.entry_time):>12}  "
            f"${t.entry_price:>7.2f}  ${t.exit_price:>7.2f}  "
            f"{marker}{t.pnl:>8.2f}  {t.pnl_pct:>6.1f}%  "
            f"  {t.signal_strength}  {t.hold_bars:>4}"
        )

    print()
    print("=" * 65)

    # Save result to JSON
    output = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "ma": [MA_FAST, MA_MID, MA_SLOW],
        "leverage": LEVERAGE,
        "investment": INVESTMENT,
        "days": round(days, 1),
        "candles": len(klines),
        "total_trades": total_trades,
        "win_rate": round(win_rate, 1),
        "total_pnl": round(result.total_pnl, 2),
        "total_return_pct": round(total_return, 1),
        "max_drawdown_pct": round(result.max_drawdown * 100, 1),
        "profit_factor": round(profit_factor, 2),
        "avg_hold_hours": round(avg_hold * INTERVAL_MINUTES / 60, 1),
        "trades": [
            {
                "direction": t.direction,
                "entry_time": ts_to_str(t.entry_time),
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "pnl": round(t.pnl, 2),
                "pnl_pct": round(t.pnl_pct, 1),
                "strength": t.signal_strength,
                "hold_bars": t.hold_bars,
            }
            for t in result.trades
        ],
    }
    out_path = Path("backtest_wti_result.json")
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"  結果已儲存: {out_path}")
    print()


# ── Main ────────────────────────────────────────────

def main():
    print("三刀流 WTI 原油回測引擎")
    print("-" * 40)

    print(f"Fetching {SYMBOL} {INTERVAL} klines from Pionex API...")
    klines = load_klines_from_api()

    if len(klines) < MA_SLOW + 10:
        print(f"ERROR: Not enough data ({len(klines)} candles, need {MA_SLOW + 10})")
        sys.exit(1)

    import datetime
    start = datetime.datetime.fromtimestamp(klines[0]["time"] / 1000)
    end = datetime.datetime.fromtimestamp(klines[-1]["time"] / 1000)
    print(f"Loaded {len(klines)} candles: {start} → {end}")

    result = run_backtest(klines)
    print_report(result, klines)


if __name__ == "__main__":
    main()
