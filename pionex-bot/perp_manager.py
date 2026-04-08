"""
三刀流 v6 Perp Manager — Automated Perpetual Futures Trading (No Grid)
========================================================================
Checks signals every interval, opens/flips long/short positions using 5x leverage.

Usage:
    python perp_manager.py --config perp_bots_15m.toml --loop
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
from signal_v6 import (
    SIG_DIR, SIG_NAMES, SIG_HOLD,
    sma, calc_raw_signal, SignalState, replay_signal_state,
)
from notifier import get_notifier

# ── Logging ─────────────────────────────────────────────

import logging
import io

def setup_logger(name: str, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
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
            Path(__file__).parent / f"{name}.log", encoding="utf-8"
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


# ── Pionex API Client ───────────────────────────────────

def load_api_keys() -> tuple[str, str]:
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


class PerpAPIClient:
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
            pass

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

    def _request(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict:
        params = params or {}
        body_str = ""
        if body:
            body_str = json.dumps(body, separators=(",", ":"))
        
        sig = self._sign(method, path, params, body_str)
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        url = f"{self.base_url}{path}?{query}&signature={sig}"
        
        headers = {
            "PIONEX-KEY": self.api_key,
            "PIONEX-SIGNATURE": sig,
            "Content-Type": "application/json" if body else "text/plain",
        }
        
        if method == "GET":
            resp = self._http.get(url, headers=headers)
        elif method == "POST":
            resp = self._http.post(url, headers=headers, content=body_str)
        elif method == "DELETE":
            resp = self._http.delete(url, headers=headers)
        else:
            raise ValueError(f"Unsupported method {method}")
            
        return resp.json()

    def get_klines(self, symbol: str, interval: str, limit: int = 500) -> list[dict]:
        params = {"symbol": symbol, "interval": interval, "limit": str(limit)}
        resp = self._http.get(f"{self.base_url}/api/v1/market/klines", params=params)
        data = resp.json()
        return data.get("data", {}).get("klines", []) if data.get("result") else []

    def get_position(self, symbol: str) -> dict | None:
        data = self._request("GET", "/uapi/v1/account/positions", {"symbol": symbol})
        if data.get("result"):
            positions = data.get("data", {}).get("positions", [])
            for p in positions:
                if p["symbol"] == symbol:
                    return p
        return None

    def set_leverage(self, symbol: str, leverage: int):
        return self._request("POST", "/uapi/v1/account/leverage", body={"symbol": symbol, "leverage": str(leverage)})

    def create_order(self, symbol: str, side: str, order_type: str, size: str):
        body = {"symbol": symbol, "side": side, "type": order_type, "size": size}
        return self._request("POST", "/uapi/v1/trade/order", body=body) # For spot, but uapi/v1/trade/order for futures? 
        # Wait, Pionex client.py used /uapi/v1/trade/order for futures.

    def create_futures_order(self, symbol: str, side: str, order_type: str, size: str):
        body = {"symbol": symbol, "side": side, "type": order_type, "size": size}
        return self._request("POST", "/uapi/v1/trade/order", body=body)

    def close(self):
        self._http.close()


# ── Processing Logic ────────────────────────────────────

def fetch_closes(client: PerpAPIClient, symbol: str, interval: str, limit: int) -> list[float]:
    klines = client.get_klines(symbol, interval, limit + 50)
    return [float(k["close"]) for k in sorted(klines, key=lambda x: x["time"])]

def process_perp_bot(
    client: PerpAPIClient,
    bot_name: str,
    bot_cfg: dict,
    global_cfg: dict,
    dry_run: bool,
    log: logging.Logger,
) -> str:
    symbol = bot_cfg["symbol"]
    lb_p = bot_cfg["liu_bei"]
    gy_p = bot_cfg["guan_yu"]
    zf_p = bot_cfg["zhang_fei"]
    dist_pct = bot_cfg.get("dist_pct", 2.0)
    disable_bounce = bot_cfg.get("disable_bounce", True)
    interval = global_cfg.get("interval", "15M")
    leverage = bot_cfg.get("leverage", 5)

    log.info("[%s] --- Checking %s (15M) ---", bot_name.upper(), symbol)

    # 1. Fetch data & calc signal
    closes = fetch_closes(client, symbol, interval, lb_p + 10)
    if len(closes) < lb_p + 1:
        log.error("[%s] Insufficient data", bot_name.upper())
        return "ERROR"
    
    lb_val = sma(closes, lb_p)
    gy_val = sma(closes, gy_p)
    zf_val = sma(closes, zf_p)
    price = closes[-1]
    
    raw_signal = calc_raw_signal(price, lb_val, gy_val, zf_val, dist_pct, disable_bounce)
    raw_dir = SIG_DIR.get(raw_signal, 0)
    
    # 2. State management
    state_data = load_state(bot_name)
    sig_state_code = state_data.get("sig_state", 0)
    
    if not state_data:
        log.info("[%s] Initializing state...", bot_name.upper())
        replayed = replay_signal_state(closes, lb_p, gy_p, zf_p, dist_pct, disable_bounce)
        save_state(bot_name, {
            "sig_state": replayed.sig_state,
            "current_direction": replayed.current_direction,
            "last_check": datetime.now(timezone.utc).isoformat(),
            "initialized": True,
        })
        return "SKIP"

    signal_state = SignalState(sig_state_code)
    direction_changed, new_dir = signal_state.update(raw_signal)

    # 3. Check current position
    position = client.get_position(symbol)
    pos_amount = float(position["number"]) if position else 0.0
    pos_trend = position["side"].lower() if position and pos_amount != 0 else "none" # BUY/SELL
    
    log.info("[%s] Signal: %s | Pos: %s (%s)", 
             bot_name.upper(), SIG_NAMES.get(raw_signal), pos_trend, pos_amount)

    target_trend = "long" if new_dir == 1 else ("short" if new_dir == -1 else "none")
    
    # Check if we need to flip or open
    needs_action = False
    if direction_changed:
        needs_action = True
    elif pos_trend == "none" and target_trend != "none":
        needs_action = True
    
    if not needs_action:
        log.info("[%s] Standing by.", bot_name.upper())
        save_state(bot_name, {**state_data, "sig_state": signal_state.sig_state, "last_check": datetime.now(timezone.utc).isoformat()})
        return "KEEP"

    log.info("[%s] >>> ACTION REQUIRED: %s -> %s <<<", bot_name.upper(), pos_trend, target_trend)

    if dry_run:
        log.info("[%s] [DRY RUN] Would execute %s order", bot_name.upper(), target_trend)
        return "FLIP"

    # 4. EXECUTION
    try:
        # Set leverage first
        client.set_leverage(symbol, leverage)
        
        # 1. Close existing if any
        if pos_trend != "none":
            log.info("[%s] Closing %s position...", bot_name.upper(), pos_trend)
            side = "SELL" if pos_trend == "buy" or pos_trend == "long" else "BUY"
            res = client.create_futures_order(symbol, side, "MARKET", str(abs(pos_amount)))
            if not res.get("result"):
                log.error("[%s] Close failed: %s", bot_name.upper(), res)
                return "ERROR"
            time.sleep(2)

        # 2. Open new
        if target_trend != "none":
            investment = float(bot_cfg.get("investment", 100))
            # Calculate size: (investment * leverage) / price
            size = (investment * leverage) / price
            # Round size (needs to match symbol precision, simplified here)
            size_str = f"{size:.4f}" 
            
            side = "BUY" if target_trend == "long" else "SELL"
            log.info("[%s] Opening %s position (size %s)...", bot_name.upper(), target_trend, size_str)
            res = client.create_futures_order(symbol, side, "MARKET", size_str)
            if not res.get("result"):
                log.error("[%s] Open failed: %s", bot_name.upper(), res)
                return "ERROR"
            
            get_notifier().notify_flip(bot_name, pos_trend.upper(), target_trend.upper(), price, symbol)

        save_state(bot_name, {
            "sig_state": signal_state.sig_state,
            "current_direction": new_dir,
            "last_check": datetime.now(timezone.utc).isoformat(),
            "initialized": True,
        })
        return "FLIP"

    except Exception as e:
        log.error("[%s] Execution error: %s", bot_name.upper(), e)
        return "ERROR"


# ── Entry Point ─────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="三刀流 v6 Perp Manager")
    parser.add_argument("--config", type=str, default="perp_bots_15m.toml")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = load_toml(str(Path(__file__).parent / args.config))
    global_cfg = cfg.get("global", {})
    bots_cfg = cfg.get("bots", {})
    
    log_name = Path(args.config).stem
    log = setup_logger(log_name, global_cfg.get("log_level", "INFO"))
    
    dry_run = args.dry_run or global_cfg.get("dry_run", True)

    log.info("=" * 60)
    log.info("三刀流 v6 Perp Manager (No Grid)")
    log.info("Config: %s | Mode: %s", args.config, "DRY" if dry_run else "LIVE")
    log.info("=" * 60)

    def run_cycle():
        client = PerpAPIClient()
        for name, bcfg in bots_cfg.items():
            try:
                process_perp_bot(client, name, bcfg, global_cfg, dry_run, log)
            except Exception as e:
                log.exception("[%s] Critical error", name.upper())
        client.close()

    if args.loop:
        poll = global_cfg.get("poll_minutes", 15)
        while True:
            run_cycle()
            now = time.time()
            next_check = ((int(now) // (poll * 60)) + 1) * (poll * 60) + 30
            wait = max(next_check - now, 10)
            log.info("Sleeping %.0f seconds...", wait)
            time.sleep(wait)
    else:
        run_cycle()

if __name__ == "__main__":
    main()
