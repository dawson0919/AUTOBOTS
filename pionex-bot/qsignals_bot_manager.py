"""Q-SIGNALS driven bot manager — DRY RUN shadow mode.

Runs 6 virtual grid bots defined in bots_qsignals.toml. Every cycle:
  1. Fetch OHLCV for each bot's symbol
  2. Evaluate all applicable Q-SIGNALS strategies
  3. Derive consensus direction (LONG / SHORT / FLAT)
  4. Compare against this bot's stored direction
  5. If the consensus flipped → log a simulated flip and mark-to-market P&L
  6. Persist state in pionex-bot/state/qs_<bot>.json

**Never calls Pionex order endpoints during dry_run.** Produces JSONL trade
log (`state/qsignals_trades.jsonl`) and per-bot state for the dashboard.

Usage:
    python qsignals_bot_manager.py                # single cycle
    python qsignals_bot_manager.py --loop         # hourly loop
    python qsignals_bot_manager.py --interval 15  # 15-min loop
    python qsignals_bot_manager.py --bot qs_btc   # one bot only
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from qsignals_adapter import evaluate, strategies_for_symbol
from signal_manager import BotAPIClient, setup_logger
from signal_manager_qsignals import fetch_ohlcv, symbol_to_spot
from utils import load_toml


def parse_base_quote(symbol: str) -> tuple[str, str]:
    """BTC_USDT_PERP → ('BTC.PERP', 'USDT') — Pionex futures grid format."""
    is_perp = "_PERP" in symbol
    s = symbol.replace("_PERP", "")
    parts = s.split("_")
    base = parts[0]
    quote = parts[1] if len(parts) > 1 else "USDT"
    return (f"{base}.PERP" if is_perp else base, quote)


def _flip_cooldown_ok(state: dict, min_hours: float) -> bool:
    last = state.get("last_flip_ts")
    if not last:
        return True
    try:
        prev_ts = datetime.fromisoformat(last.replace("Z", "+00:00"))
        age_h = (datetime.now(timezone.utc) - prev_ts).total_seconds() / 3600
        return age_h >= min_hours
    except Exception:
        return True

STATE_DIR = Path(__file__).parent / "state"
TRADE_LOG = STATE_DIR / "qsignals_trades.jsonl"
CONFIG_PATH = Path(__file__).parent / "bots_qsignals.toml"

DIR_MAP = {
    "BUY": "LONG", "CLOSE_SHORT": "LONG",
    "SELL": "SHORT", "CLOSE_LONG": "SHORT",
    "HOLD": "FLAT",
}


def consensus(qs_signals: dict[str, str]) -> tuple[str, int, int]:
    """Majority vote across strategies. Returns (direction, agree, total)."""
    votes = [DIR_MAP.get(s, "FLAT") for s in qs_signals.values() if not s.startswith("ERR")]
    if not votes:
        return "FLAT", 0, 0
    counts = {"LONG": 0, "SHORT": 0, "FLAT": 0}
    for v in votes:
        counts[v] += 1
    best = max(counts, key=counts.get)
    return best, counts[best], len(votes)


def load_bot_state(bot: str) -> dict:
    p = STATE_DIR / f"{bot}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"direction": "FLAT", "entry_price": None, "flips": 0,
            "sim_pnl": 0.0, "last_flip_ts": None}


def save_bot_state(bot: str, state: dict):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f"{bot}.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def execute_flip(client: BotAPIClient, name: str, cfg: dict, global_cfg: dict,
                 new_dir: str, state: dict, log) -> tuple[str | None, str | None]:
    """Cancel current bot (if any) and create a new futures grid in new_dir.
    Returns (new_bu_order_id, error_msg)."""
    if new_dir == "FLAT":
        # Flat: cancel existing, don't create new
        old_id = state.get("bu_order_id")
        if old_id:
            try:
                client.bot_cancel(old_id)
                log.info("[%s] cancelled bot %s (flat signal)", name.upper(), old_id[:8])
            except Exception as e:
                return None, f"cancel failed: {e}"
        return None, None

    trend = "long" if new_dir == "LONG" else "short"
    base, quote = parse_base_quote(cfg["symbol"])

    # Cancel existing first
    old_id = state.get("bu_order_id")
    if old_id:
        try:
            client.bot_cancel(old_id)
            log.info("[%s] cancelled old bot %s", name.upper(), old_id[:8])
        except Exception as e:
            log.warning("[%s] cancel old bot failed (continuing): %s", name.upper(), e)

    # Create new bot
    try:
        result = client.bot_create(
            base=base, quote=quote,
            top=str(cfg["grid_top"]), bottom=str(cfg["grid_bottom"]),
            row=int(global_cfg.get("grid_rows", 50)),
            grid_type=global_cfg.get("grid_type", "arithmetic"),
            trend=trend,
            leverage=int(cfg.get("leverage", 5)),
            investment=str(cfg.get("investment", "50")),
        )
        if result.get("result"):
            new_id = result["data"]["buOrderId"]
            log.info("[%s] CREATED %s bot %s", name.upper(), trend.upper(), new_id[:8])
            return new_id, None
        return None, f"create failed: {result}"
    except Exception as e:
        return None, f"create exception: {e}"


def process_bot(client: BotAPIClient, name: str, cfg: dict, global_cfg: dict, log) -> dict:
    symbol = cfg["symbol"]
    interval = global_cfg.get("interval", "60M")
    limit = int(global_cfg.get("kline_limit", 300))
    tf_map = {"5M": "5m", "15M": "15m", "30M": "30m", "60M": "1h", "4H": "4h", "1D": "1d"}
    timeframe = tf_map.get(interval, "1h")
    qs_symbol = symbol_to_spot(symbol)

    candles = fetch_ohlcv(client, symbol, interval, limit)
    if len(candles) < 100:
        log.warning("[%s] insufficient candles (%d) — skip", name.upper(), len(candles))
        return {"bot": name, "error": "insufficient candles"}

    price = candles[-1]["close"]
    strategies = strategies_for_symbol(qs_symbol)
    if not strategies:
        log.warning("[%s] no Q-SIGNALS strategies cover %s — skip", name.upper(), qs_symbol)
        return {"bot": name, "error": f"no strategies for {qs_symbol}"}

    qs_signals = {}
    for sid in strategies:
        try:
            res = evaluate(sid, qs_symbol, timeframe, candles, timeout=25)
            qs_signals[sid] = res["signal"]
        except Exception as e:
            qs_signals[sid] = f"ERR:{str(e)[:40]}"

    new_dir, agree, total = consensus(qs_signals)
    state = load_bot_state(name)
    prev_dir = state.get("direction", "FLAT")
    dry_run = bool(global_cfg.get("dry_run", True))
    min_cooldown = float(global_cfg.get("min_hours_between_flips", 2))

    action = "KEEP"
    sim_pnl_delta = 0.0

    if new_dir != prev_dir:
        # Enforce cooldown to avoid whipsaw
        if not _flip_cooldown_ok(state, min_cooldown):
            log.info("[%s] flip %s→%s BLOCKED by cooldown (<%s h)",
                     name.upper(), prev_dir, new_dir, min_cooldown)
            action = f"COOLDOWN ({prev_dir})"
            save_bot_state(name, {**state, "last_ts": datetime.now(timezone.utc).isoformat(),
                                  "last_price": price, "last_consensus": new_dir,
                                  "last_agree": f"{agree}/{total}"})
            return {"bot": name, "symbol": symbol, "price": price,
                    "prev_dir": prev_dir, "new_dir": new_dir, "action": action,
                    "agree": agree, "total": total,
                    "flips": state.get("flips", 0), "sim_pnl": state.get("sim_pnl", 0.0)}
        # Mark-to-market the closing leg (if we were in a position)
        if prev_dir in ("LONG", "SHORT") and state.get("entry_price"):
            entry = float(state["entry_price"])
            invest = float(cfg.get("investment", 50))
            lev = float(cfg.get("leverage", 5))
            notional = invest * lev
            qty = notional / entry
            if prev_dir == "LONG":
                sim_pnl_delta = (price - entry) * qty
            else:
                sim_pnl_delta = (entry - price) * qty
            state["sim_pnl"] = round(float(state.get("sim_pnl", 0.0)) + sim_pnl_delta, 4)

        state["direction"] = new_dir
        state["entry_price"] = price if new_dir in ("LONG", "SHORT") else None
        state["flips"] = int(state.get("flips", 0)) + 1
        state["last_flip_ts"] = datetime.now(timezone.utc).isoformat()
        action = f"FLIP {prev_dir}→{new_dir}"

        # LIVE: actually place/cancel orders on Pionex
        live_error = None
        if not dry_run:
            new_id, live_error = execute_flip(client, name, cfg, global_cfg, new_dir, state, log)
            if new_id:
                state["bu_order_id"] = new_id
                state["bot_status"] = "running"
            elif new_dir == "FLAT":
                state["bu_order_id"] = None
                state["bot_status"] = "stopped"
            else:
                state["bot_status"] = f"error: {live_error}"
                action = f"FLIP {prev_dir}→{new_dir} FAILED"
                log.error("[%s] live flip failed: %s", name.upper(), live_error)

        TRADE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(TRADE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": state["last_flip_ts"],
                "bot": name, "symbol": symbol, "qs_symbol": qs_symbol,
                "action": action, "price": price, "prev_dir": prev_dir,
                "new_dir": new_dir, "agree": agree, "total": total,
                "closed_pnl_delta": round(sim_pnl_delta, 4),
                "cumulative_pnl": state["sim_pnl"],
                "bu_order_id": state.get("bu_order_id"),
                "mode": "LIVE" if not dry_run else "DRY",
                "live_error": live_error,
                "strategies": qs_signals,
            }, ensure_ascii=False) + "\n")

    state["last_ts"] = datetime.now(timezone.utc).isoformat()
    state["last_price"] = price
    state["last_consensus"] = new_dir
    state["last_agree"] = f"{agree}/{total}"
    save_bot_state(name, state)

    log.info(
        "[%s] %s $%.4f  prev=%s  new=%s (%d/%d)  %s  flips=%d  sim_pnl=%.2f",
        name.upper(), symbol, price, prev_dir, new_dir, agree, total,
        action, state["flips"], state["sim_pnl"],
    )
    return {
        "bot": name, "symbol": symbol, "price": price,
        "prev_dir": prev_dir, "new_dir": new_dir, "action": action,
        "agree": agree, "total": total,
        "flips": state["flips"], "sim_pnl": state["sim_pnl"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=int, default=60, help="Loop minutes")
    ap.add_argument("--bot", type=str, help="Single bot only")
    ap.add_argument("--config", type=str, default=str(CONFIG_PATH))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    cfg = load_toml(args.config)
    global_cfg = cfg.get("global", {})
    bots_cfg = cfg.get("bots", {})
    log = setup_logger("DEBUG" if args.verbose else "INFO")

    dry_run = bool(global_cfg.get("dry_run", True))
    log.info("=" * 60)
    log.info("Q-SIGNALS Bot Manager — %s", "DRY RUN (shadow)" if dry_run else "!!! LIVE MODE — REAL ORDERS !!!")
    log.info("Bots: %d | Config: %s", len(bots_cfg), args.config)
    log.info("Trade log: %s", TRADE_LOG)
    if not dry_run:
        log.warning("Cooldown: %s h between flips", global_cfg.get("min_hours_between_flips", 2))
    log.info("=" * 60)

    def cycle():
        client = BotAPIClient()
        log.info("--- Cycle: %s ---", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
        results = []
        for name, bcfg in bots_cfg.items():
            if args.bot and name != args.bot:
                continue
            try:
                results.append(process_bot(client, name, bcfg, global_cfg, log))
            except Exception as e:
                log.error("[%s] unhandled: %s", name.upper(), e, exc_info=args.verbose)
        # Cycle summary
        if results:
            total_pnl = sum(r.get("sim_pnl", 0) for r in results if isinstance(r.get("sim_pnl"), (int, float)))
            total_flips = sum(r.get("flips", 0) for r in results if isinstance(r.get("flips"), int))
            log.info("--- Cycle done: %d bots | total sim P&L $%.2f | total flips %d ---",
                     len(results), total_pnl, total_flips)

    if args.loop:
        while True:
            try:
                cycle()
            except KeyboardInterrupt:
                log.info("stopped"); break
            except Exception as e:
                log.error("cycle failed: %s", e, exc_info=True)
            log.info("sleeping %d min...", args.interval)
            time.sleep(args.interval * 60)
    else:
        cycle()


if __name__ == "__main__":
    main()
