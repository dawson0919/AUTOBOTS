"""
BLADE GOD Triple MA — 3 Kingdoms Strategy
==========================================
Liu Bei(240MA) direction → Guan Yu(60MA) attack → Zhang Fei(20MA) close

Signal Types:
  - MAIN_LONG:    Main trend long (Liu Bei + Guan Yu + Zhang Fei all aligned up)
  - MAIN_SHORT:   Main trend short (all aligned down)
  - BOUNCE_LONG:  Bear regime bounce long (Liu Bei bearish, but Guan Yu finds opportunity)
  - BOUNCE_SHORT: Bull regime correction short (Liu Bei bullish, but needs adjustment)
  - ZHANGFEI_EXIT_LONG:  Zhang Fei retreat (negative slope → close long)
  - ZHANGFEI_EXIT_SHORT: Zhang Fei retreat (positive slope → close short)
  - HOLD:         No clear signal
"""
from __future__ import annotations

from enum import Enum

import numpy as np

from logger import setup_logger

log = setup_logger("three_kingdoms")


class ThreeKingdomsSignal(Enum):
    """3 Kingdoms strategy signal."""
    MAIN_LONG = "MAIN_LONG"             # Full army attack (long)
    MAIN_SHORT = "MAIN_SHORT"           # Full army retreat (short)
    BOUNCE_LONG = "BOUNCE_LONG"         # Bear bounce (short-term long)
    CORRECTION_SHORT = "CORRECTION_SHORT"  # Bull correction (short-term short)
    ZHANGFEI_CLOSE_LONG = "ZF_CLOSE_LONG"    # Zhang Fei retreat: close long
    ZHANGFEI_CLOSE_SHORT = "ZF_CLOSE_SHORT"  # Zhang Fei retreat: close short
    HOLD = "HOLD"

    @property
    def is_open_long(self) -> bool:
        return self in (ThreeKingdomsSignal.MAIN_LONG, ThreeKingdomsSignal.BOUNCE_LONG)

    @property
    def is_open_short(self) -> bool:
        return self in (ThreeKingdomsSignal.MAIN_SHORT, ThreeKingdomsSignal.CORRECTION_SHORT)

    @property
    def is_close_long(self) -> bool:
        return self in (ThreeKingdomsSignal.ZHANGFEI_CLOSE_LONG, ThreeKingdomsSignal.MAIN_SHORT, ThreeKingdomsSignal.CORRECTION_SHORT)

    @property
    def is_close_short(self) -> bool:
        return self in (ThreeKingdomsSignal.ZHANGFEI_CLOSE_SHORT, ThreeKingdomsSignal.MAIN_LONG, ThreeKingdomsSignal.BOUNCE_LONG)

    @property
    def trend(self) -> str:
        if self.is_open_long:
            return "long"
        if self.is_open_short:
            return "short"
        return "no_trend"

    @property
    def strength(self) -> int:
        """3=main trend, 2=bounce/correction, 1=Zhang Fei close, 0=hold."""
        mapping = {
            ThreeKingdomsSignal.MAIN_LONG: 3,
            ThreeKingdomsSignal.MAIN_SHORT: 3,
            ThreeKingdomsSignal.BOUNCE_LONG: 2,
            ThreeKingdomsSignal.CORRECTION_SHORT: 2,
            ThreeKingdomsSignal.ZHANGFEI_CLOSE_LONG: 1,
            ThreeKingdomsSignal.ZHANGFEI_CLOSE_SHORT: 1,
            ThreeKingdomsSignal.HOLD: 0,
        }
        return mapping[self]

    @property
    def label(self) -> str:
        labels = {
            ThreeKingdomsSignal.MAIN_LONG: "Full Army Long",
            ThreeKingdomsSignal.MAIN_SHORT: "Full Army Short",
            ThreeKingdomsSignal.BOUNCE_LONG: "Bounce Long",
            ThreeKingdomsSignal.CORRECTION_SHORT: "Correction Short",
            ThreeKingdomsSignal.ZHANGFEI_CLOSE_LONG: "ZhangFei Close Long",
            ThreeKingdomsSignal.ZHANGFEI_CLOSE_SHORT: "ZhangFei Close Short",
            ThreeKingdomsSignal.HOLD: "Hold",
        }
        return labels[self]


class ThreeKingdomsStrategy:
    """
    BLADE GOD Triple MA — 3 Kingdoms Strategy

    Liu Bei  (liu_bei)   = MA(240) — Direction (bull/bear watershed)
    Guan Yu  (guan_yu)   = MA(60)  — Attack (entry/exit rhythm)
    Zhang Fei (zhang_fei) = MA(20)  — Close (take-profit/risk control)

    Advanced:
    - Bear bounce: price < 240MA, but price > 60MA → short-term long
    - Bull correction: price > 240MA, but price < 60MA → short-term short
    - Zhang Fei slope: positive → close short, negative → close long
    """

    def __init__(
        self,
        liu_bei: int = 240,
        guan_yu: int = 60,
        zhang_fei: int = 20,
        slope_bars: int = 3,      # bars for Zhang Fei slope calculation
    ):
        self.liu_bei_period = liu_bei
        self.guan_yu_period = guan_yu
        self.zhang_fei_period = zhang_fei
        self.slope_bars = slope_bars
        self._prev_signal = ThreeKingdomsSignal.HOLD

    @staticmethod
    def sma(closes: list[float], period: int) -> float:
        if len(closes) < period:
            return 0.0
        return float(np.mean(closes[-period:]))

    @staticmethod
    def sma_series(closes: list[float], period: int, lookback: int) -> list[float]:
        """Get last N SMA values for slope calculation."""
        result = []
        for i in range(lookback):
            end = len(closes) - i
            if end < period:
                break
            result.append(float(np.mean(closes[end - period:end])))
        result.reverse()
        return result

    def _zhang_fei_slope(self, closes: list[float]) -> float:
        """Zhang Fei(20MA) slope: positive=up, negative=down."""
        vals = self.sma_series(closes, self.zhang_fei_period, self.slope_bars + 1)
        if len(vals) < 2:
            return 0.0
        # Normalized slope (per bar, as % of price)
        slope = (vals[-1] - vals[0]) / self.slope_bars
        price = closes[-1] if closes else 1.0
        return slope / price * 100  # percentage per bar

    def evaluate(self, klines: list[dict]) -> ThreeKingdomsSignal:
        """Evaluate 3 Kingdoms strategy signal."""
        closes = [float(k["close"]) for k in klines]

        if len(closes) < self.liu_bei_period + 2:
            return ThreeKingdomsSignal.HOLD

        price = closes[-1]
        liu_bei = self.sma(closes, self.liu_bei_period)   # 240MA
        guan_yu = self.sma(closes, self.guan_yu_period)   # 60MA
        zhang_fei = self.sma(closes, self.zhang_fei_period)  # 20MA
        zf_slope = self._zhang_fei_slope(closes)

        # ── Liu Bei: determine direction ──
        is_bull_regime = price > liu_bei   # above 240MA = bull
        is_bear_regime = price < liu_bei   # below 240MA = bear

        # ── Guan Yu: entry/exit ──
        price_above_gy = price > guan_yu   # above 60MA
        price_below_gy = price < guan_yu   # below 60MA

        # ── Zhang Fei: close signal ──
        zf_positive = zf_slope > 0.01     # positive slope
        zf_negative = zf_slope < -0.01    # negative slope

        signal = ThreeKingdomsSignal.HOLD

        # ═══ Main trend signal (Liu Bei + Guan Yu + Zhang Fei aligned) ═══
        if is_bull_regime and price_above_gy and price > zhang_fei:
            signal = ThreeKingdomsSignal.MAIN_LONG     # Full army long

        elif is_bear_regime and price_below_gy and price < zhang_fei:
            signal = ThreeKingdomsSignal.MAIN_SHORT    # Full army short

        # ═══ Advanced: Bounce & Correction ═══
        elif is_bear_regime and price_above_gy:
            # Bear bounce: Liu Bei says retreat (bear), but Guan Yu finds opportunity
            signal = ThreeKingdomsSignal.BOUNCE_LONG   # Short-term long

        elif is_bull_regime and price_below_gy:
            # Bull correction: Liu Bei says attack (bull), but needs adjustment
            signal = ThreeKingdomsSignal.CORRECTION_SHORT  # Short-term short

        # ═══ Zhang Fei retreat — DISABLED (too sensitive, causes over-trading) ═══
        # if zf_positive and self._prev_signal in (ThreeKingdomsSignal.MAIN_SHORT, ThreeKingdomsSignal.CORRECTION_SHORT):
        #     signal = ThreeKingdomsSignal.ZHANGFEI_CLOSE_SHORT
        # if zf_negative and self._prev_signal in (ThreeKingdomsSignal.MAIN_LONG, ThreeKingdomsSignal.BOUNCE_LONG):
        #     signal = ThreeKingdomsSignal.ZHANGFEI_CLOSE_LONG

        # Log on change
        if signal != self._prev_signal:
            regime = "BULL" if is_bull_regime else "BEAR"
            log.info(
                "3K SIGNAL: %s → %s [%s] | LiuBei(240)=%.2f GuanYu(60)=%.2f ZhangFei(20)=%.2f slope=%.3f | price=%.2f",
                self._prev_signal.label, signal.label, regime,
                liu_bei, guan_yu, zhang_fei, zf_slope, price,
            )
            self._prev_signal = signal
        else:
            log.debug(
                "LiuBei=%.2f GuanYu=%.2f ZhangFei=%.2f slope=%.3f → %s",
                liu_bei, guan_yu, zhang_fei, zf_slope, signal.label,
            )

        return signal

    def reset(self):
        self._prev_signal = ThreeKingdomsSignal.HOLD
