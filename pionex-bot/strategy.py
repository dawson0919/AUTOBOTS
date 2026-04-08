"""MA Cross strategy: generates BUY/SELL signals based on fast/slow moving average crossovers."""
from __future__ import annotations

from enum import Enum

import numpy as np

from config import Config
from logger import setup_logger

log = setup_logger("strategy")


class Signal(Enum):
    HOLD = "HOLD"
    BUY = "BUY"      # Golden cross → go long
    SELL = "SELL"     # Death cross → go short / close long


class MACrossStrategy:
    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or Config()
        self.fast_period = self.cfg.FAST_MA_PERIOD
        self.slow_period = self.cfg.SLOW_MA_PERIOD
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None

    def compute_ma(self, closes: list[float], period: int) -> float:
        """Simple Moving Average of the last `period` closes."""
        if len(closes) < period:
            return 0.0
        return float(np.mean(closes[-period:]))

    def evaluate(self, klines: list[dict]) -> Signal:
        """Evaluate kline data and return a trading signal.

        Golden cross (fast crosses above slow) → BUY
        Death cross (fast crosses below slow)  → SELL
        Otherwise                              → HOLD
        """
        closes = [float(k["close"]) for k in klines]

        if len(closes) < self.slow_period + 1:
            log.debug("Not enough data: %d candles, need %d", len(closes), self.slow_period + 1)
            return Signal.HOLD

        fast_ma = self.compute_ma(closes, self.fast_period)
        slow_ma = self.compute_ma(closes, self.slow_period)

        signal = Signal.HOLD

        if self._prev_fast is not None and self._prev_slow is not None:
            # Golden cross: fast was below slow, now above
            if self._prev_fast <= self._prev_slow and fast_ma > slow_ma:
                signal = Signal.BUY
                log.info(
                    "GOLDEN CROSS ↑ fast=%.2f slow=%.2f (prev fast=%.2f slow=%.2f)",
                    fast_ma, slow_ma, self._prev_fast, self._prev_slow,
                )

            # Death cross: fast was above slow, now below
            elif self._prev_fast >= self._prev_slow and fast_ma < slow_ma:
                signal = Signal.SELL
                log.info(
                    "DEATH CROSS ↓ fast=%.2f slow=%.2f (prev fast=%.2f slow=%.2f)",
                    fast_ma, slow_ma, self._prev_fast, self._prev_slow,
                )

        self._prev_fast = fast_ma
        self._prev_slow = slow_ma

        log.debug("MA fast=%.2f slow=%.2f → %s", fast_ma, slow_ma, signal.value)
        return signal

    def reset(self):
        self._prev_fast = None
        self._prev_slow = None


# ═══════════════════════════════════════════════════════════════
# 三刀流 Triple MA Strategy (MA7 / MA25 / MA99)
# ═══════════════════════════════════════════════════════════════


class BladeSignal(Enum):
    """Signal with strength from Triple MA (三刀流)."""
    STRONG_LONG = "STRONG_LONG"    # All 3 blades aligned bullish: MA7 > MA25 > MA99
    MEDIUM_LONG = "MEDIUM_LONG"    # 2 blades bullish: MA7 > MA25, MA25 < MA99
    WEAK_LONG = "WEAK_LONG"        # 1 blade bullish: MA7 > MA25 only
    HOLD = "HOLD"                  # Tangled / no clear signal
    WEAK_SHORT = "WEAK_SHORT"      # 1 blade bearish: MA7 < MA25 only
    MEDIUM_SHORT = "MEDIUM_SHORT"  # 2 blades bearish: MA7 < MA25, MA25 > MA99
    STRONG_SHORT = "STRONG_SHORT"  # All 3 blades aligned bearish: MA7 < MA25 < MA99

    @property
    def is_long(self) -> bool:
        return self in (BladeSignal.STRONG_LONG, BladeSignal.MEDIUM_LONG, BladeSignal.WEAK_LONG)

    @property
    def is_short(self) -> bool:
        return self in (BladeSignal.STRONG_SHORT, BladeSignal.MEDIUM_SHORT, BladeSignal.WEAK_SHORT)

    @property
    def strength(self) -> int:
        """Return signal strength: 3=strong, 2=medium, 1=weak, 0=hold."""
        mapping = {
            BladeSignal.STRONG_LONG: 3, BladeSignal.STRONG_SHORT: 3,
            BladeSignal.MEDIUM_LONG: 2, BladeSignal.MEDIUM_SHORT: 2,
            BladeSignal.WEAK_LONG: 1, BladeSignal.WEAK_SHORT: 1,
            BladeSignal.HOLD: 0,
        }
        return mapping[self]

    @property
    def trend(self) -> str:
        """Return 'long', 'short', or 'no_trend' for grid bot API."""
        if self.is_long:
            return "long"
        elif self.is_short:
            return "short"
        return "no_trend"


class TripleMAStrategy:
    """三刀流 (Triple Blade) strategy using MA(7), MA(25), MA(99).

    Signal strength is determined by how many MA "blades" are aligned:
    - 3 blades: MA7 > MA25 > MA99 (strong long) or MA7 < MA25 < MA99 (strong short)
    - 2 blades: Two of three aligned
    - 1 blade:  Only fast/mid cross
    - 0:        Tangled (HOLD)
    """

    def __init__(self, fast: int = 7, mid: int = 25, slow: int = 99):
        self.fast_period = fast
        self.mid_period = mid
        self.slow_period = slow
        self._prev_signal: BladeSignal = BladeSignal.HOLD

    @staticmethod
    def sma(closes: list[float], period: int) -> float:
        if len(closes) < period:
            return 0.0
        return float(np.mean(closes[-period:]))

    def evaluate(self, klines: list[dict]) -> BladeSignal:
        """Evaluate klines and return a BladeSignal with strength."""
        closes = [float(k["close"]) for k in klines]

        if len(closes) < self.slow_period + 1:
            log.debug("Not enough data: %d candles, need %d", len(closes), self.slow_period + 1)
            return BladeSignal.HOLD

        ma_fast = self.sma(closes, self.fast_period)
        ma_mid = self.sma(closes, self.mid_period)
        ma_slow = self.sma(closes, self.slow_period)

        # Count bullish/bearish blade alignments
        fast_above_mid = ma_fast > ma_mid   # Blade 1: fast vs mid
        mid_above_slow = ma_mid > ma_slow   # Blade 2: mid vs slow
        fast_above_slow = ma_fast > ma_slow # Blade 3: fast vs slow

        bull_count = sum([fast_above_mid, mid_above_slow, fast_above_slow])
        bear_count = 3 - bull_count

        if bull_count == 3:
            signal = BladeSignal.STRONG_LONG
        elif bull_count == 2 and fast_above_mid:
            signal = BladeSignal.MEDIUM_LONG
        elif fast_above_mid and bear_count >= 2:
            signal = BladeSignal.WEAK_LONG
        elif bear_count == 3:
            signal = BladeSignal.STRONG_SHORT
        elif bear_count == 2 and not fast_above_mid:
            signal = BladeSignal.MEDIUM_SHORT
        elif not fast_above_mid and bull_count >= 2:
            signal = BladeSignal.WEAK_SHORT
        else:
            signal = BladeSignal.HOLD

        # Log on signal change
        if signal != self._prev_signal:
            emoji = "🔥" if signal.strength == 3 else "⚡" if signal.strength == 2 else "〰️"
            log.info(
                "%s SIGNAL CHANGE: %s → %s  |  MA7=%.2f  MA25=%.2f  MA99=%.2f  |  price=%.2f",
                emoji, self._prev_signal.value, signal.value,
                ma_fast, ma_mid, ma_slow, closes[-1],
            )
            self._prev_signal = signal
        else:
            log.debug(
                "MA7=%.2f  MA25=%.2f  MA99=%.2f → %s (strength=%d)",
                ma_fast, ma_mid, ma_slow, signal.value, signal.strength,
            )

        return signal

    def reset(self):
        self._prev_signal = BladeSignal.HOLD
