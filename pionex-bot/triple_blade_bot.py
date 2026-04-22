"""
三刀流 (Triple Blade) Trading Bot
==================================
Uses MA(7) / MA(25) / MA(99) triple moving average strategy.
When blades align → creates a Pionex Futures Grid Bot via Bot API.
When signal reverses → cancels old grid, creates new one.

Usage:
    python triple_blade_bot.py          # DRY_RUN=true by default
    DRY_RUN=false python triple_blade_bot.py   # LIVE trading
"""
from __future__ import annotations

import json
import signal
import sys
import time
from pathlib import Path

from client import PionexClient, PionexAPIError
from config import Config
from logger import setup_logger
from utils import file_lock
from strategy import TripleMAStrategy, BladeSignal

log = setup_logger("triple_blade")

# Interval → seconds mapping
INTERVAL_SECONDS = {
    "1M": 60, "5M": 300, "15M": 900, "30M": 1800,
    "60M": 3600, "4H": 14400, "8H": 28800, "1D": 86400,
}

STATE_FILE = Path("triple_blade_state.json")


class TripleBladeBot:
    """三刀流 bot: monitors MA signals and manages Pionex futures grid bots."""

    def __init__(self):
        self.cfg = Config()
        self.client = PionexClient(self.cfg)
        self.strategy = TripleMAStrategy(
            fast=self.cfg.BLADE_MA_FAST,
            mid=self.cfg.BLADE_MA_MID,
            slow=self.cfg.BLADE_MA_SLOW,
        )
        self._running = False

        # Active grid bot state
        self._active_grid_id: str | None = None
        self._active_trend: str | None = None  # "long" or "short"
        self._grid_create_time: float = 0
        self._signal_history: list[dict] = []

        self._load_state()

    # ── State Persistence ────────────────────────────────────────

    def _save_state(self):
        state = {
            "active_grid_id": self._active_grid_id,
            "active_trend": self._active_trend,
            "grid_create_time": self._grid_create_time,
            "signal_history": self._signal_history[-50:],  # Keep last 50
        }
        try:
            with file_lock(STATE_FILE):
                STATE_FILE.write_text(json.dumps(state, indent=2))
        except Exception as e:
            log.error("Failed to save state: %s", e)

    def _load_state(self):
        if STATE_FILE.exists():
            try:
                state = json.loads(STATE_FILE.read_text())
                self._active_grid_id = state.get("active_grid_id")
                self._active_trend = state.get("active_trend")
                self._grid_create_time = state.get("grid_create_time", 0)
                self._signal_history = state.get("signal_history", [])
                if self._active_grid_id:
                    log.info("Resumed state: grid=%s trend=%s", self._active_grid_id, self._active_trend)
            except Exception as e:
                log.warning("Failed to load state: %s", e)

    # ── Grid Bot Management ──────────────────────────────────────

    # Statuses that mean the bot is still holding/placing orders on Pionex.
    ACTIVE_STATUSES = ("open", "running", "prepare", "preparing")

    # Q-SIGNALS signal → direction. Mirrors qsignals_bot_manager.DIR_MAP.
    _QS_DIR_MAP = {"BUY": "long", "CLOSE_SHORT": "long",
                   "SELL": "short", "CLOSE_LONG": "short",
                   "HOLD": "flat"}

    def _qsignals_consensus(self, klines: list[dict]) -> tuple[str, int, int]:
        """Return (direction, agree, total). direction ∈ {long, short, no_trend}.
        Runs all Q-SIGNALS strategies calibrated for this symbol; majority
        vote decides the grid direction. `no_trend` when coverage/agree is
        insufficient."""
        try:
            from qsignals_adapter import evaluate, strategies_for_symbol
        except Exception as e:
            log.warning("Q-SIGNALS adapter not importable: %s", e)
            return "no_trend", 0, 0

        strategies = strategies_for_symbol(self.cfg.BLADE_SYMBOL)
        if not strategies:
            log.info("Q-SIGNALS has no coverage for %s, using blade direction.",
                     self.cfg.BLADE_SYMBOL)
            return "no_trend", 0, 0

        # Map Pionex symbol format to Q-SIGNALS expected format.
        qs_symbol = self.cfg.BLADE_SYMBOL.upper().replace("_USDT_PERP", "USDT")
        counts = {"long": 0, "short": 0, "flat": 0}
        total = 0
        for sid in strategies:
            try:
                r = evaluate(sid, qs_symbol, self.cfg.BLADE_INTERVAL, klines)
                sig = (r or {}).get("signal", "HOLD")
                direction = self._QS_DIR_MAP.get(sig, "flat")
                counts[direction] += 1
                total += 1
            except Exception as e:
                log.debug("Q-SIGNALS %s failed for %s: %s", sid, qs_symbol, e)
                continue

        if total == 0:
            return "no_trend", 0, 0
        # Prefer long/short over flat — flat doesn't produce a direction.
        best = max(("long", "short"), key=lambda k: counts[k])
        agree = counts[best]
        min_agree = max(1, int(total * self.cfg.BLADE_QSIGNALS_MIN_AGREE))
        if agree < min_agree:
            log.info("Q-SIGNALS consensus too weak: %s %d/%d (need ≥%d) → no_trend",
                     best, agree, total, min_agree)
            return "no_trend", agree, total
        log.info("Q-SIGNALS consensus: %s (%d/%d, flat=%d)",
                 best, agree, total, counts["flat"])
        return best, agree, total

    def _is_still_active(self, bu_order_id: str) -> bool:
        """Return True if Pionex still shows this grid in an active state.
        Used after cancel to confirm it actually closed before opening a
        replacement (prevents duplicate bots during Pionex's cancel window)."""
        if not bu_order_id or self.cfg.DRY_RUN:
            return False
        try:
            info = self.client.bot_futures_grid_get(bu_order_id)
            data = (info or {}).get("data") or info or {}
            status = str(data.get("status") or "").lower()
            return status in self.ACTIVE_STATUSES
        except PionexAPIError as e:
            # On API error, assume still active (conservative — avoids race).
            log.warning("_is_still_active check failed for %s: %s "
                        "(assuming active to be safe)", bu_order_id, e)
            return True

    def _verify_active_grid(self) -> None:
        """Cross-check local state against Pionex. Clears stale active_grid_id
        if the tracked bot is no longer running (manual cancel, liquidation,
        auto-close, crash between cancel & save). Must run before any
        create/cancel decision — local state alone can't be trusted."""
        if not self._active_grid_id or self.cfg.DRY_RUN:
            return
        try:
            info = self.client.bot_futures_grid_get(self._active_grid_id)
            data = (info or {}).get("data") or info or {}
            status = str(data.get("status") or "").lower()
            if status and status not in self.ACTIVE_STATUSES:
                log.warning("State drift: local tracks %s as active but "
                            "Pionex reports status=%s → clearing.",
                            self._active_grid_id, status)
                self._active_grid_id = None
                self._active_trend = None
                self._save_state()
        except PionexAPIError as e:
            log.warning("Could not verify grid %s: %s", self._active_grid_id, e)
            # On API error, keep state as-is — don't risk clearing a live bot.

    def _create_grid(self, trend: str, current_price: float) -> str | None:
        """Create a futures grid bot with the given trend direction."""
        # Hard safety: refuse to create if we already track an active bot.
        # This prevents duplicate bots if state was not properly cleared
        # after a failed cancel or an out-of-band manual action.
        if self._active_grid_id:
            log.error(
                "ABORT create: already tracking active grid %s (trend=%s). "
                "Cancel it first.",
                self._active_grid_id, self._active_trend,
            )
            return None

        range_pct = self.cfg.BLADE_RANGE_PCT / 100
        top_price = current_price * (1 + range_pct)
        bottom_price = current_price * (1 - range_pct)

        # Format prices (2 decimal places for ETH)
        top_str = f"{top_price:.2f}"
        bottom_str = f"{bottom_price:.2f}"

        log.info(
            "Creating %s grid: %s - %s | %d grids | %sx leverage | %s USDT",
            trend.upper(), bottom_str, top_str,
            self.cfg.BLADE_GRID_COUNT, self.cfg.BLADE_LEVERAGE, self.cfg.BLADE_INVESTMENT,
        )

        if self.cfg.DRY_RUN:
            fake_id = f"dry-{trend}-{int(time.time())}"
            log.info("[DRY RUN] Would create %s grid bot → %s", trend, fake_id)
            return fake_id

        try:
            result = self.client.bot_futures_grid_create(
                base=self.cfg.BLADE_BASE,
                quote=self.cfg.BLADE_QUOTE,
                top=top_str,
                bottom=bottom_str,
                row=self.cfg.BLADE_GRID_COUNT,
                grid_type=self.cfg.BLADE_GRID_TYPE,
                trend=trend,
                leverage=self.cfg.BLADE_LEVERAGE,
                quote_investment=self.cfg.BLADE_INVESTMENT,
                loss_stop_type=self.cfg.BLADE_LOSS_STOP_TYPE or None,
                loss_stop=self.cfg.BLADE_LOSS_STOP or None,
                profit_stop_type=self.cfg.BLADE_PROFIT_STOP_TYPE or None,
                profit_stop=self.cfg.BLADE_PROFIT_STOP or None,
            )
            bu_order_id = result.get("buOrderId") or result.get("data", {}).get("buOrderId")
            if bu_order_id:
                log.info("✅ Grid bot created: %s (trend=%s)", bu_order_id, trend)
                return str(bu_order_id)
            else:
                log.warning("Grid created but no buOrderId in response: %s", result)
                return None
        except PionexAPIError as e:
            log.error("❌ Failed to create %s grid: %s", trend, e)
            return None

    def _cancel_grid(self, bu_order_id: str) -> bool:
        """Cancel an active futures grid bot."""
        log.info("Cancelling grid bot: %s", bu_order_id)

        if self.cfg.DRY_RUN:
            log.info("[DRY RUN] Would cancel grid bot %s", bu_order_id)
            return True

        try:
            self.client.bot_futures_grid_cancel(bu_order_id)
            log.info("✅ Grid bot cancelled: %s", bu_order_id)
            return True
        except PionexAPIError as e:
            log.error("❌ Failed to cancel grid %s: %s", bu_order_id, e)
            return False

    def _check_grid_status(self) -> dict | None:
        """Check the status of the active grid bot."""
        if not self._active_grid_id or self.cfg.DRY_RUN:
            return None
        try:
            return self.client.bot_futures_grid_get(self._active_grid_id)
        except PionexAPIError as e:
            log.warning("Failed to check grid %s: %s", self._active_grid_id, e)
            return None

    # ── Signal Processing ────────────────────────────────────────

    def _should_act(self, signal: BladeSignal) -> bool:
        """Determine if signal is strong enough to act on."""
        return signal.strength >= self.cfg.BLADE_MIN_STRENGTH

    def _needs_reversal(self, signal: BladeSignal) -> bool:
        """Check if we need to reverse the current grid direction."""
        if not self._active_grid_id or not self._active_trend:
            return False

        # Current grid is long but signal says short (or vice versa)
        if self._active_trend == "long" and signal.is_short and self._should_act(signal):
            return True
        if self._active_trend == "short" and signal.is_long and self._should_act(signal):
            return True

        return False

    def _pick_direction(self, blade_signal: BladeSignal,
                        klines: list[dict]) -> str:
        """Decide the grid direction for a new/flip bot.

        Default: blade_signal.trend (long/short).
        If BLADE_USE_QSIGNALS_DIR=true, Q-SIGNALS consensus overrides — the
        blade signal is still used to gate WHEN to act, but DIRECTION follows
        the strategy consensus (which generally has better risk-adjusted edge
        than a single MA cross)."""
        blade_dir = blade_signal.trend  # "long" | "short" | "no_trend"
        if not self.cfg.BLADE_USE_QSIGNALS_DIR or not klines:
            return blade_dir
        qs_dir, agree, total = self._qsignals_consensus(klines)
        if qs_dir in ("long", "short"):
            if qs_dir != blade_dir:
                log.info("🧭 Direction override: blade=%s → Q-SIGNALS=%s (%d/%d)",
                         blade_dir, qs_dir, agree, total)
            return qs_dir
        # Q-SIGNALS inconclusive — fall back to blade signal.
        log.info("Q-SIGNALS inconclusive; using blade direction %s.", blade_dir)
        return blade_dir

    def _process_signal(self, signal: BladeSignal, current_price: float,
                        klines: list[dict] | None = None):
        """Process a blade signal and manage grid bots accordingly."""
        # First: sync local state with Pionex truth. A bot that was manually
        # cancelled on the app, liquidated, or auto-closed must be cleared
        # before we decide whether to flip or open a new grid.
        self._verify_active_grid()

        # Record signal
        self._signal_history.append({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "signal": signal.value,
            "strength": signal.strength,
            "price": current_price,
        })

        # Case 1: Signal reversal → cancel old grid, then verify, then create
        if self._needs_reversal(signal):
            old_id = self._active_grid_id
            log.info("🔄 SIGNAL REVERSAL: %s → %s (cancelling %s grid)",
                     self._active_trend, signal.trend, old_id)

            if self._cancel_grid(old_id):
                # Pionex needs a moment for the cancel to propagate — during
                # this window the old bot's orders are still resting.
                cooldown = getattr(self.cfg, "BLADE_COOLDOWN_SEC",
                                    getattr(self.cfg, "COOLDOWN_SECONDS", 10))
                log.debug("Cooldown %ds before verifying cancel…", cooldown)
                time.sleep(cooldown)

                # Actively confirm the old bot is no longer active on Pionex.
                # If our cancel succeeded but the bot is still running, DO NOT
                # open a new one — log loudly and bail.
                if self._is_still_active(old_id):
                    log.error(
                        "❌ Cancel reported success but %s is still active "
                        "on Pionex. Aborting reversal to avoid duplicate bot.",
                        old_id,
                    )
                    # Don't clear local state — next tick will re-verify.
                    self._save_state()
                    return

                self._active_grid_id = None
                self._active_trend = None

                # Direction picked from Q-SIGNALS consensus (or blade fallback).
                new_trend = self._pick_direction(signal, klines or [])
                if new_trend == "no_trend":
                    log.info("No clear direction after cancel; staying flat.")
                    self._save_state()
                    return

                new_id = self._create_grid(new_trend, current_price)
                if new_id:
                    self._active_grid_id = new_id
                    self._active_trend = new_trend
                    self._grid_create_time = time.time()
            self._save_state()
            return

        # Case 2: No active grid + strong enough signal → create grid
        if not self._active_grid_id and self._should_act(signal) and signal.trend != "no_trend":
            new_trend = self._pick_direction(signal, klines or [])
            if new_trend == "no_trend":
                log.info("Blade wants to open but Q-SIGNALS says no_trend; skipping.")
                return
            log.info("🆕 NEW GRID: blade=%s (strength=%d) → opening %s grid",
                     signal.value, signal.strength, new_trend)

            new_id = self._create_grid(new_trend, current_price)
            if new_id:
                self._active_grid_id = new_id
                self._active_trend = new_trend
                self._grid_create_time = time.time()
            self._save_state()
            return

        # Case 3: Active grid + signal weakened to HOLD → optionally cancel
        if self._active_grid_id and signal == BladeSignal.HOLD:
            elapsed = time.time() - self._grid_create_time
            # Only cancel on HOLD if grid has been running for > 30 min
            if elapsed > 1800:
                log.info("⏸️ Signal tangled (HOLD) for existing %s grid, keeping active", self._active_trend)
            return

        # Case 4: Active grid + same direction signal → keep running
        if self._active_grid_id:
            log.debug("Grid %s (%s) still active, signal=%s", self._active_grid_id, self._active_trend, signal.value)

    # ── Main Loop ────────────────────────────────────────────────

    def _tick(self):
        """One iteration of the triple blade loop."""
        # 1. Fetch klines (need enough for MA99 + buffer)
        needed = self.cfg.BLADE_MA_SLOW + 5
        klines = self.client.get_klines(
            self.cfg.BLADE_SYMBOL,
            self.cfg.BLADE_INTERVAL,
            limit=needed,
        )
        if not klines:
            log.warning("No klines received for %s", self.cfg.BLADE_SYMBOL)
            return

        current_price = float(klines[-1]["close"])

        # 2. Evaluate triple MA signal (blade signal — gates strength/when)
        signal = self.strategy.evaluate(klines)

        # 3. Process signal. Pass klines so Q-SIGNALS adapter can vote on
        #    DIRECTION (long/short) when opening or flipping a grid.
        self._process_signal(signal, current_price, klines)

    def _print_banner(self):
        log.info("=" * 60)
        log.info("⚔️  三刀流 Triple Blade Trading Bot  ⚔️")
        log.info("=" * 60)
        log.info("  Symbol:      %s", self.cfg.BLADE_SYMBOL)
        log.info("  MA Periods:  %d / %d / %d", self.cfg.BLADE_MA_FAST, self.cfg.BLADE_MA_MID, self.cfg.BLADE_MA_SLOW)
        log.info("  Interval:    %s", self.cfg.BLADE_INTERVAL)
        log.info("  Leverage:    %dx", self.cfg.BLADE_LEVERAGE)
        log.info("  Investment:  %s USDT per grid", self.cfg.BLADE_INVESTMENT)
        log.info("  Grid Count:  %d", self.cfg.BLADE_GRID_COUNT)
        log.info("  Grid Range:  ±%.1f%%", self.cfg.BLADE_RANGE_PCT)
        log.info("  Min Strength: %d (1=weak, 2=medium, 3=strong)", self.cfg.BLADE_MIN_STRENGTH)
        log.info("  Poll:        %ds", self.cfg.BLADE_POLL_SEC)
        log.info("  Loss Stop:   %s (%s)", self.cfg.BLADE_LOSS_STOP, self.cfg.BLADE_LOSS_STOP_TYPE)
        log.info("  Profit Stop: %s (%s)", self.cfg.BLADE_PROFIT_STOP, self.cfg.BLADE_PROFIT_STOP_TYPE)
        log.info("  Dry Run:     %s", self.cfg.DRY_RUN)
        if self._active_grid_id:
            log.info("  Active Grid: %s (%s)", self._active_grid_id, self._active_trend)
        log.info("=" * 60)

    def run(self):
        """Start the triple blade bot."""
        if not self.cfg.API_KEY or not self.cfg.API_SECRET:
            log.error("API_KEY and API_SECRET must be set in .env")
            sys.exit(1)

        self._running = True
        self._print_banner()

        # Get initial price for startup log
        try:
            ticker = self.client.get_ticker(self.cfg.BLADE_SYMBOL)
            price = float(ticker.get("close", 0))
            log.info("Current %s price: $%.2f", self.cfg.BLADE_SYMBOL, price)
        except Exception as e:
            log.warning("Failed to get initial price: %s", e)

        tick_count = 0
        while self._running:
            try:
                self._tick()
                tick_count += 1

                # Print status every 10 ticks
                if tick_count % 10 == 0:
                    status = "ACTIVE" if self._active_grid_id else "WAITING"
                    grid_info = f" [{self._active_trend} grid: {self._active_grid_id}]" if self._active_grid_id else ""
                    log.info("📊 Tick #%d | Status: %s%s", tick_count, status, grid_info)

            except KeyboardInterrupt:
                break
            except Exception:
                log.exception("Tick error")

            try:
                time.sleep(self.cfg.BLADE_POLL_SEC)
            except KeyboardInterrupt:
                break

        # Shutdown
        log.info("Triple Blade bot stopping...")
        self._save_state()
        self.client.close()
        log.info("Triple Blade bot stopped. State saved.")

    def stop(self):
        self._running = False


def main():
    bot = TripleBladeBot()

    def _signal_handler(sig, frame):
        log.info("Received shutdown signal")
        bot.stop()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    bot.run()


if __name__ == "__main__":
    main()
