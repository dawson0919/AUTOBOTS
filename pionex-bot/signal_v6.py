"""
三刀流 v6 Signal Engine — Extracted from backtest for production use.
Pure functions + SignalState class for flip-only detection.
"""
from __future__ import annotations

import numpy as np

# Signal codes: matches PineScript v6 exactly
SIG_HOLD = 0
SIG_MAIN_LONG = 1
SIG_MAIN_SHORT = 2
SIG_CORR_SHORT = 3
SIG_BOUNCE_LONG = 4

SIG_NAMES = {0: "HOLD", 1: "MAIN_LONG", 2: "MAIN_SHORT", 3: "CORR_SHORT", 4: "BOUNCE_LONG"}
SIG_DIR = {0: 0, 1: 1, 2: -1, 3: -1, 4: 1}  # 1=long, -1=short, 0=hold


def sma(values: list[float] | np.ndarray, period: int) -> float:
    """Simple moving average of the last `period` values."""
    if len(values) < period:
        return 0.0
    return float(np.mean(values[-period:]))


def calc_raw_signal(
    price: float,
    lb: float,
    gy: float,
    zf: float,
    dist_pct: float = 2.0,
    disable_bounce: bool = True,
) -> int:
    """Calculate raw signal for current bar — matches PineScript v6 exactly.

    Args:
        price: current close price
        lb: Liu Bei SMA value (slow/direction)
        gy: Guan Yu SMA value (mid/attack)
        zf: Zhang Fei SMA value (fast/confirm)
        dist_pct: minimum distance from GY (%), 0 = disabled
        disable_bounce: if True, BOUNCE_LONG signal is suppressed
    """
    is_bull = price > lb
    is_bear = price < lb

    gy_dist = abs(price - gy) / gy * 100 if gy > 0 else 0
    far_enough = dist_pct == 0 or gy_dist >= dist_pct

    above_gy = price > gy and far_enough
    below_gy = price < gy and far_enough

    if is_bull and above_gy and price > zf:
        return SIG_MAIN_LONG
    elif is_bear and below_gy and price < zf:
        return SIG_MAIN_SHORT
    elif not disable_bounce and is_bear and above_gy:
        return SIG_BOUNCE_LONG
    elif is_bull and below_gy:
        return SIG_CORR_SHORT
    return SIG_HOLD


class SignalState:
    """Tracks signal state for flip-only detection.

    The key insight: we only act when direction CHANGES (long→short or short→long).
    This prevents oscillation and matches the backtest state machine exactly.
    """

    def __init__(self, sig_state: int = 0):
        self.sig_state = sig_state  # last non-HOLD raw signal code

    def update(self, raw_signal: int) -> tuple[bool, int]:
        """Process a new raw signal and detect direction change.

        Returns:
            (direction_changed, new_direction)
            direction_changed: True if signal flipped long↔short
            new_direction: 1=long, -1=short, 0=hold
        """
        raw_dir = SIG_DIR.get(raw_signal, 0)

        prev_dir = SIG_DIR.get(self.sig_state, 0)

        direction_changed = raw_dir != 0 and raw_dir != prev_dir

        # Update state if we got a non-HOLD signal
        if raw_signal != SIG_HOLD:
            self.sig_state = raw_signal

        return direction_changed, raw_dir

    @property
    def current_direction(self) -> int:
        return SIG_DIR.get(self.sig_state, 0)

    @property
    def current_signal_name(self) -> str:
        return SIG_NAMES.get(self.sig_state, "UNKNOWN")


def replay_signal_state(
    closes: list[float],
    lb_period: int,
    gy_period: int,
    zf_period: int,
    dist_pct: float = 2.0,
    disable_bounce: bool = True,
) -> SignalState:
    """Replay all candles to establish current signal state. Optimized with cumsum SMA."""
    arr = np.array(closes, dtype=np.float64)
    n = len(arr)
    state = SignalState()
    start = max(lb_period, gy_period, zf_period)

    if n < start + 1:
        return state

    # Pre-compute all SMA arrays using cumsum (O(n) instead of O(n*period))
    def sma_array(period: int) -> np.ndarray:
        if n < period:
            return np.full(n, np.nan)
        cumsum = np.cumsum(arr)
        cumsum = np.insert(cumsum, 0, 0)
        result = np.full(n, np.nan)
        result[period - 1 :] = (cumsum[period:] - cumsum[:-period]) / period
        return result

    lb_arr = sma_array(lb_period)
    gy_arr = sma_array(gy_period)
    zf_arr = sma_array(zf_period)

    for i in range(start, n):
        lb_val = lb_arr[i]
        gy_val = gy_arr[i]
        zf_val = zf_arr[i]
        if np.isnan(lb_val) or np.isnan(gy_val) or np.isnan(zf_val):
            continue
        raw = calc_raw_signal(closes[i], float(lb_val), float(gy_val), float(zf_val), dist_pct, disable_bounce)
        state.update(raw)

    return state
