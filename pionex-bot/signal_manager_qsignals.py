"""Q-SIGNALS parallel signal manager.

Observation-only runner that polls every bot in bots.toml, fetches OHLCV,
and evaluates BOTH the existing MA-Cross strategy AND every applicable
Q-SIGNALS strategy. Emits a side-by-side log + JSONL file for comparison.

**Does NOT place, modify, or cancel any orders.** Purpose is to compare
signal disagreement rates over time before choosing a replacement.

Usage:
    python signal_manager_qsignals.py                # single cycle
    python signal_manager_qsignals.py --loop         # every hour
    python signal_manager_qsignals.py --interval 15  # every 15 min
    python signal_manager_qsignals.py --bot btc      # one bot only
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from qsignals_adapter import evaluate, strategies_for_symbol
from signal_manager import BotAPIClient, setup_logger
from strategy import MACrossStrategy
from utils import load_toml

LOG_DIR = Path(__file__).parent / "state"
JSONL_LOG = LOG_DIR / "qsignals_compare.jsonl"


def fetch_ohlcv(client: BotAPIClient, symbol: str, interval: str, limit: int) -> list[dict]:
    """Fetch klines and return OHLCV dicts (oldest first)."""
    all_k: dict[int, dict] = {}
    end_time = None
    pages = (limit // 500) + 2
    for _ in range(pages):
        params = {"symbol": symbol, "interval": interval, "limit": "500"}
        if end_time:
            params["endTime"] = str(end_time)
        r = client._http.get(f"{client.base_url}/api/v1/market/klines", params=params)
        data = r.json()
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
    out = []
    for k in sorted(all_k.values(), key=lambda x: x["time"]):
        out.append({
            "time": k["time"],
            "open": float(k["open"]),
            "high": float(k["high"]),
            "low": float(k["low"]),
            "close": float(k["close"]),
            "volume": float(k.get("volume", 0)),
        })
    return out[-limit:]


def ma_cross_signal(closes: list[float]) -> str:
    """Run the incumbent MA Cross strategy and return HOLD/BUY/SELL."""
    strat = MACrossStrategy()
    # MACrossStrategy.evaluate takes list of kline dicts; pass stub
    klines = [{"close": c} for c in closes]
    sig = strat.evaluate(klines)
    return sig.value if hasattr(sig, "value") else str(sig)


def symbol_to_spot(symbol: str) -> str:
    """Map Pionex perp symbol to Q-SIGNALS OPTIMIZED_PARAMS key.

    Examples: BTC_USDT_PERP → BTCUSDT, XAUT_USDT_PERP → XAUUSDT
    """
    base = symbol.replace("_USDT_PERP", "").replace("_PERP", "").replace("_USDT", "")
    # Normalise a few tickers Q-SIGNALS uses
    alias = {"XAUT": "XAU", "USOX": "CL", "WTI": "CL", "PAXG": "PAXG"}
    return f"{alias.get(base, base)}USDT"


def process_bot(client: BotAPIClient, name: str, bot_cfg: dict, log) -> dict:
    symbol = bot_cfg["symbol"]
    interval = bot_cfg.get("interval", "60M")   # Pionex: 5M/15M/30M/60M/4H/1D
    limit = 300  # enough for 200-period indicators
    # Pionex → Q-SIGNALS timeframe key
    tf_map = {"5M": "5m", "15M": "15m", "30M": "30m",
              "60M": "1h", "4H": "4h", "1D": "1d"}
    timeframe = tf_map.get(interval, "1h")
    qs_symbol = symbol_to_spot(symbol)

    try:
        candles = fetch_ohlcv(client, symbol, interval, limit)
    except Exception as e:
        log.error("[%s] fetch failed: %s", name.upper(), e)
        return {"bot": name, "error": f"fetch: {e}"}

    if len(candles) < 100:
        log.warning("[%s] only %d candles — skipping", name.upper(), len(candles))
        return {"bot": name, "error": "insufficient candles"}

    closes = [c["close"] for c in candles]
    ma_sig = ma_cross_signal(closes)
    price = closes[-1]

    qs_signals = {}
    for sid in strategies_for_symbol(qs_symbol):
        try:
            res = evaluate(sid, qs_symbol, timeframe, candles, timeout=25)
            qs_signals[sid] = res["signal"]
        except Exception as e:
            qs_signals[sid] = f"ERR:{str(e)[:40]}"

    # Agreement scoring — count how many Q-SIGNALS agree with MA-Cross
    dir_map = {"BUY": "LONG", "CLOSE_SHORT": "LONG",
               "SELL": "SHORT", "CLOSE_LONG": "SHORT",
               "HOLD": "FLAT"}
    ma_dir = dir_map.get(ma_sig, "FLAT")
    qs_dirs = {sid: dir_map.get(s, "FLAT") for sid, s in qs_signals.items() if not s.startswith("ERR")}
    agree = sum(1 for d in qs_dirs.values() if d == ma_dir)
    total = len(qs_dirs)
    consensus = max(set(qs_dirs.values()), key=list(qs_dirs.values()).count) if qs_dirs else "FLAT"

    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "bot": name,
        "symbol": symbol,
        "qs_symbol": qs_symbol,
        "tf": timeframe,
        "price": price,
        "ma_cross": ma_sig,
        "ma_dir": ma_dir,
        "qsignals": qs_signals,
        "qs_consensus": consensus,
        "agree": agree,
        "total": total,
    }

    # Console summary
    flag = "✓" if ma_dir == consensus else "✗"
    log.info(
        "[%s] %s %s  MA=%s  QS-consensus=%s (%d/%d agree)  %s",
        name.upper(), symbol, f"${price:,.4f}",
        ma_sig, consensus, agree, total, flag,
    )
    # Per-strategy details at DEBUG level
    for sid, s in qs_signals.items():
        log.debug("   %s=%s", sid, s)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=int, default=60, help="Loop interval minutes")
    ap.add_argument("--bot", type=str)
    ap.add_argument("--config", type=str, default="bots.toml")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    cfg = load_toml(str(Path(__file__).parent / args.config))
    bots_cfg = cfg.get("bots", {})
    log = setup_logger("DEBUG" if args.verbose else "INFO")
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    log.info("=" * 60)
    log.info("Q-SIGNALS Parallel Compare — OBSERVATION ONLY")
    log.info("Bots: %d | JSONL: %s", len(bots_cfg), JSONL_LOG)
    log.info("=" * 60)

    def cycle():
        client = BotAPIClient()
        log.info("--- Cycle: %s ---", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
        agg = {"runs": 0, "agree": 0, "total": 0}
        with open(JSONL_LOG, "a", encoding="utf-8") as f:
            for name, bcfg in bots_cfg.items():
                if args.bot and name != args.bot:
                    continue
                rec = process_bot(client, name, bcfg, log)
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                if "agree" in rec:
                    agg["runs"] += 1
                    agg["agree"] += rec["agree"]
                    agg["total"] += rec["total"]
        if agg["total"]:
            rate = agg["agree"] / agg["total"] * 100
            log.info("Agreement this cycle: %.1f%% (%d/%d strategy votes, %d bots)",
                     rate, agg["agree"], agg["total"], agg["runs"])

    if args.loop:
        while True:
            try:
                cycle()
            except KeyboardInterrupt:
                log.info("Stopped by user"); break
            except Exception as e:
                log.error("Cycle failed: %s", e, exc_info=True)
            log.info("Sleeping %d min...", args.interval)
            time.sleep(args.interval * 60)
    else:
        cycle()


if __name__ == "__main__":
    main()
