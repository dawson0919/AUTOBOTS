"""
Futures Grid Trading Strategy for Pionex.

Places a grid of limit orders across a price range on perpetual contracts.
When a buy order fills → place a sell order one grid level up (take profit).
When a sell order fills → place a buy order one grid level down (take profit).
Supports both long-only, short-only, and neutral (long+short) modes.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional

from logger import setup_logger

log = setup_logger("grid")


class GridMode(Enum):
    LONG = "LONG"       # Only buy low, sell high (bullish)
    SHORT = "SHORT"     # Only sell high, buy low (bearish)
    NEUTRAL = "NEUTRAL" # Both directions (range-bound)


class GridOrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class GridLevel:
    """Represents one price level in the grid."""
    index: int
    price: float
    side: GridOrderSide          # Expected action at this level
    order_id: Optional[str] = None
    is_filled: bool = False
    is_active: bool = False      # Order is live on exchange

    @property
    def price_str(self) -> str:
        return f"{self.price:.2f}"


@dataclass
class GridState:
    """Persistent state of the grid bot."""
    levels: list[GridLevel] = field(default_factory=list)
    total_profit: float = 0.0
    total_trades: int = 0
    total_fees: float = 0.0
    grid_created: bool = False

    def to_dict(self) -> dict:
        return {
            "levels": [
                {
                    "index": lv.index,
                    "price": lv.price,
                    "side": lv.side.value,
                    "order_id": lv.order_id,
                    "is_filled": lv.is_filled,
                    "is_active": lv.is_active,
                }
                for lv in self.levels
            ],
            "total_profit": self.total_profit,
            "total_trades": self.total_trades,
            "total_fees": self.total_fees,
            "grid_created": self.grid_created,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GridState":
        state = cls()
        state.total_profit = data.get("total_profit", 0.0)
        state.total_trades = data.get("total_trades", 0)
        state.total_fees = data.get("total_fees", 0.0)
        state.grid_created = data.get("grid_created", False)
        for lv_data in data.get("levels", []):
            state.levels.append(GridLevel(
                index=lv_data["index"],
                price=lv_data["price"],
                side=GridOrderSide(lv_data["side"]),
                order_id=lv_data.get("order_id"),
                is_filled=lv_data.get("is_filled", False),
                is_active=lv_data.get("is_active", False),
            ))
        return state


class FuturesGridStrategy:
    """
    Contract Grid Trading Strategy.

    Parameters:
        upper_price: Top of the grid range
        lower_price: Bottom of the grid range
        grid_count:  Number of grid lines (creates grid_count-1 intervals)
        size_per_grid: Contract size per grid order
        mode: LONG / SHORT / NEUTRAL
        leverage: Futures leverage multiplier
    """

    STATE_FILE = "grid_state.json"

    def __init__(
        self,
        upper_price: float,
        lower_price: float,
        grid_count: int,
        size_per_grid: float,
        mode: GridMode = GridMode.NEUTRAL,
        leverage: int = 10,
        take_profit_pct: float = 0.0,   # 0 = disabled, use grid spacing
        stop_loss_pct: float = 5.0,     # Emergency stop loss %
        trailing_up: bool = False,       # Auto-extend grid upward
        trailing_down: bool = False,     # Auto-extend grid downward
    ):
        if upper_price <= lower_price:
            raise ValueError("upper_price must be > lower_price")
        if grid_count < 3:
            raise ValueError("grid_count must be >= 3")

        self.upper_price = upper_price
        self.lower_price = lower_price
        self.grid_count = grid_count
        self.size_per_grid = size_per_grid
        self.mode = mode
        self.leverage = leverage
        self.take_profit_pct = take_profit_pct
        self.stop_loss_pct = stop_loss_pct
        self.trailing_up = trailing_up
        self.trailing_down = trailing_down

        # Derived
        self.grid_spacing = (upper_price - lower_price) / (grid_count - 1)
        self.profit_per_grid = self.grid_spacing * size_per_grid

        # State
        self.state = GridState()

        # Try to load saved state
        self._load_state()

        log.info("Grid Strategy initialized:")
        log.info("  Range: %.2f - %.2f", lower_price, upper_price)
        log.info("  Grids: %d (spacing: %.2f)", grid_count, self.grid_spacing)
        log.info("  Size/grid: %.6f", size_per_grid)
        log.info("  Mode: %s | Leverage: %dx", mode.value, leverage)
        log.info("  Est. profit/grid: $%.4f", self.profit_per_grid)

    # ── Grid Creation ─────────────────────────────────────────────

    def create_grid_levels(self, current_price: float) -> list[GridLevel]:
        """Generate grid levels based on current price and mode."""
        levels = []

        for i in range(self.grid_count):
            price = self.lower_price + i * self.grid_spacing
            price = round(price, 2)

            if self.mode == GridMode.NEUTRAL:
                # Below current price → BUY, above → SELL
                side = GridOrderSide.BUY if price < current_price else GridOrderSide.SELL
            elif self.mode == GridMode.LONG:
                side = GridOrderSide.BUY
            else:  # SHORT
                side = GridOrderSide.SELL

            levels.append(GridLevel(index=i, price=price, side=side))

        self.state.levels = levels
        self.state.grid_created = True
        self._save_state()

        log.info("Created %d grid levels around price %.2f", len(levels), current_price)
        buy_count = sum(1 for lv in levels if lv.side == GridOrderSide.BUY)
        sell_count = sum(1 for lv in levels if lv.side == GridOrderSide.SELL)
        log.info("  BUY levels: %d | SELL levels: %d", buy_count, sell_count)

        return levels

    # ── Order Management ──────────────────────────────────────────

    def get_orders_to_place(self, current_price: float) -> list[GridLevel]:
        """Return grid levels that need orders placed."""
        to_place = []
        for lv in self.state.levels:
            if lv.is_active or lv.is_filled:
                continue

            # Skip levels too close to current price (within 0.1 * spacing)
            if abs(lv.price - current_price) < self.grid_spacing * 0.1:
                continue

            # For BUY orders, price must be below current
            if lv.side == GridOrderSide.BUY and lv.price < current_price:
                to_place.append(lv)
            # For SELL orders, price must be above current
            elif lv.side == GridOrderSide.SELL and lv.price > current_price:
                to_place.append(lv)

        return to_place

    def mark_order_placed(self, level_index: int, order_id: str):
        """Mark a grid level as having an active order."""
        for lv in self.state.levels:
            if lv.index == level_index:
                lv.order_id = order_id
                lv.is_active = True
                log.info("Grid[%d] order placed: %s @ %.2f (%s)",
                         lv.index, lv.side.value, lv.price, order_id)
                break
        self._save_state()

    def process_fill(self, level_index: int, fill_price: float) -> Optional[GridLevel]:
        """
        Process a filled order. Returns the counter-order level to place.

        When BUY fills → create SELL at next grid level up
        When SELL fills → create BUY at next grid level down
        """
        filled_level = None
        for lv in self.state.levels:
            if lv.index == level_index:
                filled_level = lv
                break

        if not filled_level:
            return None

        filled_level.is_filled = True
        filled_level.is_active = False
        filled_level.order_id = None

        # Calculate profit from this grid trade
        profit = self.grid_spacing * self.size_per_grid
        self.state.total_profit += profit
        self.state.total_trades += 1

        log.info("Grid[%d] FILLED: %s @ %.2f | Grid profit: $%.4f | Total: $%.4f",
                 filled_level.index, filled_level.side.value, fill_price,
                 profit, self.state.total_profit)

        # Create counter order
        if filled_level.side == GridOrderSide.BUY:
            # BUY filled → place SELL one level up
            counter_index = filled_level.index + 1
            counter_side = GridOrderSide.SELL
        else:
            # SELL filled → place BUY one level down
            counter_index = filled_level.index - 1
            counter_side = GridOrderSide.BUY

        # Find or create counter level
        counter_level = None
        for lv in self.state.levels:
            if lv.index == counter_index:
                counter_level = lv
                break

        if counter_level and not counter_level.is_active:
            counter_level.side = counter_side
            counter_level.is_filled = False
            counter_level.order_id = None
            self._save_state()
            return counter_level

        # Reset filled level for re-entry
        filled_level.is_filled = False
        self._save_state()
        return None

    # ── Risk Checks ───────────────────────────────────────────────

    def check_stop_loss(self, current_price: float) -> bool:
        """Check if price has moved outside grid range by stop_loss_pct."""
        if self.stop_loss_pct <= 0:
            return False

        upper_stop = self.upper_price * (1 + self.stop_loss_pct / 100)
        lower_stop = self.lower_price * (1 - self.stop_loss_pct / 100)

        if current_price > upper_stop:
            log.warning("STOP LOSS TRIGGERED: Price %.2f > upper stop %.2f", current_price, upper_stop)
            return True
        if current_price < lower_stop:
            log.warning("STOP LOSS TRIGGERED: Price %.2f < lower stop %.2f", current_price, lower_stop)
            return True
        return False

    def check_out_of_range(self, current_price: float) -> str:
        """Check if current price is outside grid range."""
        if current_price > self.upper_price:
            return "ABOVE"
        if current_price < self.lower_price:
            return "BELOW"
        return "IN_RANGE"

    # ── Statistics ────────────────────────────────────────────────

    def get_stats(self) -> dict:
        active_orders = sum(1 for lv in self.state.levels if lv.is_active)
        filled_orders = sum(1 for lv in self.state.levels if lv.is_filled)
        buy_active = sum(1 for lv in self.state.levels if lv.is_active and lv.side == GridOrderSide.BUY)
        sell_active = sum(1 for lv in self.state.levels if lv.is_active and lv.side == GridOrderSide.SELL)

        investment = self.size_per_grid * self.grid_count * self.lower_price / self.leverage
        roi = (self.state.total_profit / investment * 100) if investment > 0 else 0

        return {
            "grid_range": f"{self.lower_price:.2f} - {self.upper_price:.2f}",
            "grid_count": self.grid_count,
            "grid_spacing": round(self.grid_spacing, 2),
            "active_orders": active_orders,
            "filled_orders": filled_orders,
            "buy_active": buy_active,
            "sell_active": sell_active,
            "total_trades": self.state.total_trades,
            "total_profit": round(self.state.total_profit, 4),
            "total_fees": round(self.state.total_fees, 4),
            "net_profit": round(self.state.total_profit - self.state.total_fees, 4),
            "est_roi_pct": round(roi, 2),
            "mode": self.mode.value,
            "leverage": self.leverage,
        }

    # ── Persistence ───────────────────────────────────────────────

    def _save_state(self):
        try:
            with open(self.STATE_FILE, "w") as f:
                json.dump(self.state.to_dict(), f, indent=2)
        except Exception as e:
            log.error("Failed to save grid state: %s", e)

    def _load_state(self):
        if os.path.exists(self.STATE_FILE):
            try:
                with open(self.STATE_FILE, "r") as f:
                    data = json.load(f)
                self.state = GridState.from_dict(data)
                log.info("Loaded grid state: %d levels, %d trades, profit=$%.4f",
                         len(self.state.levels), self.state.total_trades, self.state.total_profit)
            except Exception as e:
                log.warning("Failed to load grid state: %s", e)

    def reset_state(self):
        """Clear all grid state and start fresh."""
        self.state = GridState()
        if os.path.exists(self.STATE_FILE):
            os.remove(self.STATE_FILE)
        log.info("Grid state reset")
