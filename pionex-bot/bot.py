"""Main trading bot: polling loop that fetches klines, evaluates strategy, and executes trades."""

import asyncio
import signal
import sys
import time

from client import PionexClient, PionexAPIError
from config import Config
from logger import setup_logger
from risk import RiskManager
from strategy import MACrossStrategy, Signal

log = setup_logger("bot")

# Interval → seconds mapping for sleep calculation
INTERVAL_SECONDS = {
    "1M": 60, "5M": 300, "15M": 900, "30M": 1800,
    "60M": 3600, "4H": 14400, "8H": 28800, "12H": 43200, "1D": 86400,
}


class TradingBot:
    def __init__(self):
        self.cfg = Config()
        self.client = PionexClient(self.cfg)
        self.strategy = MACrossStrategy(self.cfg)
        self.risk = RiskManager(cfg=self.cfg)
        self._running = False

    def _validate_config(self):
        if not self.cfg.API_KEY or not self.cfg.API_SECRET:
            log.error("API_KEY and API_SECRET must be set in .env")
            sys.exit(1)
        if self.cfg.KLINE_INTERVAL not in INTERVAL_SECONDS:
            log.error("Invalid KLINE_INTERVAL: %s", self.cfg.KLINE_INTERVAL)
            sys.exit(1)

    def _get_current_price(self) -> float:
        ticker = self.client.get_ticker(self.cfg.SYMBOL)
        return float(ticker.get("close", 0))

    def _execute_open(self, side: str, price: float):
        size = self.risk.calculate_size(price)
        size_str = f"{size:.8f}".rstrip("0").rstrip(".")

        if self.cfg.DRY_RUN:
            log.info("[DRY RUN] Would %s %.6f %s @ %.2f", side, size, self.cfg.SYMBOL, price)
            self.risk.open_position(side, size, price, order_id="dry-run")
            return

        try:
            # Use futures endpoint for PERP symbols
            result = self.client.new_futures_order(
                symbol=self.cfg.SYMBOL,
                side=side,
                order_type="MARKET",
                size=size_str,
            )
            order_id = str(result.get("orderId", ""))
            self.risk.open_position(side, size, price, order_id=order_id)
            log.info("Futures order placed: %s %s [%s]", side, size_str, order_id)
        except PionexAPIError as e:
            log.error("Failed to open %s: %s", side, e)

    def _execute_close(self, price: float):
        pos = self.risk.position
        size_str = f"{pos.size:.8f}".rstrip("0").rstrip(".")

        # Close by opening opposite side
        close_side = "SELL" if pos.side == "BUY" else "BUY"

        if self.cfg.DRY_RUN:
            pnl = self.risk.close_position(price)
            log.info("[DRY RUN] Would close %s %.6f @ %.2f (PnL: %.2f%%)", close_side, pos.size, price, pnl)
            return

        try:
            if close_side == "SELL":
                self.client.futures_market_sell(self.cfg.SYMBOL, size_str)
            else:
                self.client.futures_market_buy(self.cfg.SYMBOL, size_str)
            pnl = self.risk.close_position(price)
            log.info("Position closed via %s @ %.2f (PnL: %.2f%%)", close_side, price, pnl)
        except PionexAPIError as e:
            log.error("Failed to close position: %s", e)

    def _tick(self):
        """One iteration of the trading loop."""
        # 1. Fetch klines
        needed = self.cfg.SLOW_MA_PERIOD + 5
        klines = self.client.get_klines(self.cfg.SYMBOL, self.cfg.KLINE_INTERVAL, limit=needed)
        if not klines:
            log.warning("No klines received")
            return

        current_price = float(klines[-1]["close"])

        # 2. Check stop-loss / take-profit for open position
        if self.risk.position.is_open:
            if self.risk.should_close(current_price):
                self._execute_close(current_price)
                return

        # 3. Evaluate strategy signal
        sig = self.strategy.evaluate(klines)

        # 4. Execute based on signal
        if sig == Signal.BUY:
            if self.risk.position.is_open and self.risk.position.side == "SELL":
                # Close short first
                self._execute_close(current_price)
            if self.risk.can_open_position():
                self._execute_open("BUY", current_price)

        elif sig == Signal.SELL:
            if self.risk.position.is_open and self.risk.position.side == "BUY":
                # Close long first
                self._execute_close(current_price)
            if self.risk.can_open_position():
                self._execute_open("SELL", current_price)

    def run(self):
        """Start the polling-based trading loop."""
        self._validate_config()
        self._running = True
        interval_sec = INTERVAL_SECONDS[self.cfg.KLINE_INTERVAL]

        # Show futures account info
        leverage_info = "N/A"
        pos_mode = "N/A"
        futures_bal = "N/A"
        try:
            levs = self.client.get_leverage(self.cfg.SYMBOL)
            if levs:
                leverage_info = f"{levs[0].get('leverage', '?')}x"
            pos_mode = self.client.get_position_mode()
            fb = self.client.get_futures_balance()
            for b in fb.get("balances", []):
                if b.get("coin") == "USDT" or b.get("coin") == "PUSD":
                    futures_bal = f"{b['coin']}={b['free']}"
                    break
        except Exception as e:
            log.warning("Failed to get futures info: %s", e)

        log.info("=" * 60)
        log.info("Pionex Futures Trading Bot started")
        log.info("  Symbol:    %s", self.cfg.SYMBOL)
        log.info("  Leverage:  %s", leverage_info)
        log.info("  Pos Mode:  %s", pos_mode)
        log.info("  Balance:   %s", futures_bal)
        log.info("  Strategy:  MA Cross (fast=%d, slow=%d)", self.cfg.FAST_MA_PERIOD, self.cfg.SLOW_MA_PERIOD)
        log.info("  Interval:  %s (%ds)", self.cfg.KLINE_INTERVAL, interval_sec)
        log.info("  Max size:  %s", self.cfg.MAX_POSITION_SIZE)
        log.info("  SL/TP:     %.1f%% / %.1f%%", self.cfg.STOP_LOSS_PCT, self.cfg.TAKE_PROFIT_PCT)
        log.info("  Dry run:   %s", self.cfg.DRY_RUN)
        log.info("=" * 60)

        # Poll every interval
        while self._running:
            try:
                self._tick()
            except KeyboardInterrupt:
                break
            except Exception:
                log.exception("Tick error")

            # Sleep until next candle (with early wake-up buffer)
            sleep_time = max(interval_sec * 0.9, 10)
            log.debug("Sleeping %.0fs until next tick", sleep_time)
            try:
                time.sleep(sleep_time)
            except KeyboardInterrupt:
                break

        log.info("Bot stopped")
        self.client.close()

    def stop(self):
        self._running = False


def main():
    bot = TradingBot()

    def _signal_handler(sig, frame):
        log.info("Received shutdown signal")
        bot.stop()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    bot.run()


if __name__ == "__main__":
    main()
