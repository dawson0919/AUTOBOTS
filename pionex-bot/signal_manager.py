"""
三刀流 v6 Signal Manager — Automated Bot Lifecycle
====================================================
Checks signals every hour, cancels conflicting bots, creates aligned bots.

Usage:
    python signal_manager.py                # Run once (check all bots)
    python signal_manager.py --loop         # Run continuously (every hour)
    python signal_manager.py --dry-run      # Force dry run (no real trades)
    python signal_manager.py --live         # Force live mode (overrides config)
    python signal_manager.py --bot xaut     # Check single bot only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np

from utils import load_toml, load_state, save_state, STATE_DIR

# ── Logging ─────────────────────────────────────────────

import logging
import io

def setup_logger(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("signal-manager")
    logger.setLevel(getattr(logging, level, logging.INFO))
    if not logger.handlers:
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        utf8_out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        ch = logging.StreamHandler(utf8_out)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

        fh = logging.FileHandler(
            Path(__file__).parent / "signal_manager.log", encoding="utf-8"
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


# ── Signal Engine (imported from signal_v6.py) ──────────

from signal_v6 import (
    SIG_DIR, SIG_NAMES, SIG_HOLD,
    sma, calc_raw_signal, SignalState, replay_signal_state,
)

from notifier import get_notifier


# ── Pionex API Client (standalone, no Config dependency) ─

def load_api_keys() -> tuple[str, str]:
    """Load API keys from ~/.pionex/config.toml."""
    config_path = Path.home() / ".pionex" / "config.toml"
    key, secret = "", ""
    with open(config_path, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("api_key"):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("secret_key"):
                secret = line.split("=", 1)[1].strip().strip('"').strip("'")
    return key, secret


class BotAPIClient:
    """Minimal Pionex API client for signal manager."""

    def __init__(self):
        self.api_key, self.api_secret = load_api_keys()
        self.base_url = "https://api.pionex.com"
        self._http = httpx.Client(timeout=15)
        self._time_offset = 0
        self._sync_time()

    def _sync_time(self):
        try:
            local_ts = int(time.time() * 1000)
            resp = self._http.get(f"{self.base_url}/api/v1/common/symbols?symbols=BTC_USDT")
            server_ts = resp.json().get("timestamp", local_ts)
            self._time_offset = server_ts - local_ts
        except Exception:
            self._time_offset = 0

    def _sign(self, method: str, path: str, params: dict, body_str: str = "") -> str:
        import hashlib, hmac as _hmac
        ts = str(int(time.time() * 1000) + self._time_offset)
        params["timestamp"] = ts
        sorted_params = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        sign_str = f"{method}{path}?{sorted_params}"
        if body_str:
            sign_str += body_str
        return _hmac.new(
            self.api_secret.encode(), sign_str.encode(), hashlib.sha256
        ).hexdigest()

    def _get(self, path: str, params: dict) -> dict:
        sig = self._sign("GET", path, params)
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        url = f"{self.base_url}{path}?{query}&signature={sig}"
        headers = {"PIONEX-KEY": self.api_key, "PIONEX-SIGNATURE": sig}
        resp = self._http.get(url, headers=headers)
        return resp.json()

    def _post(self, path: str, body: dict) -> dict:
        params: dict = {}
        body_str = json.dumps(body, separators=(",", ":"))
        sig = self._sign("POST", path, params, body_str)
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        url = f"{self.base_url}{path}?{query}&signature={sig}"
        headers = {
            "PIONEX-KEY": self.api_key,
            "PIONEX-SIGNATURE": sig,
            "Content-Type": "application/json",
        }
        resp = self._http.post(url, headers=headers, content=body_str)
        return resp.json()

    # ── Public ──

    def get_klines(self, symbol: str, interval: str, limit: int = 500) -> list[dict]:
        params = {"symbol": symbol, "interval": interval, "limit": str(limit)}
        resp = self._http.get(f"{self.base_url}/api/v1/market/klines", params=params)
        data = resp.json()
        if data.get("result"):
            return data.get("data", {}).get("klines", [])
        return []

    def get_ticker_price(self, symbol: str) -> float:
        resp = self._http.get(
            f"{self.base_url}/api/v1/market/tickers", params={"symbol": symbol}
        )
        data = resp.json()
        if data.get("result"):
            tickers = data.get("data", {}).get("tickers", [])
            if tickers:
                return float(tickers[0].get("close", 0))
        return 0.0

    # ── Bot API ──

    def bot_get(self, bu_order_id: str) -> dict | None:
        data = self._get("/api/v1/bot/orders/futuresGrid/order", {"buOrderId": bu_order_id})
        if data.get("result"):
            return data.get("data", {})
        return None

    def bot_cancel(self, bu_order_id: str) -> dict:
        return self._post("/api/v1/bot/orders/futuresGrid/cancel", {"buOrderId": bu_order_id})

    def bot_create(
        self, base: str, quote: str, top: str, bottom: str, row: int,
        grid_type: str, trend: str, leverage: int, investment: str,
    ) -> dict:
        body = {
            "base": base,
            "quote": quote,
            "buOrderData": {
                "top": top,
                "bottom": bottom,
                "row": row,
                "grid_type": grid_type,
                "trend": trend,
                "leverage": leverage,
                "quoteInvestment": investment,
            },
        }
        return self._post("/api/v1/bot/orders/futuresGrid/create", body)

    def close(self):
        self._http.close()


# ── Main Logic ──────────────────────────────────────────

def fetch_closes(client: BotAPIClient, symbol: str, interval: str, limit: int) -> list[float]:
    """Fetch klines and return close prices (oldest first)."""
    all_k: dict[int, dict] = {}
    end_time = None
    pages_needed = (limit // 500) + 2

    for _ in range(pages_needed):
        params = {"symbol": symbol, "interval": interval, "limit": "500"}
        if end_time:
            params["endTime"] = str(end_time)
        resp = client._http.get(f"{client.base_url}/api/v1/market/klines", params=params)
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

    sorted_klines = sorted(all_k.values(), key=lambda x: x["time"])
    return [float(k["close"]) for k in sorted_klines]


def process_bot(
    client: BotAPIClient,
    bot_name: str,
    bot_cfg: dict,
    global_cfg: dict,
    dry_run: bool,
    log: logging.Logger,
) -> str:
    """Process one bot: check signal, decide action, execute.

    Returns: action taken ("KEEP", "FLIP", "SKIP", "ERROR")
    """
    symbol = bot_cfg["symbol"]
    lb_p = bot_cfg["liu_bei"]
    gy_p = bot_cfg["guan_yu"]
    zf_p = bot_cfg["zhang_fei"]
    dist_pct = bot_cfg.get("dist_pct", 2.0)
    disable_bounce = bot_cfg.get("disable_bounce", True)
    bu_order_id = bot_cfg.get("bu_order_id", "")
    interval = global_cfg.get("interval", "60M")

    log.info("[%s] === Checking %s ===", bot_name.upper(), symbol)

    # ── 1. Fetch klines ──
    need_candles = lb_p + 50
    closes = fetch_closes(client, symbol, interval, need_candles)
    if len(closes) < lb_p + 1:
        log.error("[%s] Not enough candles: %d (need %d)", bot_name.upper(), len(closes), lb_p + 1)
        return "ERROR"

    log.info("[%s] Loaded %d candles", bot_name.upper(), len(closes))

    # ── 2. Calculate current SMAs and signal ──
    lb_val = sma(closes, lb_p)
    gy_val = sma(closes, gy_p)
    zf_val = sma(closes, zf_p)
    price = closes[-1]

    raw_signal = calc_raw_signal(price, lb_val, gy_val, zf_val, dist_pct, disable_bounce)
    raw_dir = SIG_DIR.get(raw_signal, 0)

    log.info(
        "[%s] Price=%.4f | LB(%.4f) GY(%.4f) ZF(%.4f) | Signal=%s Dir=%s",
        bot_name.upper(), price, lb_val, gy_val, zf_val,
        SIG_NAMES.get(raw_signal, "?"),
        {1: "LONG", -1: "SHORT", 0: "HOLD"}.get(raw_dir, "?"),
    )

    # ── 3. Load state and check for flip ──
    state_data = load_state(bot_name)
    sig_state_code = state_data.get("sig_state", 0)
    flips_today = state_data.get("flips_today", 0)
    last_flip_date = state_data.get("last_flip_date", "")
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Reset daily counter
    if last_flip_date != today_str:
        flips_today = 0

    # Initialize state if first run
    if not state_data:
        log.info("[%s] First run — replaying signal history to establish state...", bot_name.upper())
        replayed = replay_signal_state(closes, lb_p, gy_p, zf_p, dist_pct, disable_bounce)
        sig_state_code = replayed.sig_state
        log.info(
            "[%s] Replay complete: sig_state=%s dir=%s (observe only, no action)",
            bot_name.upper(),
            SIG_NAMES.get(sig_state_code, "?"),
            {1: "LONG", -1: "SHORT", 0: "HOLD"}.get(replayed.current_direction, "?"),
        )
        save_state(bot_name, {
            "sig_state": sig_state_code,
            "current_direction": replayed.current_direction,
            "bu_order_id": bu_order_id,
            "last_check": datetime.now(timezone.utc).isoformat(),
            "last_price": price,
            "last_signal": SIG_NAMES.get(raw_signal, "?"),
            "flips_today": 0,
            "last_flip_date": today_str,
            "initialized": True,
        })
        return "SKIP"  # Don't act on first run

    # Apply flip-only state machine
    signal_state = SignalState(sig_state_code)
    direction_changed, new_dir = signal_state.update(raw_signal)

    log.info(
        "[%s] State: prev=%s | new=%s | flip=%s",
        bot_name.upper(),
        SIG_NAMES.get(sig_state_code, "?"),
        SIG_NAMES.get(signal_state.sig_state, "?"),
        direction_changed,
    )

    # ── 4. Query current bot status ──
    bot_data = None
    bot_trend = "unknown"
    bot_status = "unknown"
    if bu_order_id:
        bot_data = client.bot_get(bu_order_id)
        if bot_data:
            bd = bot_data.get("buOrderData", {})
            bot_status = bd.get("status", "unknown")
            bot_trend = bd.get("trend", "unknown")
            log.info("[%s] Bot status=%s trend=%s", bot_name.upper(), bot_status, bot_trend)
        else:
            log.warning("[%s] Could not query bot %s", bot_name.upper(), bu_order_id)

    # ── Grid range monitoring ──
    if bot_status == "running" and price > 0:
        grid_bottom = float(bot_cfg.get("grid_bottom", 0))
        grid_top = float(bot_cfg.get("grid_top", 0))
        grid_range = grid_top - grid_bottom

        if grid_range > 0:
            margin = grid_range * 0.05  # 5% buffer

            if price < grid_bottom + margin:
                log.warning("[%s] PRICE NEAR GRID BOTTOM: %.4f (grid: %.2f-%.2f)",
                           bot_name.upper(), price, grid_bottom, grid_top)
                get_notifier().notify_risk(
                    f"⚠️ {bot_name.upper()} price {price:.4f} near grid bottom {grid_bottom}"
                )
            elif price > grid_top - margin:
                log.warning("[%s] PRICE NEAR GRID TOP: %.4f (grid: %.2f-%.2f)",
                           bot_name.upper(), price, grid_bottom, grid_top)
                get_notifier().notify_risk(
                    f"⚠️ {bot_name.upper()} price {price:.4f} near grid top {grid_top}"
                )

            if price < grid_bottom or price > grid_top:
                log.error("[%s] PRICE OUTSIDE GRID: %.4f (grid: %.2f-%.2f) — bot may be inactive!",
                         bot_name.upper(), price, grid_bottom, grid_top)
                get_notifier().notify_error(bot_name,
                    f"Price {price:.4f} OUTSIDE grid [{grid_bottom}-{grid_top}]! Bot may be inactive.")

    # ── 5. Decide action ──

    # Map bot trend to direction
    trend_dir = {"long": 1, "short": -1, "no_trend": 0}.get(bot_trend, 0)

    # ── 5a. Orphan bot detection ──
    # Bot was externally stopped (grid out of range, manual cancel, liquidation)
    # but state still holds the old buOrderId. Rebuild if signal has a direction.
    if bot_status in ("canceled", "closed", None) and bu_order_id:
        sig_dir = SIG_DIR.get(signal_state.sig_state, 0)
        if sig_dir != 0:
            log.warning(
                "[%s] ORPHAN BOT DETECTED: bot %s is %s but signal is %s (dir=%d)",
                bot_name.upper(), bu_order_id[:12], bot_status,
                SIG_NAMES.get(signal_state.sig_state, "?"), sig_dir,
            )

            if not dry_run:
                trend = "long" if sig_dir == 1 else "short"
                investment = bot_cfg.get("investment", "125")

                # Check portfolio override
                portfolio_path = STATE_DIR / "portfolio.json"
                if portfolio_path.exists():
                    try:
                        with open(portfolio_path, "r", encoding="utf-8") as f:
                            pf = json.load(f)
                        alloc = pf.get("allocations", {}).get(bot_name, {})
                        if alloc.get("target_investment"):
                            investment = str(alloc["target_investment"])
                    except Exception:
                        pass

                log.info("[%s] Rebuilding %s bot with investment=%s", bot_name.upper(), trend, investment)
                try:
                    result = client.bot_create(
                        base=bot_cfg["base"], quote=bot_cfg["quote"],
                        top=bot_cfg["grid_top"], bottom=bot_cfg["grid_bottom"],
                        row=int(bot_cfg.get("grid_rows", 50)),
                        grid_type=bot_cfg.get("grid_type", "arithmetic"),
                        trend=trend, leverage=int(bot_cfg.get("leverage", 5)),
                        investment=investment,
                    )
                    if result.get("result"):
                        new_id = result["data"]["buOrderId"]
                        log.info("[%s] Rebuilt bot: %s", bot_name.upper(), new_id)
                        get_notifier().notify_rebuild(
                            bot_name, trend,
                            "Orphan bot detected — externally canceled",
                        )
                        save_state(bot_name, {
                            **state_data,
                            "bu_order_id": new_id,
                            "bot_status": "running",
                            "bot_trend": trend,
                            "sig_state": signal_state.sig_state,
                            "current_direction": signal_state.current_direction,
                            "last_check": datetime.now(timezone.utc).isoformat(),
                        })
                        return "REBUILD"
                    else:
                        log.error("[%s] Rebuild failed: %s", bot_name.upper(), result)
                except Exception as e:
                    log.error("[%s] Rebuild error: %s", bot_name.upper(), e)
            else:
                log.info(
                    "[%s] [DRY RUN] Would rebuild %s bot",
                    bot_name.upper(), "LONG" if sig_dir == 1 else "SHORT",
                )
                return "REBUILD_DRY"

    if not direction_changed:
        # No flip — just save state and continue
        save_state(bot_name, {
            "sig_state": signal_state.sig_state,
            "current_direction": signal_state.current_direction,
            "bu_order_id": bu_order_id,
            "bot_status": bot_status,
            "bot_trend": bot_trend,
            "last_check": datetime.now(timezone.utc).isoformat(),
            "last_price": price,
            "last_signal": SIG_NAMES.get(raw_signal, "?"),
            "flips_today": flips_today,
            "last_flip_date": today_str,
            "initialized": True,
        })
        log.info("[%s] No direction change — KEEP bot running", bot_name.upper())
        return "KEEP"

    # Direction changed! Check safety limits
    max_flips = global_cfg.get("max_flips_per_day", 2)
    if flips_today >= max_flips:
        log.warning(
            "[%s] FLIP BLOCKED: already %d flips today (max=%d)",
            bot_name.upper(), flips_today, max_flips,
        )
        save_state(bot_name, {
            "sig_state": signal_state.sig_state,
            "current_direction": signal_state.current_direction,
            "bu_order_id": bu_order_id,
            "bot_status": bot_status,
            "bot_trend": bot_trend,
            "last_check": datetime.now(timezone.utc).isoformat(),
            "last_price": price,
            "last_signal": SIG_NAMES.get(raw_signal, "?"),
            "flips_today": flips_today,
            "last_flip_date": today_str,
            "flip_blocked": True,
            "initialized": True,
        })
        return "SKIP"

    # Multi-timeframe confirmation (optional)
    if global_cfg.get("mtf_confirm", False):
        from mtf_confirm import confirm_flip
        dist = float(bot_cfg.get("dist_pct", 2.0))
        no_bounce = bot_cfg.get("disable_bounce", True)
        if isinstance(no_bounce, str):
            no_bounce = no_bounce.lower() in ("true", "yes", "1")
        if not confirm_flip(client, symbol, new_dir,
                           int(bot_cfg["liu_bei"]), int(bot_cfg["guan_yu"]), int(bot_cfg["zhang_fei"]),
                           dist, no_bounce):
            log.info("[%s] Flip BLOCKED by 4H timeframe — waiting for alignment", bot_name.upper())
            save_state(bot_name, {
                "sig_state": signal_state.sig_state,
                "current_direction": signal_state.current_direction,
                "bu_order_id": bu_order_id,
                "bot_status": bot_status,
                "bot_trend": bot_trend,
                "last_check": datetime.now(timezone.utc).isoformat(),
                "last_price": price,
                "last_signal": SIG_NAMES.get(raw_signal, "?"),
                "flips_today": flips_today,
                "last_flip_date": today_str,
                "mtf_blocked": True,
                "initialized": True,
            })
            return "MTF_BLOCK"

    # Price sanity check
    grid_bottom = float(bot_cfg["grid_bottom"])
    grid_top = float(bot_cfg["grid_top"])
    if price < grid_bottom or price > grid_top:
        log.warning(
            "[%s] Price %.4f outside grid [%.2f — %.2f], skip flip",
            bot_name.upper(), price, grid_bottom, grid_top,
        )
        save_state(bot_name, {
            "sig_state": signal_state.sig_state,
            "current_direction": signal_state.current_direction,
            "bu_order_id": bu_order_id,
            "last_check": datetime.now(timezone.utc).isoformat(),
            "last_price": price,
            "last_signal": SIG_NAMES.get(raw_signal, "?"),
            "flips_today": flips_today,
            "last_flip_date": today_str,
            "price_out_of_range": True,
            "initialized": True,
        })
        return "SKIP"

    new_trend = "long" if new_dir == 1 else "short"
    log.info(
        "[%s] >>> SIGNAL FLIP: %s -> %s (new trend: %s) <<<",
        bot_name.upper(),
        SIG_NAMES.get(sig_state_code, "?"),
        SIG_NAMES.get(signal_state.sig_state, "?"),
        new_trend.upper(),
    )

    # ── 6. Execute: Cancel old bot ──
    new_bu_order_id = bu_order_id

    if dry_run:
        log.info("[%s] [DRY RUN] Would cancel bot %s", bot_name.upper(), bu_order_id)
        log.info(
            "[%s] [DRY RUN] Would create %s bot: %s grid=[%s-%s] lev=%sx inv=%s",
            bot_name.upper(), new_trend.upper(), symbol,
            bot_cfg["grid_bottom"], bot_cfg["grid_top"],
            bot_cfg["leverage"], bot_cfg["investment"],
        )
    else:
        # Cancel existing bot — always attempt if we have an ID
        if bu_order_id:
            log.info("[%s] Cancelling bot %s (status=%s)...", bot_name.upper(), bu_order_id, bot_status)
            try:
                result = client.bot_cancel(bu_order_id)
                if result.get("result"):
                    log.info("[%s] Bot cancelled successfully", bot_name.upper())
                elif "already" in str(result.get("message", "")).lower() or bot_status in ("canceled", "closed"):
                    log.info("[%s] Bot already canceled/closed, proceeding", bot_name.upper())
                else:
                    log.warning("[%s] Cancel returned: %s — proceeding anyway", bot_name.upper(), result)
            except Exception as e:
                log.warning("[%s] Cancel exception: %s — proceeding anyway", bot_name.upper(), e)

            # Cooldown
            cooldown = global_cfg.get("cooldown_seconds", 10)
            log.info("[%s] Waiting %ds cooldown...", bot_name.upper(), cooldown)
            time.sleep(cooldown)
        else:
            log.warning("[%s] No old bot ID to cancel — creating new bot directly", bot_name.upper())

        # ── 7. Create new bot ──
        investment = bot_cfg["investment"]

        # Check portfolio agent allocation override
        portfolio_path = STATE_DIR / "portfolio.json"
        if portfolio_path.exists():
            try:
                with open(portfolio_path, "r", encoding="utf-8") as f:
                    pf = json.load(f)
                alloc = pf.get("allocations", {}).get(bot_name, {})
                target = alloc.get("target_investment")
                if target:
                    investment = str(target)
                    log.info("[%s] Portfolio override: investment=%s USDT", bot_name.upper(), investment)
            except Exception as e:
                log.warning("[%s] Failed to read portfolio state: %s", bot_name.upper(), e)

        log.info(
            "[%s] Creating %s bot: %s grid=[%s-%s] rows=%d lev=%sx inv=%s",
            bot_name.upper(), new_trend.upper(), symbol,
            bot_cfg["grid_bottom"], bot_cfg["grid_top"],
            bot_cfg["grid_rows"], bot_cfg["leverage"], investment,
        )
        try:
            result = client.bot_create(
                base=bot_cfg["base"],
                quote=bot_cfg["quote"],
                top=bot_cfg["grid_top"],
                bottom=bot_cfg["grid_bottom"],
                row=bot_cfg["grid_rows"],
                grid_type=bot_cfg["grid_type"],
                trend=new_trend,
                leverage=bot_cfg["leverage"],
                investment=investment,
            )
            if result.get("result"):
                new_bu_order_id = result.get("data", {}).get("buOrderId", bu_order_id)
                log.info("[%s] New bot created! ID=%s", bot_name.upper(), new_bu_order_id)
            else:
                log.error("[%s] Create failed: %s - %s", bot_name.upper(), result.get("code"), result.get("message"))
                return "ERROR"
        except Exception as e:
            log.error("[%s] Create exception: %s", bot_name.upper(), e)
            return "ERROR"

    # ── 8. Save state ──
    flips_today += 1
    old_dir_name = {1: "LONG", -1: "SHORT", 0: "HOLD"}.get(
        SIG_DIR.get(sig_state_code, 0), "HOLD"
    )
    new_dir_name = new_trend.upper()
    save_state(bot_name, {
        "sig_state": signal_state.sig_state,
        "current_direction": signal_state.current_direction,
        "bu_order_id": new_bu_order_id,
        "bot_trend": new_trend,
        "bot_status": "running" if not dry_run else "dry_run",
        "last_check": datetime.now(timezone.utc).isoformat(),
        "last_price": price,
        "last_signal": SIG_NAMES.get(raw_signal, "?"),
        "last_flip_time": datetime.now(timezone.utc).isoformat(),
        "flips_today": flips_today,
        "last_flip_date": today_str,
        "initialized": True,
    })

    if not dry_run:
        get_notifier().notify_flip(bot_name, old_dir_name, new_dir_name, price, symbol)

    return "FLIP"


# ── Entry Point ─────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="三刀流 v6 Signal Manager")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--dry-run", action="store_true", help="Force dry run")
    parser.add_argument("--live", action="store_true", help="Force live mode")
    parser.add_argument("--bot", type=str, help="Process single bot only")
    parser.add_argument("--config", type=str, default="bots.toml", help="Config file (default: bots.toml)")
    args = parser.parse_args()

    # Load config
    config_path = Path(__file__).parent / args.config
    cfg = load_toml(str(config_path))
    global_cfg = cfg.get("global", {})
    bots_cfg = cfg.get("bots", {})

    # Determine dry_run
    dry_run = global_cfg.get("dry_run", True)
    if args.dry_run:
        dry_run = True
    if args.live:
        dry_run = False

    log = setup_logger(global_cfg.get("log_level", "INFO"))

    log.info("=" * 60)
    log.info("三刀流 v6 Signal Manager")
    log.info("Mode: %s | Bots: %d | Interval: %s",
             "DRY RUN" if dry_run else "LIVE",
             len(bots_cfg), global_cfg.get("interval", "60M"))
    log.info("=" * 60)

    def run_cycle():
        client = BotAPIClient()
        log.info("--- Cycle start: %s ---", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
        results = {}
        for bot_name, bot_cfg in bots_cfg.items():
            if args.bot and bot_name != args.bot:
                continue
            try:
                action = process_bot(client, bot_name, bot_cfg, global_cfg, dry_run, log)
                results[bot_name] = action
            except Exception as e:
                log.error("[%s] Unhandled error: %s", bot_name.upper(), e, exc_info=True)
                results[bot_name] = "ERROR"

        # Summary
        log.info("--- Cycle complete ---")
        for name, action in results.items():
            label = {
                "KEEP": "KEEP",
                "FLIP": "FLIP ***",
                "REBUILD": "REBUILD ***",
                "REBUILD_DRY": "REBUILD_DRY",
                "SKIP": "SKIP",
                "MTF_BLOCK": "MTF_BLOCK",
                "ERROR": "ERROR !!!",
            }.get(action, action)
            log.info("  %s: %s", name.upper(), label)
        log.info("")

        # After cycle complete, check if daily summary needed
        utc_hour = datetime.now(timezone.utc).hour
        if utc_hour == 0:  # First cycle of the day
            try:
                summary = {
                    'portfolio_value': 0,
                    'drawdown': 0,
                    'bots': {}
                }
                # Read portfolio state
                pf_path = STATE_DIR / "portfolio.json"
                pf = {}
                if pf_path.exists():
                    with open(pf_path, "r", encoding="utf-8") as f:
                        pf = json.load(f)
                    summary['portfolio_value'] = pf.get('portfolio_value', 0)
                    summary['drawdown'] = pf.get('max_drawdown_seen', 0)

                for name in bots_cfg:
                    summary['bots'][name] = {
                        'status': results.get(name, 'KEEP'),
                        'roi': pf.get('allocations', {}).get(name, {}).get('roi', 0) if pf else 0,
                    }
                get_notifier().notify_daily_summary(summary)
            except Exception as e:
                log.warning("Daily summary failed: %s", e)

        client.close()

    if args.loop:
        poll_minutes = global_cfg.get("poll_minutes", 60)
        log.info("Loop mode: checking every %d minutes", poll_minutes)
        while True:
            try:
                run_cycle()
            except KeyboardInterrupt:
                log.info("Shutting down...")
                break
            except Exception as e:
                log.error("Cycle error: %s", e, exc_info=True)

            # Sleep until next hour + 60s (align with candle close)
            now = time.time()
            next_check = ((int(now) // (poll_minutes * 60)) + 1) * (poll_minutes * 60) + 60
            wait = max(next_check - now, 10)
            log.info("Next check in %.0f seconds...", wait)
            try:
                time.sleep(wait)
            except KeyboardInterrupt:
                log.info("Shutting down...")
                break
    else:
        run_cycle()


if __name__ == "__main__":
    main()
