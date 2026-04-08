"""Risk management: position sizing, stop-loss, take-profit, and daily loss tracking."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from config import Config
from logger import setup_logger

log = setup_logger("risk")


@dataclass
class Position:
    side: str = ""          # "BUY" (long) or "SELL" (short)
    size: float = 0.0       # position size in base currency
    entry_price: float = 0.0
    order_id: str = ""
    timestamp: float = 0.0

    @property
    def is_open(self) -> bool:
        return self.size > 0 and self.side != ""

    def unrealized_pnl(self, current_price: float) -> float:
        if not self.is_open:
            return 0.0
        if self.side == "BUY":
            return (current_price - self.entry_price) / self.entry_price * 100
        else:
            return (self.entry_price - current_price) / self.entry_price * 100

    def clear(self):
        self.side = ""
        self.size = 0.0
        self.entry_price = 0.0
        self.order_id = ""
        self.timestamp = 0.0


@dataclass
class RiskManager:
    cfg: Config = field(default_factory=Config)
    position: Position = field(default_factory=Position)
    daily_pnl_pct: float = 0.0
    _day_start: float = field(default_factory=lambda: time.time())
    _trade_count: int = 0

    def _reset_daily_if_needed(self):
        elapsed = time.time() - self._day_start
        if elapsed > 86400:  # 24 hours
            log.info("Daily reset: PnL was %.2f%%, trades: %d", self.daily_pnl_pct, self._trade_count)
            self.daily_pnl_pct = 0.0
            self._trade_count = 0
            self._day_start = time.time()

    def can_open_position(self) -> bool:
        self._reset_daily_if_needed()

        if self.position.is_open:
            log.debug("Position already open, cannot open another")
            return False

        if self.daily_pnl_pct <= -self.cfg.MAX_DAILY_LOSS_PCT:
            log.warning(
                "Daily loss limit reached: %.2f%% (max: %.2f%%)",
                self.daily_pnl_pct, self.cfg.MAX_DAILY_LOSS_PCT,
            )
            return False

        return True

    def calculate_size(self, current_price: float) -> float:
        """Return position size capped at MAX_POSITION_SIZE."""
        return self.cfg.MAX_POSITION_SIZE

    def check_stop_loss(self, current_price: float) -> bool:
        if not self.position.is_open:
            return False
        pnl = self.position.unrealized_pnl(current_price)
        if pnl <= -self.cfg.STOP_LOSS_PCT:
            log.warning("STOP LOSS triggered: PnL=%.2f%% (threshold: -%.2f%%)", pnl, self.cfg.STOP_LOSS_PCT)
            return True
        return False

    def check_take_profit(self, current_price: float) -> bool:
        if not self.position.is_open:
            return False
        pnl = self.position.unrealized_pnl(current_price)
        if pnl >= self.cfg.TAKE_PROFIT_PCT:
            log.info("TAKE PROFIT triggered: PnL=%.2f%% (threshold: +%.2f%%)", pnl, self.cfg.TAKE_PROFIT_PCT)
            return True
        return False

    def should_close(self, current_price: float) -> bool:
        return self.check_stop_loss(current_price) or self.check_take_profit(current_price)

    def open_position(self, side: str, size: float, price: float, order_id: str = ""):
        self.position = Position(
            side=side,
            size=size,
            entry_price=price,
            order_id=order_id,
            timestamp=time.time(),
        )
        self._trade_count += 1
        log.info("Position opened: %s %.6f @ %.2f [%s]", side, size, price, order_id)

    def close_position(self, close_price: float) -> float:
        pnl = self.position.unrealized_pnl(close_price)
        self.daily_pnl_pct += pnl
        log.info(
            "Position closed: %s %.6f @ %.2f → PnL=%.2f%% (daily: %.2f%%)",
            self.position.side, self.position.size, close_price, pnl, self.daily_pnl_pct,
        )
        self.position.clear()
        return pnl
