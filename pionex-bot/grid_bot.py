"""
Pionex Futures Grid Trading Bot.

Manages a grid of limit orders on perpetual contracts.
Polls order status and replaces filled orders with counter-orders.
"""
from __future__ import annotations

import signal
import sys
import time
import json

from client import PionexClient, PionexAPIError
from grid_strategy import FuturesGridStrategy, GridMode, GridOrderSide, GridLevel
from logger import setup_logger

log = setup_logger("grid_bot")


class GridConfig:
    """Grid bot configuration from environment or defaults."""
    def __init__(self):
        import os
        from dotenv import load_dotenv
        load_dotenv()

        # API
        self.API_KEY = os.getenv("PIONEX_API_KEY", "")
        self.API_SECRET = os.getenv("PIONEX_API_SECRET", "")
        self.BASE_URL = "https://api.pionex.com"
        self.WS_PUBLIC_URL = "wss://ws.pionex.com/wsPub"
        self.WS_PRIVATE_URL = "wss://ws.pionex.com/ws"

        # Grid parameters
        self.SYMBOL = os.getenv("GRID_SYMBOL", "BTC_USDT_PERP")
        self.UPPER_PRICE = float(os.getenv("GRID_UPPER_PRICE", "90000"))
        self.LOWER_PRICE = float(os.getenv("GRID_LOWER_PRICE", "80000"))
        self.GRID_COUNT = int(os.getenv("GRID_COUNT", "20"))
        self.SIZE_PER_GRID = float(os.getenv("GRID_SIZE", "0.001"))
        self.LEVERAGE = int(os.getenv("GRID_LEVERAGE", "10"))
        self.GRID_MODE = os.getenv("GRID_MODE", "NEUTRAL").upper()

        # Risk
        self.STOP_LOSS_PCT = float(os.getenv("GRID_STOP_LOSS_PCT", "5.0"))
        self.TRAILING_UP = os.getenv("GRID_TRAILING_UP", "false").lower() == "true"
        self.TRAILING_DOWN = os.getenv("GRID_TRAILING_DOWN", "false").lower() == "true"

        # System
        self.POLL_INTERVAL = int(os.getenv("GRID_POLL_INTERVAL", "10"))
        self.DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
        self.LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


class FuturesGridBot:
    def __init__(self):
        self.cfg = GridConfig()
        self.client = PionexClient(self.cfg)
        self.strategy = FuturesGridStrategy(
            upper_price=self.cfg.UPPER_PRICE,
            lower_price=self.cfg.LOWER_PRICE,
            grid_count=self.cfg.GRID_COUNT,
            size_per_grid=self.cfg.SIZE_PER_GRID,
            mode=GridMode[self.cfg.GRID_MODE],
            leverage=self.cfg.LEVERAGE,
            stop_loss_pct=self.cfg.STOP_LOSS_PCT,
            trailing_up=self.cfg.TRAILING_UP,
            trailing_down=self.cfg.TRAILING_DOWN,
        )
        self._running = False
        self._order_map: dict[str, int] = {}  # order_id → level_index

    # ── Setup ─────────────────────────────────────────────────────

    def _validate(self):
        if not self.cfg.API_KEY or not self.cfg.API_SECRET:
            log.error("PIONEX_API_KEY and PIONEX_API_SECRET required")
            sys.exit(1)

    def _setup_leverage(self):
        """Set leverage on exchange."""
        if self.cfg.DRY_RUN:
            log.info("[DRY RUN] Would set leverage to %dx", self.cfg.LEVERAGE)
            return
        try:
            self.client.modify_leverage(self.cfg.SYMBOL, self.cfg.LEVERAGE)
            log.info("Leverage set to %dx for %s", self.cfg.LEVERAGE, self.cfg.SYMBOL)
        except PionexAPIError as e:
            log.warning("Failed to set leverage: %s", e)

    def _get_current_price(self) -> float:
        ticker = self.client.get_ticker(self.cfg.SYMBOL)
        return float(ticker.get("close", 0))

    # ── Order Placement ───────────────────────────────────────────

    def _place_grid_order(self, level: GridLevel) -> bool:
        """Place a limit order for a grid level."""
        side = level.side.value
        size_str = f"{self.cfg.SIZE_PER_GRID:.8f}".rstrip("0").rstrip(".")
        price_str = level.price_str

        if self.cfg.DRY_RUN:
            fake_id = f"dry-{level.index}-{int(time.time())}"
            self.strategy.mark_order_placed(level.index, fake_id)
            self._order_map[fake_id] = level.index
            log.info("[DRY RUN] Grid[%d] %s limit @ %s (size: %s)",
                     level.index, side, price_str, size_str)
            return True

        try:
            result = self.client.new_futures_order(
                symbol=self.cfg.SYMBOL,
                side=side,
                order_type="LIMIT",
                size=size_str,
                price=price_str,
            )
            order_id = str(result.get("orderId", ""))
            self.strategy.mark_order_placed(level.index, order_id)
            self._order_map[order_id] = level.index
            log.info("Grid[%d] %s limit placed @ %s [%s]",
                     level.index, side, price_str, order_id)
            return True
        except PionexAPIError as e:
            log.error("Failed to place Grid[%d] %s @ %s: %s",
                      level.index, side, price_str, e)
            return False

    def _place_initial_grid(self, current_price: float):
        """Create and place the initial grid of orders."""
        levels = self.strategy.create_grid_levels(current_price)
        orders_to_place = self.strategy.get_orders_to_place(current_price)

        log.info("Placing %d initial grid orders...", len(orders_to_place))
        placed = 0
        for level in orders_to_place:
            if self._place_grid_order(level):
                placed += 1
            time.sleep(0.2)  # Rate limit protection

        log.info("Initial grid: %d/%d orders placed", placed, len(orders_to_place))

    # ── Order Monitoring ──────────────────────────────────────────

    def _check_filled_orders(self, current_price: float):
        """Check for filled orders and place counter-orders."""
        if self.cfg.DRY_RUN:
            self._simulate_fills(current_price)
            return

        try:
            # Get all open orders
            open_orders = self.client.get_futures_open_orders(self.cfg.SYMBOL)
            open_ids = {str(o.get("orderId", "")) for o in open_orders}

            # Check which tracked orders are no longer open (= filled)
            filled_order_ids = []
            for order_id, level_idx in list(self._order_map.items()):
                if order_id not in open_ids:
                    filled_order_ids.append((order_id, level_idx))

            for order_id, level_idx in filled_order_ids:
                log.info("Order %s (Grid[%d]) filled!", order_id, level_idx)
                del self._order_map[order_id]

                # Process fill and get counter-order
                counter_level = self.strategy.process_fill(level_idx, current_price)
                if counter_level:
                    self._place_grid_order(counter_level)
                    time.sleep(0.2)

        except PionexAPIError as e:
            log.error("Failed to check orders: %s", e)

    def _simulate_fills(self, current_price: float):
        """In DRY_RUN mode, simulate fills when price crosses grid levels."""
        for lv in self.strategy.state.levels:
            if not lv.is_active or not lv.order_id:
                continue

            filled = False
            if lv.side == GridOrderSide.BUY and current_price <= lv.price:
                filled = True
            elif lv.side == GridOrderSide.SELL and current_price >= lv.price:
                filled = True

            if filled:
                log.info("[DRY RUN] Grid[%d] %s @ %.2f FILLED (price: %.2f)",
                         lv.index, lv.side.value, lv.price, current_price)

                if lv.order_id in self._order_map:
                    del self._order_map[lv.order_id]

                counter_level = self.strategy.process_fill(lv.index, current_price)
                if counter_level:
                    self._place_grid_order(counter_level)

    # ── Cancel All ────────────────────────────────────────────────

    def _cancel_all_grid_orders(self):
        """Cancel all active grid orders."""
        if self.cfg.DRY_RUN:
            log.info("[DRY RUN] Would cancel all grid orders")
            for lv in self.strategy.state.levels:
                lv.is_active = False
                lv.order_id = None
            self._order_map.clear()
            return

        try:
            self.client.cancel_all_futures_orders(self.cfg.SYMBOL)
            for lv in self.strategy.state.levels:
                lv.is_active = False
                lv.order_id = None
            self._order_map.clear()
            log.info("All grid orders cancelled")
        except PionexAPIError as e:
            log.error("Failed to cancel orders: %s", e)

    # ── Main Loop ─────────────────────────────────────────────────

    def _print_status(self, current_price: float):
        stats = self.strategy.get_stats()
        range_status = self.strategy.check_out_of_range(current_price)

        log.info("─" * 50)
        log.info("Price: %.2f | Range: %s | Status: %s",
                 current_price, stats["grid_range"], range_status)
        log.info("Active: %d (B:%d S:%d) | Trades: %d | Profit: $%.4f",
                 stats["active_orders"], stats["buy_active"], stats["sell_active"],
                 stats["total_trades"], stats["net_profit"])
        log.info("─" * 50)

    def run(self):
        """Start the grid trading bot."""
        self._validate()
        self._running = True

        # Setup leverage
        self._setup_leverage()

        # Get initial price
        current_price = self._get_current_price()
        if current_price <= 0:
            log.error("Failed to get current price")
            sys.exit(1)

        # Show startup info
        log.info("=" * 60)
        log.info("Pionex Futures Grid Bot started")
        log.info("  Symbol:     %s", self.cfg.SYMBOL)
        log.info("  Grid:       %.2f - %.2f (%d levels)",
                 self.cfg.LOWER_PRICE, self.cfg.UPPER_PRICE, self.cfg.GRID_COUNT)
        log.info("  Spacing:    $%.2f", self.strategy.grid_spacing)
        log.info("  Size/grid:  %.6f", self.cfg.SIZE_PER_GRID)
        log.info("  Mode:       %s | Leverage: %dx", self.cfg.GRID_MODE, self.cfg.LEVERAGE)
        log.info("  Poll:       %ds", self.cfg.POLL_INTERVAL)
        log.info("  Dry run:    %s", self.cfg.DRY_RUN)
        log.info("  Price now:  %.2f", current_price)
        log.info("=" * 60)

        # Create initial grid if not already created
        if not self.strategy.state.grid_created:
            self._place_initial_grid(current_price)
        else:
            log.info("Resuming existing grid (%d levels)", len(self.strategy.state.levels))
            # Re-place any missing orders
            orders_to_place = self.strategy.get_orders_to_place(current_price)
            if orders_to_place:
                log.info("Re-placing %d missing orders", len(orders_to_place))
                for level in orders_to_place:
                    self._place_grid_order(level)
                    time.sleep(0.2)

        # Main polling loop
        tick_count = 0
        while self._running:
            try:
                current_price = self._get_current_price()

                # Stop loss check
                if self.strategy.check_stop_loss(current_price):
                    log.warning("EMERGENCY STOP: Cancelling all orders!")
                    self._cancel_all_grid_orders()
                    break

                # Check for filled orders
                self._check_filled_orders(current_price)

                # Re-place missing orders
                orders_to_place = self.strategy.get_orders_to_place(current_price)
                for level in orders_to_place:
                    self._place_grid_order(level)
                    time.sleep(0.2)

                # Print status every 6 ticks (~ every minute at 10s interval)
                tick_count += 1
                if tick_count % 6 == 0:
                    self._print_status(current_price)

            except KeyboardInterrupt:
                break
            except Exception:
                log.exception("Tick error")

            try:
                time.sleep(self.cfg.POLL_INTERVAL)
            except KeyboardInterrupt:
                break

        # Shutdown
        log.info("Grid bot stopping...")
        stats = self.strategy.get_stats()
        log.info("Final stats: %s", json.dumps(stats, indent=2))
        self.client.close()
        log.info("Grid bot stopped")

    def stop(self):
        self._running = False


def main():
    bot = FuturesGridBot()

    def _signal_handler(sig, frame):
        log.info("Received shutdown signal")
        bot.stop()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    bot.run()


if __name__ == "__main__":
    main()
